"""
services/verification_service.py

SHA-256 hash verification service for DFECPIMS.

This is Module 6 — the integrity check engine.

What this does:
  Given an Evidence record, it:
    1. Reads the file from storage_path on disk in 64KB chunks
    2. Computes a fresh SHA-256 hash from those bytes
    3. Compares the fresh hash to the stored hash (set at ingestion)
    4. Updates last_verified_at and integrity_status on the Evidence record
    5. Writes a HASH_VERIFIED or HASH_FAILED audit log entry
    6. Returns a VerificationResult with the outcome and both hash values

Why re-read from disk every time:
  Using a cached or in-memory copy would defeat the purpose entirely.
  The whole point is to verify the bytes currently stored on disk
  match what was originally ingested. Re-reading from storage_path
  is the only honest implementation.

Why chunk-read instead of reading the whole file:
  Evidence files can be disk images, memory dumps, or raw device captures
  that are multiple gigabytes. Loading them entirely into memory would OOM
  the server. 64KB chunks keep memory usage flat regardless of file size.

The sha256_hash field on Evidence is treated as immutable ground truth here.
This service never modifies it — only integrity_status and last_verified_at
are updated after a verification run.

Distinct failure modes:
  FILE_MISSING  — os.path.exists(storage_path) is False
                  The file is gone. Could be storage failure, accidental
                  deletion, or deliberate tampering with the filesystem.
                  Written as EVIDENCE_FILE_MISSING in the audit log.

  HASH_MISMATCH — File exists but computed hash != stored hash
                  The file has been modified since ingestion.
                  This is the most alarming outcome — treated as a
                  potential evidence tampering event.
                  Written as HASH_FAILED in the audit log.

  VERIFIED      — File exists and hash matches exactly.
                  Written as HASH_VERIFIED in the audit log.
"""

import hashlib
import os
from datetime import datetime, timezone
from typing import Optional

import aiofiles

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.evidence import Evidence, IntegrityStatus
from app.schemas.verification import (
    BulkVerificationSummary,
    VerificationOutcome,
    VerificationResult,
)
from app.services.audit_service import AuditService
from app.services.evidence_service import EvidenceService


# Same chunk size as ingestion — consistent, well-tested value
HASH_CHUNK_SIZE = 65_536  # 64 KB


class VerificationService:
    """
    Re-hashes stored evidence files and compares against the ingestion-time hash.

    Usage — single item:
        service = VerificationService(db)
        result = await service.verify_evidence(evidence, actor_id="uuid")
        await db.commit()

    Usage — whole case:
        result = await service.verify_case(case_id="CASE-2026-0001", actor_id="uuid")
        await db.commit()
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.audit = AuditService(db)

    # ─── Core verification logic ──────────────────────────────────────────────

    @staticmethod
    async def _compute_hash_from_disk(storage_path: str) -> Optional[str]:
        """
        Read a file from disk in chunks and compute its SHA-256 hash.

        Returns the hex digest string, or None if the file cannot be read.
        Uses aiofiles for non-blocking I/O — important since verification
        may be called while the event loop is handling other requests.

        Args:
            storage_path: Absolute path to the file on disk.

        Returns:
            64-character SHA-256 hex digest, or None on read failure.
        """
        hasher = hashlib.sha256()

        try:
            async with aiofiles.open(storage_path, "rb") as f:
                while True:
                    chunk = await f.read(HASH_CHUNK_SIZE)
                    if not chunk:
                        break
                    hasher.update(chunk)
            return hasher.hexdigest()

        except (OSError, IOError):
            # File unreadable — permission denied, I/O error, corrupt filesystem
            return None

    async def verify_evidence(
        self,
        evidence: Evidence,
        actor_id: str,
    ) -> VerificationResult:
        """
        Verify the integrity of a single evidence item.

        Reads the file at evidence.storage_path, computes its SHA-256 hash,
        and compares against evidence.sha256_hash (set at ingestion).

        Updates evidence.last_verified_at and evidence.integrity_status
        in place (flushed but not committed — caller commits).

        Writes an audit log entry for every run, regardless of outcome.

        Args:
            evidence: The Evidence ORM object to verify.
            actor_id: UUID of the user triggering the verification.

        Returns:
            VerificationResult with outcome, both hashes, and timestamp.
        """
        now = datetime.now(timezone.utc)
        stored_hash = evidence.sha256_hash

        # ── Step 1: Check if file exists ──────────────────────────────────────

        if not os.path.exists(evidence.storage_path):
            # File is missing from disk entirely
            evidence.last_verified_at = now
            evidence.integrity_status = IntegrityStatus.failed
            self.db.add(evidence)
            await self.db.flush()

            await self.audit.log(
                action="EVIDENCE_FILE_MISSING",
                actor_id=actor_id,
                case_id=evidence.case_id,
                detail={
                    "evidence_id": evidence.id,
                    "filename": evidence.filename,
                    "storage_path": evidence.storage_path,
                    "stored_hash": stored_hash,
                    "note": "File not found at storage path — possible deletion or storage failure",
                },
            )

            return VerificationResult(
                evidence_id=evidence.id,
                case_id=evidence.case_id,
                filename=evidence.filename,
                outcome=VerificationOutcome.FILE_MISSING,
                stored_hash=stored_hash,
                computed_hash=None,
                verified_at=now,
                verdict=(
                    f"FAIL — File not found at expected storage path. "
                    f"Evidence '{evidence.id}' ({evidence.filename}) may have been "
                    f"deleted or moved. Stored hash: {stored_hash}"
                ),
                passed=False,
            )

        # ── Step 2: Compute fresh hash from disk ──────────────────────────────

        computed_hash = await self._compute_hash_from_disk(evidence.storage_path)

        if computed_hash is None:
            # File exists but couldn't be read — treat as missing
            evidence.last_verified_at = now
            evidence.integrity_status = IntegrityStatus.failed
            self.db.add(evidence)
            await self.db.flush()

            await self.audit.log(
                action="EVIDENCE_FILE_MISSING",
                actor_id=actor_id,
                case_id=evidence.case_id,
                detail={
                    "evidence_id": evidence.id,
                    "filename": evidence.filename,
                    "storage_path": evidence.storage_path,
                    "stored_hash": stored_hash,
                    "note": "File exists but could not be read — permission or I/O error",
                },
            )

            return VerificationResult(
                evidence_id=evidence.id,
                case_id=evidence.case_id,
                filename=evidence.filename,
                outcome=VerificationOutcome.FILE_MISSING,
                stored_hash=stored_hash,
                computed_hash=None,
                verified_at=now,
                verdict=(
                    f"FAIL — File exists at storage path but could not be read. "
                    f"Check filesystem permissions. Evidence '{evidence.id}' ({evidence.filename}). "
                    f"Stored hash: {stored_hash}"
                ),
                passed=False,
            )

        # ── Step 3: Compare hashes ────────────────────────────────────────────

        hashes_match = computed_hash == stored_hash

        # Update integrity fields regardless of outcome
        evidence.last_verified_at = now
        evidence.integrity_status = (
            IntegrityStatus.verified if hashes_match else IntegrityStatus.failed
        )
        self.db.add(evidence)
        await self.db.flush()

        if hashes_match:
            await self.audit.log(
                action="HASH_VERIFIED",
                actor_id=actor_id,
                case_id=evidence.case_id,
                detail={
                    "evidence_id": evidence.id,
                    "filename": evidence.filename,
                    "stored_hash": stored_hash,
                    "computed_hash": computed_hash,
                    "outcome": "PASS",
                },
            )

            return VerificationResult(
                evidence_id=evidence.id,
                case_id=evidence.case_id,
                filename=evidence.filename,
                outcome=VerificationOutcome.VERIFIED,
                stored_hash=stored_hash,
                computed_hash=computed_hash,
                verified_at=now,
                verdict=(
                    f"PASS — Integrity verified. SHA-256 matches ingestion-time hash. "
                    f"Evidence '{evidence.id}' ({evidence.filename}) is unmodified. "
                    f"Hash: {computed_hash}"
                ),
                passed=True,
            )

        else:
            # Hash mismatch — the most serious outcome
            await self.audit.log(
                action="HASH_FAILED",
                actor_id=actor_id,
                case_id=evidence.case_id,
                detail={
                    "evidence_id": evidence.id,
                    "filename": evidence.filename,
                    "stored_hash": stored_hash,
                    "computed_hash": computed_hash,
                    "outcome": "FAIL",
                    "note": (
                        "SHA-256 mismatch detected. Evidence file may have been "
                        "modified since ingestion. Treat as potential tampering event."
                    ),
                },
            )

            return VerificationResult(
                evidence_id=evidence.id,
                case_id=evidence.case_id,
                filename=evidence.filename,
                outcome=VerificationOutcome.HASH_MISMATCH,
                stored_hash=stored_hash,
                computed_hash=computed_hash,
                verified_at=now,
                verdict=(
                    f"FAIL — SHA-256 MISMATCH DETECTED. "
                    f"Evidence '{evidence.id}' ({evidence.filename}) has been modified "
                    f"since ingestion. This is a critical integrity failure. "
                    f"Stored: {stored_hash} | Computed: {computed_hash}"
                ),
                passed=False,
            )

    # ─── Bulk verification ────────────────────────────────────────────────────

    async def verify_case(
        self,
        case_id: str,
        actor_id: str,
    ) -> BulkVerificationSummary:
        """
        Run hash verification on every evidence item in a case.

        Verifies items in acquisition order. Each item is independently
        verified — a failure on one item does not stop the others.
        All results are written to the audit log as part of this call.

        This is the right thing to call before generating a PDF chain-of-custody
        report — it gives you a fresh integrity snapshot of the entire case.

        Args:
            case_id:  The case to verify.
            actor_id: UUID of the user requesting bulk verification.

        Returns:
            BulkVerificationSummary with per-item results and aggregate counts.
        """
        ev_service = EvidenceService(self.db)
        evidence_items = await ev_service.list_by_case(case_id)

        now = datetime.now(timezone.utc)
        results: list[VerificationResult] = []

        for evidence in evidence_items:
            result = await self.verify_evidence(evidence, actor_id=actor_id)
            results.append(result)

        # Aggregate counts
        verified_count = sum(1 for r in results if r.outcome == VerificationOutcome.VERIFIED)
        mismatch_count = sum(1 for r in results if r.outcome == VerificationOutcome.HASH_MISMATCH)
        missing_count = sum(1 for r in results if r.outcome == VerificationOutcome.FILE_MISSING)

        # Write a single summary audit entry for the bulk run
        await self.audit.log(
            action="BULK_VERIFICATION_RUN",
            actor_id=actor_id,
            case_id=case_id,
            detail={
                "total_items": len(results),
                "verified": verified_count,
                "mismatch": mismatch_count,
                "missing": missing_count,
                "all_passed": verified_count == len(results),
            },
        )

        return BulkVerificationSummary(
            case_id=case_id,
            total_items=len(results),
            verified_count=verified_count,
            mismatch_count=mismatch_count,
            missing_count=missing_count,
            all_passed=(verified_count == len(results) and len(results) > 0),
            verified_at=now,
            results=results,
        )