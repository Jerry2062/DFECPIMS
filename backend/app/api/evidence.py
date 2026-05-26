"""
api/evidence.py

Evidence Ingestion route handlers for DFECPIMS.

Endpoints:
  POST  /cases/{case_id}/evidence          — upload and ingest a file (investigator+)
  GET   /cases/{case_id}/evidence          — list all evidence for a case (all authenticated)
  GET   /cases/{case_id}/evidence/{ev_id}  — get a single evidence item (all authenticated)

Upload request format:
  This endpoint uses multipart/form-data, NOT application/json.
  The file and all metadata fields are sent together as form parts.

  Fields:
    file                  (required) — the file binary
    location_description  (optional, string)
    notes                 (optional, string)
    write_blocker_used    (optional, bool, default false)

  Example with curl:
    curl -X POST http://localhost:8000/api/v1/cases/CASE-2026-0001/evidence \\
      -H "Authorization: Bearer <token>" \\
      -F "file=@/path/to/evidence.dd" \\
      -F "location_description=Seized hard drive from suspect workstation" \\
      -F "write_blocker_used=true" \\
      -F "notes=Imaged using FTK Imager 4.7"

SHA-256 computation:
  The hash is ALWAYS computed server-side at the moment the file bytes are
  received. The client never supplies a hash — if a client-provided hash were
  accepted, chain-of-custody integrity would be meaningless. The server
  computes the hash, stores it, and it becomes the ground truth.

Size limit enforcement:
  FastAPI reads the entire UploadFile into memory (via SpooledTemporaryFile).
  We enforce the size limit in the service layer after reading. If you need
  streaming enforcement BEFORE reading the full file, implement a custom
  middleware that counts incoming bytes on the upload route. For a forensic
  system with internal users, post-read enforcement is acceptable.
"""

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_user, require_investigator
from app.core.security import TokenPayload
from app.schemas.evidence import (
    EvidenceIngestionConfirmation,
    EvidenceListResponse,
    EvidenceResponse,
)
from app.services.audit_service import AuditService
from app.services.case_service import CaseService
from app.services.evidence_service import EvidenceService


router = APIRouter(tags=["Evidence Ingestion"])


# ─── Helper: fetch case or 404 ────────────────────────────────────────────────

async def _get_case_or_404(case_id: str, db: AsyncSession):
    service = CaseService(db)
    case = await service.get_by_id(case_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case '{case_id}' not found.",
        )
    return case


# ─── Helper: fetch evidence or 404 ───────────────────────────────────────────

async def _get_evidence_or_404(evidence_id: str, case_id: str, db: AsyncSession):
    service = EvidenceService(db)
    evidence = await service.get_by_id(evidence_id)
    if evidence is None or evidence.case_id != case_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Evidence '{evidence_id}' not found in case '{case_id}'.",
        )
    return evidence


# ─── POST /cases/{case_id}/evidence ──────────────────────────────────────────

@router.post(
    "/cases/{case_id}/evidence",
    response_model=EvidenceIngestionConfirmation,
    summary="Upload and ingest a file as evidence",
    status_code=status.HTTP_201_CREATED,
)
async def ingest_evidence(
    case_id: str,
    # File upload — FastAPI reads this from the multipart form
    file: Annotated[UploadFile, File(description="The evidence file to ingest")],
    # Metadata as Form fields alongside the file
    location_description: Annotated[
        Optional[str],
        Form(description="Physical or logical location where evidence was acquired"),
    ] = None,
    notes: Annotated[
        Optional[str],
        Form(description="Investigator notes about this evidence item"),
    ] = None,
    write_blocker_used: Annotated[
        bool,
        Form(description="Declaration that a hardware/software write blocker was used"),
    ] = False,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(require_investigator),
) -> EvidenceIngestionConfirmation:
    """
    Upload a file and register it as evidence against a case.

    The SHA-256 hash is computed server-side at the moment of ingestion.
    It is stored immediately and becomes the integrity ground truth for
    all future verification runs.

    The upload is rejected if:
      - The case does not exist
      - The case is Archived or Closed
      - The file is empty
      - The file exceeds the configured maximum size

    On success, returns the evidence record including the computed SHA-256 hash.
    An EVIDENCE_UPLOADED audit log entry is written as part of the same transaction.
    """
    # Step 1: Verify the case exists
    case = await _get_case_or_404(case_id, db)

    # Step 2: Read the file bytes
    # FastAPI's UploadFile.read() returns a coroutine for SpooledTemporaryFile.
    # We read all bytes here. For very large files (>500MB), consider streaming
    # in chunks — but that requires a custom approach since we need all bytes
    # to compute the hash before we know the final hash value.
    file_bytes = await file.read()

    # Step 3: Get the original filename (strip any path components the client sent)
    original_filename = file.filename or "unnamed_evidence"

    # Step 4: Ingest via service (hashes, stores, creates DB record, writes audit)
    try:
        service = EvidenceService(db)
        evidence = await service.ingest(
            case=case,
            file_bytes=file_bytes,
            original_filename=original_filename,
            acquired_by_id=current_user.user_id,
            acquired_by_name=current_user.name,
            location_description=location_description,
            notes=notes,
            write_blocker_used=write_blocker_used,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except RuntimeError as e:
        # File write integrity error — this is a server-side problem, not client error
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )

    await db.commit()
    await db.refresh(evidence)

    evidence_response = EvidenceResponse.model_validate(evidence)

    return EvidenceIngestionConfirmation(
        status="ingested",
        message=(
            f"Evidence '{evidence.id}' ingested successfully. "
            f"SHA-256: {evidence.sha256_hash}"
        ),
        evidence=evidence_response,
    )


# ─── GET /cases/{case_id}/evidence ───────────────────────────────────────────

@router.get(
    "/cases/{case_id}/evidence",
    response_model=EvidenceListResponse,
    summary="List all evidence items for a case",
    status_code=status.HTTP_200_OK,
)
async def list_evidence(
    case_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> EvidenceListResponse:
    """
    Return all evidence items attached to a case, ordered by acquisition time.

    Accessible to all authenticated users (investigator, supervisor, readonly).
    Viewing the evidence list is logged as EVIDENCE_LIST_ACCESSED in the audit log.
    """
    # Verify case exists
    await _get_case_or_404(case_id, db)

    service = EvidenceService(db)
    items = await service.list_by_case(case_id)

    # Log the access event
    audit = AuditService(db)
    await audit.log(
        action="EVIDENCE_LIST_ACCESSED",
        actor_id=current_user.user_id,
        case_id=case_id,
        detail={"item_count": len(items)},
    )
    await db.commit()

    return EvidenceListResponse(
        case_id=case_id,
        items=[EvidenceResponse.model_validate(ev) for ev in items],
        total=len(items),
    )


# ─── GET /cases/{case_id}/evidence/{evidence_id} ─────────────────────────────

@router.get(
    "/cases/{case_id}/evidence/{evidence_id}",
    response_model=EvidenceResponse,
    summary="Get a single evidence item",
    status_code=status.HTTP_200_OK,
)
async def get_evidence(
    case_id: str,
    evidence_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> EvidenceResponse:
    """
    Return a single evidence item including its SHA-256 hash and integrity status.

    Accessible to all authenticated users.
    Individual evidence access is logged as EVIDENCE_ACCESSED.
    """
    evidence = await _get_evidence_or_404(evidence_id, case_id, db)

    audit = AuditService(db)
    await audit.log(
        action="EVIDENCE_ACCESSED",
        actor_id=current_user.user_id,
        case_id=case_id,
        detail={
            "evidence_id": evidence_id,
            "filename": evidence.filename,
        },
    )
    await db.commit()

    return EvidenceResponse.model_validate(evidence)