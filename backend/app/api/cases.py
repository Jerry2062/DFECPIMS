"""
api/cases.py

Case Management route handlers for DFECPIMS.

Endpoints:
  POST   /cases                    — create a case (investigator+)
  GET    /cases                    — list cases with filters (all authenticated)
  GET    /cases/{case_id}          — get case detail (all authenticated)
  PATCH  /cases/{case_id}          — update title/description/severity (investigator+)
  POST   /cases/{case_id}/status   — transition status (investigator+ with matrix rules)
  POST   /cases/{case_id}/reassign — reassign investigator (supervisor only)

Route handlers are thin. They:
  1. Extract validated input (Pydantic)
  2. Call the service
  3. Handle ValueError → HTTP error mapping
  4. Return the response schema

All write operations (create, update, transition, reassign) are followed
by an audit log entry written inside the service layer, then a single commit.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import (
    get_current_user,
    require_investigator,
    require_supervisor,
)
from app.core.security import TokenPayload
from app.models.case import CaseSeverity, CaseStatus
from app.schemas.case import (
    CaseCreate,
    CaseListResponse,
    CaseResponse,
    CaseStatusTransition,
    CaseUpdate,
    InvestigatorReassign,
)
from app.services.audit_service import AuditService
from app.services.case_service import CaseService
from app.services.user_service import UserService


router = APIRouter(prefix="/cases", tags=["Case Management"])


# ─── Helper: fetch case or 404 ────────────────────────────────────────────────

async def _get_case_or_404(case_id: str, db: AsyncSession):
    """
    Internal helper to fetch a case by ID and raise 404 if missing.
    Used by multiple route handlers below.
    """
    service = CaseService(db)
    case = await service.get_by_id(case_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case '{case_id}' not found.",
        )
    return case


# ─── POST /cases ──────────────────────────────────────────────────────────────

@router.post(
    "",
    response_model=CaseResponse,
    summary="Create a new forensic case",
    status_code=status.HTTP_201_CREATED,
)
async def create_case(
    data: CaseCreate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(require_investigator),
) -> CaseResponse:
    """
    Open a new forensic investigation case.

    - Investigators create cases assigned to themselves by default.
    - Supervisors may supply investigator_id to assign to someone else.
    - The case ID is auto-generated as CASE-YYYY-NNNN.
    - Initial status is always Active.
    - An audit log entry is written at creation time.
    """
    # If the request supplies an investigator_id different from the actor,
    # only supervisors are allowed to do that.
    from app.models.user import UserRole
    if (
        data.investigator_id is not None
        and data.investigator_id != current_user.user_id
        and current_user.role != UserRole.supervisor
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only supervisors can assign a case to a different investigator.",
        )

    # Verify the target investigator exists (if explicitly provided)
    if data.investigator_id is not None:
        user_service = UserService(db)
        target = await user_service.get_by_id(data.investigator_id)
        if target is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Investigator with ID '{data.investigator_id}' not found.",
            )

    service = CaseService(db)
    case = await service.create_case(
        data=data,
        actor_id=current_user.user_id,
        actor_name=current_user.name,
    )
    await db.commit()
    await db.refresh(case)

    return CaseResponse.model_validate(case)


# ─── GET /cases ───────────────────────────────────────────────────────────────

@router.get(
    "",
    response_model=CaseListResponse,
    summary="List cases with optional filters and pagination",
    status_code=status.HTTP_200_OK,
)
async def list_cases(
    page: int = Query(default=1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(default=20, ge=1, le=100, description="Results per page"),
    status_filter: Optional[CaseStatus] = Query(default=None, alias="status"),
    severity_filter: Optional[CaseSeverity] = Query(default=None, alias="severity"),
    investigator_id: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None, description="Substring search on case title"),
    db: AsyncSession = Depends(get_db),
    _: TokenPayload = Depends(get_current_user),
) -> CaseListResponse:
    """
    Return a paginated list of cases.

    All filter parameters are optional and combine with AND logic:
      ?status=Active&severity=CRITICAL
      ?search=ransomware&page=2&page_size=10
    """
    service = CaseService(db)
    return await service.list_cases(
        page=page,
        page_size=page_size,
        status_filter=status_filter,
        severity_filter=severity_filter,
        investigator_id_filter=investigator_id,
        search=search,
    )


# ─── GET /cases/{case_id} ─────────────────────────────────────────────────────

@router.get(
    "/{case_id}",
    response_model=CaseResponse,
    summary="Get full details of a specific case",
    status_code=status.HTTP_200_OK,
)
async def get_case(
    case_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> CaseResponse:
    """
    Return full case details including the embedded investigator.

    Evidence items and audit log entries are available via separate endpoints:
      GET /cases/{case_id}/evidence
      GET /cases/{case_id}/audit

    Accessing a case detail is logged as CASE_ACCESSED for forensic completeness.
    """
    case = await _get_case_or_404(case_id, db)

    # Log this access event — knowing who read a case is forensically relevant
    audit = AuditService(db)
    await audit.log(
        action="CASE_ACCESSED",
        actor_id=current_user.user_id,
        case_id=case.id,
        detail={"case_title": case.title},
    )
    await db.commit()

    return CaseResponse.model_validate(case)


# ─── PATCH /cases/{case_id} ───────────────────────────────────────────────────

@router.patch(
    "/{case_id}",
    response_model=CaseResponse,
    summary="Update case title, description, or severity",
    status_code=status.HTTP_200_OK,
)
async def update_case(
    case_id: str,
    data: CaseUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(require_investigator),
) -> CaseResponse:
    """
    Update mutable case fields.

    Only title, description, and severity can be updated here.
    Status changes → POST /cases/{id}/status
    Investigator reassignment → POST /cases/{id}/reassign

    Archived and Closed cases cannot be modified.
    """
    case = await _get_case_or_404(case_id, db)

    if case.status in {CaseStatus.Archived, CaseStatus.Closed}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Case '{case_id}' is {case.status.value} and cannot be modified.",
        )

    try:
        service = CaseService(db)
        case = await service.update_case(
            case=case,
            data=data,
            actor_id=current_user.user_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    await db.commit()
    await db.refresh(case)

    return CaseResponse.model_validate(case)


# ─── POST /cases/{case_id}/status ────────────────────────────────────────────

@router.post(
    "/{case_id}/status",
    response_model=CaseResponse,
    summary="Transition case to a new status",
    status_code=status.HTTP_200_OK,
)
async def transition_case_status(
    case_id: str,
    data: CaseStatusTransition,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(require_investigator),
) -> CaseResponse:
    """
    Transition a case through its lifecycle status states.

    Permitted transitions:
      Active      → UnderReview, Closed
      UnderReview → Active (reopen), Archived, Closed
      Archived    → (terminal — no transitions)
      Closed      → (terminal — no transitions)

    The optional `reason` field is stored in the audit log.
    Supervisors can perform all transitions. Investigators can perform all
    transitions except Archived (only supervisors can archive).
    """
    from app.models.user import UserRole

    case = await _get_case_or_404(case_id, db)

    # Only supervisors can archive cases
    if (
        data.new_status == CaseStatus.Archived
        and current_user.role != UserRole.supervisor
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only supervisors can archive cases.",
        )

    try:
        service = CaseService(db)
        case = await service.transition_status(
            case=case,
            new_status=data.new_status,
            actor_id=current_user.user_id,
            reason=data.reason,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    await db.commit()
    await db.refresh(case)

    return CaseResponse.model_validate(case)


# ─── POST /cases/{case_id}/reassign ──────────────────────────────────────────

@router.post(
    "/{case_id}/reassign",
    response_model=CaseResponse,
    summary="Reassign lead investigator (supervisor only)",
    status_code=status.HTTP_200_OK,
)
async def reassign_investigator(
    case_id: str,
    data: InvestigatorReassign,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(require_supervisor),
) -> CaseResponse:
    """
    Reassign the lead investigator on a case.

    Supervisor-only. Cannot reassign Archived or Closed cases.
    The new investigator must have the 'investigator' or 'supervisor' role.
    The change and reason are recorded in the audit log.
    """
    case = await _get_case_or_404(case_id, db)

    user_service = UserService(db)
    new_investigator = await user_service.get_by_id(data.new_investigator_id)
    if new_investigator is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User '{data.new_investigator_id}' not found.",
        )

    try:
        service = CaseService(db)
        case = await service.reassign_investigator(
            case=case,
            new_investigator=new_investigator,
            actor_id=current_user.user_id,
            reason=data.reason,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    await db.commit()
    await db.refresh(case)

    return CaseResponse.model_validate(case)