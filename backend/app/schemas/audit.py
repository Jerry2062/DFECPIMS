"""
schemas/audit.py

Pydantic response schemas for the Audit Log API.

Design notes:

  AuditLogEntry is the core response type. The `detail` field is stored
  in the database as a JSON string but is deserialized back to a dict
  (or None) here — the client gets structured data, not a string to parse.

  actor_name is derived from the joined User relationship when available.
  If the user was deleted after the event was logged (actor_id set to NULL
  by the SET NULL FK constraint), actor_name surfaces as None. The frontend
  should render this as "User deleted" or similar.

  We do NOT expose the actor's full UserResponse here — just their name
  and ID. The audit log is a narrow read surface; full user objects would
  bloat every entry unnecessarily.

AuditLogListResponse wraps a list with pagination — mandatory since a
busy case can accumulate thousands of entries quickly.
"""

import json
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class AuditLogEntry(BaseModel):
    """
    A single audit log entry, safe to return to clients.

    The `detail` field is deserialized from the stored JSON string back
    to a dict at response time so the client doesn't have to parse it.
    """
    id: str
    case_id: Optional[str]
    actor_id: Optional[str]
    actor_name: Optional[str] = Field(
        default=None,
        description="Display name of the actor; None if user was subsequently deleted",
    )
    action: str = Field(description="UPPER_SNAKE_CASE event code")
    detail: Optional[dict[str, Any]] = Field(
        default=None,
        description="Structured context for the event; None if no detail was recorded",
    )
    timestamp: datetime

    model_config = {"from_attributes": True}

    @field_validator("detail", mode="before")
    @classmethod
    def deserialize_detail(cls, v: Any) -> Optional[dict]:
        """
        The DB stores detail as a raw JSON string (or None).
        Deserialize it before Pydantic validates the rest of the model.
        If the string is malformed JSON (shouldn't happen, but be safe),
        return it wrapped in a dict rather than crashing the response.
        """
        if v is None:
            return None
        if isinstance(v, dict):
            # Already deserialized — happens when constructed from a dict directly
            return v
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (json.JSONDecodeError, ValueError):
                # Malformed JSON stored in DB — surface it as raw string
                # rather than dropping the information entirely
                return {"_raw": v}
        return v


class AuditLogListResponse(BaseModel):
    """
    Paginated list of audit log entries.

    Used by both:
      GET /cases/{case_id}/audit   — case-scoped entries
      GET /audit                   — system-wide entries (supervisor only)
    """
    items: list[AuditLogEntry]
    total: int
    page: int
    page_size: int
    total_pages: int
    # Context field: populated for case-scoped queries, None for system-wide
    case_id: Optional[str] = None