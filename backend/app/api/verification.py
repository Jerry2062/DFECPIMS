"""
api/verification.py

Hash verification route handlers for DFECPIMS.

Endpoints:
  POST /cases/{case_id}/evidence/{evidence_id}/verify  — verify a single item
  POST /cases/{case_id}/verify-all                     — verify all items in a case

Both are POST, not GET, because they are not read-only operations. Each call:
  - Re-reads the file from disk
  - Computes a fresh SHA-256 hash
  - Updates last_verified_at and integrity_status on the Evidence record
  - Writes audit log entries

Using GET for state-mutating operations would violate HTTP semantics
and could cause unintended verification runs from browser prefetch
or caching proxies. POST is correct here.

Access control:
  - Both endpoints require investigator or supervisor role.
  - Readonly users can view verification results via GET /evidence/{id}
    (the integrity_status and last_verified_at fields) but cannot
    trigger a new verification run.

Timing note:
  Verification on large files (multi-GB disk images) may take several seconds
  since the entire file is read from disk. This is expected behavior. If you
  need async job queuing for very large files, that's a future enhancement —
  for typical forensic evidence sizes up to 500MB, synchronous is fine.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import require_investigator
from app.core.security import TokenPayload
from app.schemas.verification import BulkVerificationSummary, VerificationResult
from app.services.case_service import CaseService
from app.services.evidence_service import EvidenceService
from app.services.verification_service import VerificationService


router = APIRouter(tags=["Hash Verification"])


# ─── POST /cases/{case_id}/evidence/{evidence_id}/verify ─────────────────────

@router.post(
    "/cases/{case_id}/evidence/{evidence_id}/verify",
    response_model=VerificationResult,
    summary="Re-hash an evidence file and verify its integrity",
    status_code=status.HTTP_200_OK,
)
async def verify_evidence_hash(
    case_id: str,
    evidence_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(require_investigator),
) -> VerificationResult:
    """
    Re-compute the SHA-256 hash of a stored evidence file and compare it
    against the hash recorded at ingestion time.

    The file is read from disk in 64KB chunks — no cached or in-memory copy
    is used. This guarantees the verification reflects the actual bytes
    currently stored on the server filesystem.

    Three possible outcomes:
      VERIFIED      — Hash matches. Evidence is unmodified since ingestion.
      HASH_MISMATCH — Hash does not match. Evidence may have been tampered with.
                      Treat this as a critical integrity failure.
      FILE_MISSING  — The file no longer exists at its registered storage path.

    The Evidence record is updated:
      - last_verified_at is set to the current UTC timestamp
      - integrity_status is set to 'verified' or 'failed'

    An audit log entry is written for every run, regardless of outcome.
    This means verification history is fully traceable in the audit trail.

    Returns the full result including both the stored and computed hashes,
    making the comparison explicit and documentable.
    """
    # Verify the case exists
    case_service = CaseService(db)
    case = await case_service.get_by_id(case_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case '{case_id}' not found.",
        )

    # Fetch the evidence item
    ev_service = EvidenceService(db)
    evidence = await ev_service.get_by_id(evidence_id)
    if evidence is None or evidence.case_id != case_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Evidence '{evidence_id}' not found in case '{case_id}'.",
        )

    # Run verification — updates the ORM object in place, writes audit log
    verification_service = VerificationService(db)
    result = await verification_service.verify_evidence(
        evidence=evidence,
        actor_id=current_user.user_id,
    )

    await db.commit()

    return result


# ─── POST /cases/{case_id}/verify-all ────────────────────────────────────────

@router.post(
    "/cases/{case_id}/verify-all",
    response_model=BulkVerificationSummary,
    summary="Verify integrity of all evidence items in a case",
    status_code=status.HTTP_200_OK,
)
async def verify_all_evidence(
    case_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(require_investigator),
) -> BulkVerificationSummary:
    """
    Run SHA-256 hash verification on every evidence item attached to a case.

    Processes evidence items in acquisition order. A failure on one item
    does not stop verification of the others — the full case is always
    checked and every result is returned.

    A single BULK_VERIFICATION_RUN audit entry is written summarising the
    overall outcome, in addition to individual HASH_VERIFIED / HASH_FAILED /
    EVIDENCE_FILE_MISSING entries per item.

    Returns a summary with:
      - Per-item VerificationResult list
      - Aggregate counts (verified / mismatch / missing)
      - all_passed boolean — True only if every item passed

    Recommended to call this before generating a PDF chain-of-custody report,
    so the report reflects a freshly verified integrity state.

    Note: On a case with many large evidence files this may take some time.
    Each file is fully read from disk — this cannot be short-circuited.
    """
    # Verify the case exists
    case_service = CaseService(db)
    case = await case_service.get_by_id(case_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case '{case_id}' not found.",
        )

    verification_service = VerificationService(db)
    summary = await verification_service.verify_case(
        case_id=case_id,
        actor_id=current_user.user_id,
    )

    await db.commit()

    return summary