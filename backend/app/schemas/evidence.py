"""
schemas/evidence.py

Pydantic request and response schemas for the Evidence Ingestion API.

Important note on upload requests:
  File upload endpoints use multipart/form-data, not application/json.
  This means metadata fields (notes, location_description, write_blocker_used)
  come through as Form fields in the same multipart request as the file.
  They cannot be nested in a JSON body alongside an UploadFile.

  FastAPI handles this cleanly with Form() annotations in the route handler —
  these schemas are used for response serialization only, not request parsing.

EvidenceResponse is the full record returned after upload or on GET.
EvidenceListResponse wraps a list for the case evidence tab.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.models.evidence import IntegrityStatus
from app.schemas.auth import UserResponse


# ─── Response schemas ─────────────────────────────────────────────────────────

class EvidenceResponse(BaseModel):
    """
    Full evidence record returned to the client.

    Includes the embedded acquired_by_user object so the frontend can
    display "Acquired by: Jonny Doe" without an extra request.

    sha256_hash is always 64 hex characters — displayed prominently
    in the UI since it's the core integrity proof.
    """
    id: str
    case_id: str
    filename: str
    file_type: Optional[str]
    file_size_bytes: int
    sha256_hash: str = Field(description="SHA-256 hex digest, 64 characters, server-computed at ingestion")
    acquired_by_user: UserResponse
    acquired_at: datetime
    location_description: Optional[str]
    notes: Optional[str]
    write_blocker_used: bool
    last_verified_at: Optional[datetime]
    integrity_status: IntegrityStatus

    model_config = {"from_attributes": True}


class EvidenceListResponse(BaseModel):
    """
    List of evidence items for a case — used by GET /cases/{id}/evidence.

    No pagination here. A single case rarely has thousands of evidence items,
    and returning them all at once makes the chain-of-custody view trivial
    to render. Add pagination if your use case demands it.
    """
    case_id: str
    items: list[EvidenceResponse]
    total: int


class EvidenceIngestionConfirmation(BaseModel):
    """
    Minimal confirmation returned immediately after a successful upload.

    Includes the evidence ID, hash, and a human-readable message.
    The full EvidenceResponse is also returned — this wraps it with
    a top-level status field for clarity.
    """
    status: str = "ingested"
    message: str
    evidence: EvidenceResponse