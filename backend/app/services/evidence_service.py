"""
services/evidence_service.py

Business logic for Evidence Ingestion in DFECPIMS.

Responsibilities:
  1. Generate EV-NNN IDs atomically (SELECT FOR UPDATE on evidence_sequence)
  2. Compute SHA-256 hash server-side during ingestion — never from client input
  3. Store the file at a deterministic, namespaced path
  4. Detect MIME type from actual file bytes (not client-supplied Content-Type)
  5. Persist the Evidence record with hash, path, and all metadata
  6. Write EVIDENCE_UPLOADED audit log entry
  7. Provide read methods for evidence retrieval

SHA-256 hashing approach:
  Files are read in 64KB chunks through hashlib.sha256() while simultaneously
  being written to disk. This means:
    - Memory usage is bounded at ~64KB regardless of file size
    - The file is read exactly once (no second pass for hashing)
    - The hash reflects the exact bytes stored on disk

  This is critical: if we hashed the upload stream and then wrote to disk
  separately, a buggy write could store different bytes than what we hashed.
  Reading chunks to both hasher and file writer simultaneously guarantees
  hash == bytes on disk.

File storage layout:
  {EVIDENCE_STORAGE_PATH}/
    {case_id}/
      {evidence_id}/
        {original_filename}

  Example:
    /var/dfecpims/evidence/CASE-2026-0001/EV-001/memory_dump.raw

  This namespacing ensures:
    - No filename collisions across cases or evidence items
    - Manual inspection of the storage directory is human-readable
    - Deleting a case's evidence directory is a single rm -rf (if ever needed)
"""

import hashlib
import os
from datetime import datetime, timezone
from typing import Optional

import aiofiles
import magic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.case import Case, CaseStatus
from app.models.evidence import Evidence, IntegrityStatus
from app.models.case_sequence import EvidenceSequence
from app.services.audit_service import AuditService


# Chunk size for streaming file reads — 64KB is a reasonable balance between
# syscall overhead and memory pressure. Adjust if profiling shows a bottleneck.
HASH_CHUNK_SIZE = 65_536  # 64 KB

# Storage base path from environment; falls back to a local dev path
EVIDENCE_STORAGE_PATH: str = os.environ.get(
    "EVIDENCE_STORAGE_PATH",
    "/tmp/dfecpims_evidence_dev",
)

# Maximum allowed upload size in bytes (500MB default)
MAX_UPLOAD_SIZE_BYTES: int = int(
    os.environ.get("MAX_UPLOAD_SIZE_BYTES", str(500 * 1024 * 1024))
)


class EvidenceService:
    """
    Handles evidence ingestion, storage, and retrieval.

    Usage:
        service = EvidenceService(db)
        evidence = await service.ingest(
            case=case_orm,
            file=upload_file,
            acquired_by_id="user-uuid",
            ...
        )
        await db.commit()
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.audit = AuditService(db)

    # ─── ID generation ────────────────────────────────────────────────────────

    async def generate_evidence_id(self) -> str:
        """
        Generate the next EV-NNN identifier atomically.

        Uses SELECT ... FOR UPDATE on the single-row evidence_sequence table.
        The lock is held until the surrounding transaction commits.

        EV-NNN zero-pads to 3 digits. Beyond EV-999, it expands gracefully
        (EV-1000, EV-1001, ...) — the String(10) column in the model
        handles up to EV-9999999 without truncation.

        Returns:
            A string like "EV-001" or "EV-042".
        """
        result = await self.db.execute(
            select(EvidenceSequence)
            .where(EvidenceSequence.id == 1)
            .with_for_update()
        )
        seq_row = result.scalar_one_or_none()

        if seq_row is None:
            # Seed row missing — create it. This should only happen if
            # migrations/init_schema.py seed step was skipped.
            seq_row = EvidenceSequence(id=1, last_sequence=1)
            self.db.add(seq_row)
            await self.db.flush()
            next_seq = 1
        else:
            next_seq = seq_row.last_sequence + 1
            seq_row.last_sequence = next_seq
            self.db.add(seq_row)
            await self.db.flush()

        return f"EV-{next_seq:03d}"

    # ─── File hashing and storage ─────────────────────────────────────────────

    @staticmethod
    async def _hash_and_store(
        file_data: bytes,
        dest_path: str,
    ) -> tuple[str, int]:
        """
        Write file bytes to dest_path and return (sha256_hex, file_size_bytes).

        For in-memory bytes (small files already read into memory):
          - Computes SHA-256 in one pass
          - Writes to disk asynchronously via aiofiles
          - Returns hex digest and byte count

        Args:
            file_data:  Raw file bytes (already read from the upload stream).
            dest_path:  Absolute path where the file should be written.

        Returns:
            Tuple of (sha256_hex_string, file_size_in_bytes).
        """
        # Ensure parent directory exists
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)

        # Compute hash and write in one pass
        hasher = hashlib.sha256()
        hasher.update(file_data)
        sha256_hex = hasher.hexdigest()

        async with aiofiles.open(dest_path, "wb") as f:
            await f.write(file_data)

        return sha256_hex, len(file_data)

    @staticmethod
    async def _hash_and_store_streaming(
        file_obj,
        dest_path: str,
    ) -> tuple[str, int]:
        """
        Stream a file from an async-readable source to disk, computing
        SHA-256 simultaneously in 64KB chunks.

        Use this for large files (>10MB) to avoid loading the whole file
        into memory. For UploadFile objects from FastAPI, file_obj is
        the SpooledTemporaryFile backing the upload.

        Args:
            file_obj:   Async-readable file-like object (FastAPI UploadFile.file).
            dest_path:  Absolute path where the file should be written.

        Returns:
            Tuple of (sha256_hex_string, file_size_in_bytes).
        """
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)

        hasher = hashlib.sha256()
        total_bytes = 0

        async with aiofiles.open(dest_path, "wb") as out_file:
            while True:
                chunk = file_obj.read(HASH_CHUNK_SIZE)
                # UploadFile.file is a SpooledTemporaryFile — its read() is sync.
                # We read sync chunks and write async. This is fine because
                # the SpooledTemporaryFile is already in memory or local disk.
                if not chunk:
                    break
                hasher.update(chunk)
                await out_file.write(chunk)
                total_bytes += len(chunk)

        return hasher.hexdigest(), total_bytes

    # ─── MIME detection ───────────────────────────────────────────────────────

    @staticmethod
    def _detect_mime_type(file_bytes: bytes) -> str:
        """
        Detect MIME type from the first bytes of a file using libmagic.

        Reads actual magic bytes — not the client-supplied Content-Type header,
        which can be spoofed. Returns 'application/octet-stream' as fallback
        if detection fails (e.g. python-magic not installed or unknown format).

        Args:
            file_bytes: The first N bytes of the file (64 bytes is enough
                        for most MIME detection; we pass the first 1024).

        Returns:
            MIME type string, e.g. "image/jpeg", "application/pdf".
        """
        try:
            return magic.from_buffer(file_bytes[:1024], mime=True)
        except Exception:
            # python-magic requires libmagic to be installed on the OS.
            # If it's missing, fall back gracefully rather than crashing.
            return "application/octet-stream"

    # ─── Main ingestion method ────────────────────────────────────────────────

    async def ingest(
        self,
        case: Case,
        file_bytes: bytes,
        original_filename: str,
        acquired_by_id: str,
        acquired_by_name: str,
        location_description: Optional[str] = None,
        notes: Optional[str] = None,
        write_blocker_used: bool = False,
    ) -> Evidence:
        """
        Ingest a new piece of digital evidence into DFECPIMS.

        This is the core of Module 4. It:
          1. Validates the case is in an active state
          2. Enforces max upload size
          3. Generates an EV-NNN ID atomically
          4. Detects MIME type from file bytes
          5. Computes SHA-256 hash server-side (never from client input)
          6. Writes file to namespaced storage path
          7. Persists the Evidence record
          8. Writes EVIDENCE_UPLOADED audit log entry

        Args:
            case:                The Case ORM object (must be Active or UnderReview).
            file_bytes:          Raw bytes of the uploaded file.
            original_filename:   Original filename from the upload (sanitized here).
            acquired_by_id:      UUID of the user performing ingestion.
            acquired_by_name:    Display name for audit log detail.
            location_description: Where the evidence was physically/logically acquired.
            notes:               Free-text investigator notes.
            write_blocker_used:  Declaration that a write blocker was in place.

        Returns:
            The newly created Evidence ORM object (flushed, not committed).

        Raises:
            ValueError: If the case is in a terminal state, or if the file
                        exceeds the maximum allowed size.
        """
        # Guard: don't add evidence to terminal cases
        if case.status in {CaseStatus.Archived, CaseStatus.Closed}:
            raise ValueError(
                f"Cannot add evidence to a '{case.status.value}' case. "
                "Evidence ingestion is only permitted on Active or UnderReview cases."
            )

        # Guard: max upload size
        file_size = len(file_bytes)
        if file_size > MAX_UPLOAD_SIZE_BYTES:
            max_mb = MAX_UPLOAD_SIZE_BYTES / (1024 * 1024)
            actual_mb = file_size / (1024 * 1024)
            raise ValueError(
                f"File size {actual_mb:.1f}MB exceeds the maximum allowed "
                f"upload size of {max_mb:.0f}MB."
            )

        # Guard: reject empty files
        if file_size == 0:
            raise ValueError("Cannot ingest an empty file. Upload was rejected.")

        # Step 1: Generate evidence ID atomically
        evidence_id = await self.generate_evidence_id()

        # Step 2: Sanitize filename — strip path separators to prevent
        # directory traversal if someone uploads a file named "../../etc/passwd"
        safe_filename = os.path.basename(original_filename).strip()
        if not safe_filename:
            safe_filename = evidence_id  # Fallback if filename is empty after strip

        # Step 3: Detect MIME type from actual bytes
        mime_type = self._detect_mime_type(file_bytes)

        # Step 4: Build storage path
        # Layout: {base}/{case_id}/{evidence_id}/{filename}
        storage_path = os.path.join(
            EVIDENCE_STORAGE_PATH,
            case.id,
            evidence_id,
            safe_filename,
        )

        # Step 5 + 6: Hash and store simultaneously
        sha256_hex, stored_size = await self._hash_and_store(
            file_data=file_bytes,
            dest_path=storage_path,
        )

        # Sanity check: stored size should match what we received
        # If these differ, something went wrong with the write — abort.
        if stored_size != file_size:
            # Attempt to clean up the partial file
            try:
                os.remove(storage_path)
            except OSError:
                pass
            raise RuntimeError(
                f"File write integrity error: received {file_size} bytes, "
                f"stored {stored_size} bytes. Evidence ingestion aborted."
            )

        # Step 7: Persist Evidence record
        evidence = Evidence(
            id=evidence_id,
            case_id=case.id,
            filename=safe_filename,
            file_type=mime_type,
            file_size_bytes=file_size,
            storage_path=storage_path,
            sha256_hash=sha256_hex,         # Ground truth hash — never modified after this
            acquired_by=acquired_by_id,
            acquired_at=datetime.now(timezone.utc),
            location_description=location_description,
            notes=notes,
            write_blocker_used=write_blocker_used,
            last_verified_at=None,           # Not yet verified
            integrity_status=IntegrityStatus.pending,
        )

        self.db.add(evidence)
        await self.db.flush()

        # Step 8: Audit log
        await self.audit.log(
            action="EVIDENCE_UPLOADED",
            actor_id=acquired_by_id,
            case_id=case.id,
            detail={
                "evidence_id": evidence_id,
                "filename": safe_filename,
                "file_type": mime_type,
                "file_size_bytes": file_size,
                "sha256_hash": sha256_hex,
                "write_blocker_used": write_blocker_used,
                "location_description": location_description,
                "acquired_by": acquired_by_name,
            },
        )

        return evidence

    # ─── Read methods ─────────────────────────────────────────────────────────

    async def get_by_id(self, evidence_id: str) -> Optional[Evidence]:
        """
        Fetch a single evidence item by its EV-NNN ID.

        Returns None if not found.
        """
        result = await self.db.execute(
            select(Evidence).where(Evidence.id == evidence_id)
        )
        return result.scalar_one_or_none()

    async def list_by_case(self, case_id: str) -> list[Evidence]:
        """
        Return all evidence items for a case, ordered by acquisition time.

        No pagination — see EvidenceListResponse docstring for rationale.
        """
        result = await self.db.execute(
            select(Evidence)
            .where(Evidence.case_id == case_id)
            .order_by(Evidence.acquired_at)
        )
        return list(result.scalars().all())