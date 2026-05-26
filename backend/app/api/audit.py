"""
api/audit.py

Audit Log read-only route handlers for DFECPIMS.

Endpoints:
  GET /cases/{case_id}/audit   — paginated audit entries for a case (all authenticated)
  GET /audit                   — system-wide audit log (supervisor only)

Both endpoints are strictly read-only. There are no POST, PATCH, or DELETE
routes here — the audit log is written exclusively by the service layer
(AuditService.log()) and is INSERT-only at the DB level via trigger.

The case-scoped endpoint is the primary one for day-to-day investigation work.
It surfaces the complete paper trail for a specific case: who created it, who
uploaded evidence, who ran verifications, who viewed it and when.

The system-wide endpoint is supervisor-only. It exposes auth events (login,
failed logins), cross-case activity by an actor, and any event not tied
to a specific case. Useful for security audits and incident response.

Filter parameters (all optional, all AND-combined):
  action         — exact action code match (e.g. HASH_FAILED)
  actor_id       — show only events by this user UUID
  from_timestamp — ISO 8601 UTC datetime lower bound
  to_timestamp   — ISO 8601 UTC datetime upper bound
  page           — 1-indexed page number
  page_size      — results per page (default 50, max 200)

Timestamps in query parameters must be ISO 8601 format with UTC timezone:
  2026-05-25T09:30:00Z  or  2026-05-25T09:30:00+00:00
FastAPI parses these automatically via datetime type annotations.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_user, require_supervisor
from app.core.security import TokenPayload
from app.schemas.audit import AuditLogListResponse
from app.services.audit_query_service import AuditQueryService
from app.services.case_service import CaseService


router = APIRouter(tags=["Audit Log"])


# ─── GET /cases/{case_id}/audit ───────────────────────────────────────────────

@router.get(
    "/cases/{case_id}/audit",
    response_model=AuditLogListResponse,
    summary="Get audit log entries for a specific case",
    status_code=status.HTTP_200_OK,
)
async def get_case_audit_log(
    case_id: str,
    # Filter parameters
    action: Optional[str] = Query(
        default=None,
        description="Exact action code filter (e.g. EVIDENCE_UPLOADED, HASH_FAILED)",
    ),
    actor_id: Optional[str] = Query(
        default=None,
        description="Filter by actor UUID — show only events by this user",
    ),
    from_timestamp: Optional[datetime] = Query(
        default=None,
        description="ISO 8601 UTC lower bound (e.g. 2026-05-01T00:00:00Z)",
    ),
    to_timestamp: Optional[datetime] = Query(
        default=None,
        description="ISO 8601 UTC upper bound (e.g. 2026-05-31T23:59:59Z)",
    ),
    # Pagination
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: TokenPayload = Depends(get_current_user),
) -> AuditLogListResponse:
    """
    Return paginated audit log entries for a specific case.

    Accessible to all authenticated users (investigator, supervisor, readonly).
    Entries are ordered newest-first by default — most relevant for live review.

    The `detail` field in each entry is a structured dict (not a raw JSON string)
    containing event-specific context: before/after values for updates, hash
    values for verification events, IP addresses for login events, etc.

    Common action codes you'll see in a case audit trail:
      CASE_CREATED, CASE_UPDATED, CASE_STATUS_CHANGED
      CASE_INVESTIGATOR_REASSIGNED, CASE_ACCESSED
      EVIDENCE_UPLOADED, EVIDENCE_ACCESSED, EVIDENCE_LIST_ACCESSED
      HASH_VERIFIED, HASH_FAILED
      REPORT_EXPORTED
    """
    # Verify the case exists before querying its audit log
    case_service = CaseService(db)
    case = await case_service.get_by_id(case_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case '{case_id}' not found.",
        )

    audit_service = AuditQueryService(db)
    return await audit_service.query(
        case_id=case_id,
        actor_id=actor_id,
        action=action,
        from_timestamp=from_timestamp,
        to_timestamp=to_timestamp,
        page=page,
        page_size=page_size,
        ascending=False,  # Newest-first for live view
    )


# ─── GET /audit ───────────────────────────────────────────────────────────────

@router.get(
    "/audit",
    response_model=AuditLogListResponse,
    summary="System-wide audit log (supervisor only)",
    status_code=status.HTTP_200_OK,
)
async def get_system_audit_log(
    # Filter parameters
    action: Optional[str] = Query(
        default=None,
        description="Exact action code filter",
    ),
    actor_id: Optional[str] = Query(
        default=None,
        description="Filter by actor UUID",
    ),
    case_id_filter: Optional[str] = Query(
        default=None,
        alias="case_id",
        description="Filter by case ID (returns entries for that case only)",
    ),
    from_timestamp: Optional[datetime] = Query(
        default=None,
        description="ISO 8601 UTC lower bound",
    ),
    to_timestamp: Optional[datetime] = Query(
        default=None,
        description="ISO 8601 UTC upper bound",
    ),
    # Pagination
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: TokenPayload = Depends(require_supervisor),
) -> AuditLogListResponse:
    """
    Return the system-wide audit log across all cases and system events.

    Supervisor-only. This includes:
      - Login and failed login events (USER_LOGIN, USER_LOGIN_FAILED)
      - Events from all cases (not just the supervisor's own)
      - User registration events
      - Any event not scoped to a specific case

    Useful for:
      - Investigating suspicious activity across the system
      - Tracking what a specific user has done across all cases
      - Reviewing failed hash verification events system-wide
      - Security audits and compliance reporting

    All filter parameters are optional and combine with AND logic.
    """
    audit_service = AuditQueryService(db)
    return await audit_service.query(
        case_id=case_id_filter,   # None = system-wide; set = scoped to that case
        actor_id=actor_id,
        action=action,
        from_timestamp=from_timestamp,
        to_timestamp=to_timestamp,
        page=page,
        page_size=page_size,
        ascending=False,
    )