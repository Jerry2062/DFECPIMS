"""
schemas/case.py

Pydantic request and response schemas for the Case Management API.

Naming convention mirrors auth.py:
  CaseCreate          — POST /cases body
  CaseUpdate          — PATCH /cases/{id} body (all fields optional)
  CaseStatusTransition — POST /cases/{id}/status body
  CaseResponse        — full case record returned to clients
  CaseSummary         — lighter version for list endpoints (no audit/evidence)
  InvestigatorReassign — POST /cases/{id}/reassign body

One design note: CaseResponse includes a nested UserResponse for the
investigator, so the frontend doesn't need a second request to display
the investigator's name. Evidence items and audit logs are NOT embedded
here — they have their own endpoints and would bloat the response.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.models.case import CaseSeverity, CaseStatus
from app.schemas.auth import UserResponse


# ─── Request schemas ──────────────────────────────────────────────────────────

class CaseCreate(BaseModel):
    """
    POST /cases request body.

    investigator_id is optional at request time. If omitted, the case is
    assigned to the requesting user (the authenticated investigator or supervisor).
    Supervisors can supply a different investigator_id to assign on behalf of someone else.
    """
    title: str = Field(
        min_length=3,
        max_length=500,
        description="Short descriptive title of the case",
    )
    description: Optional[str] = Field(
        default=None,
        description="Full narrative description of the incident",
    )
    severity: CaseSeverity = Field(
        default=CaseSeverity.MEDIUM,
        description="Assessed severity: LOW, MEDIUM, HIGH, or CRITICAL",
    )
    investigator_id: Optional[str] = Field(
        default=None,
        description=(
            "UUID of the lead investigator. Defaults to the requesting user. "
            "Only supervisors can assign to someone else."
        ),
    )


class CaseUpdate(BaseModel):
    """
    PATCH /cases/{id} request body.

    All fields optional — only provided fields are updated.
    Status and investigator changes have their own dedicated endpoints
    (they're significant enough to deserve separate audit action codes).
    """
    title: Optional[str] = Field(
        default=None,
        min_length=3,
        max_length=500,
    )
    description: Optional[str] = Field(default=None)
    severity: Optional[CaseSeverity] = Field(default=None)


class CaseStatusTransition(BaseModel):
    """
    POST /cases/{id}/status request body.

    Explicit status transition request. The service layer validates whether
    the transition is permitted from the current state.
    """
    new_status: CaseStatus = Field(
        description="The target status to transition to",
    )
    reason: Optional[str] = Field(
        default=None,
        max_length=1000,
        description="Optional explanation for the status change (stored in audit log)",
    )


class InvestigatorReassign(BaseModel):
    """
    POST /cases/{id}/reassign request body.

    Supervisor-only. Reassigns the lead investigator on a case.
    """
    new_investigator_id: str = Field(
        description="UUID of the new lead investigator",
    )
    reason: Optional[str] = Field(
        default=None,
        max_length=1000,
        description="Optional explanation for the reassignment",
    )


# ─── Response schemas ─────────────────────────────────────────────────────────

class CaseSummary(BaseModel):
    """
    Lightweight case representation for list endpoints.

    Includes the investigator name (embedded) but not evidence or audit data.
    Used for GET /cases (paginated list).
    """
    id: str
    title: str
    severity: CaseSeverity
    status: CaseStatus
    investigator: UserResponse
    evidence_count: int = Field(
        default=0,
        description="Number of evidence items attached to this case",
    )
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CaseResponse(BaseModel):
    """
    Full case record returned for GET /cases/{id}.

    Includes the embedded investigator object. Evidence and audit log entries
    are fetched via their own endpoints to keep response sizes manageable.
    """
    id: str
    title: str
    description: Optional[str]
    severity: CaseSeverity
    status: CaseStatus
    investigator: UserResponse
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CaseListResponse(BaseModel):
    """
    Paginated list response for GET /cases.

    Wraps the list in a container with pagination metadata so the frontend
    can implement proper paging without guessing at total counts.
    """
    items: list[CaseSummary]
    total: int
    page: int
    page_size: int
    total_pages: int