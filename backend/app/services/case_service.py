"""
services/case_service.py

Business logic for Case Management in DFECPIMS.

Responsibilities:
  1. Generate CASE-YYYY-NNNN IDs atomically (SELECT FOR UPDATE on case_sequence)
  2. Create, read, update cases
  3. Enforce status transition rules
  4. Handle investigator reassignment
  5. Write audit log entries for every meaningful event

Status transition matrix:
  Active      → UnderReview, Closed
  UnderReview → Active (reopen), Archived, Closed
  Archived    → (terminal — no further transitions)
  Closed      → (terminal — no further transitions)

Archived and Closed are terminal states. A closed case stays closed.
If you need to reopen an archived case, that's a workflow decision for
your organization — this system does not permit it by default. Add an
explicit transition if your SOPs require it.

Concurrency note on ID generation:
  generate_case_id() uses SELECT ... FOR UPDATE to lock the sequence row
  for the current year. This prevents two simultaneous case-creation requests
  from getting the same sequence number. The lock is held until the surrounding
  transaction commits or rolls back.
"""

import math
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.case import Case, CaseSeverity, CaseStatus
from app.models.case_sequence import CaseSequence
from app.models.user import User
from app.schemas.case import CaseCreate, CaseListResponse, CaseSummary, CaseUpdate
from app.services.audit_service import AuditService


# ─── Status transition matrix ─────────────────────────────────────────────────

# Maps each status to the set of statuses it can legally transition to.
# Attempting any transition not in this map raises ValueError.
VALID_TRANSITIONS: dict[CaseStatus, set[CaseStatus]] = {
    CaseStatus.Active:      {CaseStatus.UnderReview, CaseStatus.Closed},
    CaseStatus.UnderReview: {CaseStatus.Active, CaseStatus.Archived, CaseStatus.Closed},
    CaseStatus.Archived:    set(),   # Terminal
    CaseStatus.Closed:      set(),   # Terminal
}


class CaseService:
    """
    Handles all case lifecycle operations.

    Usage:
        service = CaseService(db)
        case = await service.create_case(data, actor_id="uuid-string")
        await db.commit()
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.audit = AuditService(db)

    # ─── ID generation ────────────────────────────────────────────────────────

    async def generate_case_id(self) -> str:
        """
        Generate the next CASE-YYYY-NNNN identifier atomically.

        Uses SELECT ... FOR UPDATE to lock the sequence row for the current year,
        preventing duplicate IDs under concurrent requests. The lock is held
        until the surrounding transaction commits.

        If no sequence row exists for this year (first case of the year),
        one is created with last_sequence=1 and the ID CASE-YYYY-0001 is returned.

        Returns:
            A string like "CASE-2026-0001".
        """
        year = datetime.now(timezone.utc).year

        # with_for_update() maps to SELECT ... FOR UPDATE in Postgres.
        # This blocks other transactions trying to lock the same row until
        # we commit — the simplest correct approach for low-to-medium volume.
        result = await self.db.execute(
            select(CaseSequence)
            .where(CaseSequence.year == year)
            .with_for_update()
        )
        seq_row = result.scalar_one_or_none()

        if seq_row is None:
            # First case created this year — insert the sequence row
            seq_row = CaseSequence(year=year, last_sequence=1)
            self.db.add(seq_row)
            await self.db.flush()
            next_seq = 1
        else:
            next_seq = seq_row.last_sequence + 1
            seq_row.last_sequence = next_seq
            self.db.add(seq_row)
            await self.db.flush()

        # Zero-pad to 4 digits: CASE-2026-0001 through CASE-2026-9999
        # If you somehow exceed 9999 cases in one year, the format expands
        # gracefully (CASE-2026-10000) rather than breaking.
        return f"CASE-{year}-{next_seq:04d}"

    # ─── Create ───────────────────────────────────────────────────────────────

    async def create_case(
        self,
        data: CaseCreate,
        actor_id: str,
        actor_name: str,
    ) -> Case:
        """
        Create a new forensic case.

        If data.investigator_id is None, the case is assigned to the actor
        (the user making the request). Supervisors can supply a different
        investigator_id to assign to someone else.

        Args:
            data:       Validated CaseCreate schema.
            actor_id:   UUID of the user creating the case (from JWT).
            actor_name: Display name of the actor (for audit log detail).

        Returns:
            The newly created Case ORM object (flushed, not committed).
        """
        case_id = await self.generate_case_id()

        # Default to self-assignment if no investigator specified
        investigator_id = data.investigator_id or actor_id

        case = Case(
            id=case_id,
            title=data.title.strip(),
            description=data.description,
            severity=data.severity,
            status=CaseStatus.Active,
            investigator_id=investigator_id,
        )

        self.db.add(case)
        await self.db.flush()

        await self.audit.log(
            action="CASE_CREATED",
            actor_id=actor_id,
            case_id=case_id,
            detail={
                "title": case.title,
                "severity": case.severity.value,
                "investigator_id": investigator_id,
                "created_by": actor_name,
            },
        )

        return case

    # ─── Read ─────────────────────────────────────────────────────────────────

    async def get_by_id(self, case_id: str) -> Optional[Case]:
        """
        Fetch a single case by its CASE-YYYY-NNNN identifier.

        Returns None if not found.
        Does NOT write an audit log entry — reads are logged at the
        route handler level when the access is user-initiated (GET /cases/{id}).
        """
        result = await self.db.execute(
            select(Case).where(Case.id == case_id)
        )
        return result.scalar_one_or_none()

    async def list_cases(
        self,
        page: int = 1,
        page_size: int = 20,
        status_filter: Optional[CaseStatus] = None,
        severity_filter: Optional[CaseSeverity] = None,
        investigator_id_filter: Optional[str] = None,
        search: Optional[str] = None,
    ) -> CaseListResponse:
        """
        Return a paginated, optionally filtered list of cases.

        All filters are additive (AND logic). Unset filters are ignored.

        Args:
            page:                   1-indexed page number.
            page_size:              Results per page (capped at 100).
            status_filter:          Return only cases with this status.
            severity_filter:        Return only cases with this severity.
            investigator_id_filter: Return only cases assigned to this user.
            search:                 Case-insensitive substring match on title.

        Returns:
            CaseListResponse with items and pagination metadata.
        """
        page_size = min(page_size, 100)  # Hard cap — don't allow unbounded queries
        offset = (page - 1) * page_size

        # Build base query
        base_query = select(Case)

        # Apply filters
        if status_filter is not None:
            base_query = base_query.where(Case.status == status_filter)
        if severity_filter is not None:
            base_query = base_query.where(Case.severity == severity_filter)
        if investigator_id_filter is not None:
            base_query = base_query.where(Case.investigator_id == investigator_id_filter)
        if search:
            # ilike = case-insensitive LIKE in SQLAlchemy
            base_query = base_query.where(Case.title.ilike(f"%{search}%"))

        # Count total matching rows (for pagination metadata)
        count_result = await self.db.execute(
            select(func.count()).select_from(base_query.subquery())
        )
        total = count_result.scalar_one()

        # Fetch the page
        items_result = await self.db.execute(
            base_query
            .order_by(Case.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        cases = list(items_result.scalars().unique().all())

        # Build summary items with evidence count
        summary_items = []
        for case in cases:
            # Count evidence items without loading the full relationship
            ev_count_result = await self.db.execute(
                select(func.count()).select_from(
                    select(Case).where(Case.id == case.id)
                    .join(Case.evidence_items)
                    .subquery()
                )
            )
            evidence_count = ev_count_result.scalar_one_or_none() or 0

            summary_items.append(
                CaseSummary(
                    id=case.id,
                    title=case.title,
                    severity=case.severity,
                    status=case.status,
                    investigator=case.investigator,
                    evidence_count=evidence_count,
                    created_at=case.created_at,
                    updated_at=case.updated_at,
                )
            )

        total_pages = math.ceil(total / page_size) if total > 0 else 1

        return CaseListResponse(
            items=summary_items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )

    # ─── Update ───────────────────────────────────────────────────────────────

    async def update_case(
        self,
        case: Case,
        data: CaseUpdate,
        actor_id: str,
    ) -> Case:
        """
        Update mutable case fields (title, description, severity).

        Status and investigator changes are handled by their own methods.
        Only writes an audit log entry if at least one field actually changed.

        Args:
            case:     The Case ORM object to update (must be attached to session).
            data:     Validated CaseUpdate schema.
            actor_id: UUID of the user making the change.

        Returns:
            The updated Case ORM object (flushed, not committed).
        """
        changes: dict[str, dict] = {}

        if data.title is not None and data.title.strip() != case.title:
            changes["title"] = {"before": case.title, "after": data.title.strip()}
            case.title = data.title.strip()

        if data.description is not None and data.description != case.description:
            changes["description"] = {
                "before": case.description,
                "after": data.description,
            }
            case.description = data.description

        if data.severity is not None and data.severity != case.severity:
            changes["severity"] = {
                "before": case.severity.value,
                "after": data.severity.value,
            }
            case.severity = data.severity

        if not changes:
            # Nothing actually changed — skip the DB write and audit entry
            return case

        self.db.add(case)
        await self.db.flush()

        await self.audit.log(
            action="CASE_UPDATED",
            actor_id=actor_id,
            case_id=case.id,
            detail={"changes": changes},
        )

        return case

    # ─── Status transitions ───────────────────────────────────────────────────

    async def transition_status(
        self,
        case: Case,
        new_status: CaseStatus,
        actor_id: str,
        reason: Optional[str] = None,
    ) -> Case:
        """
        Transition a case to a new status, enforcing the transition matrix.

        Args:
            case:       The Case ORM object.
            new_status: The target status.
            actor_id:   UUID of the user requesting the transition.
            reason:     Optional explanation (stored in audit detail).

        Returns:
            The updated Case ORM object (flushed, not committed).

        Raises:
            ValueError: If the transition is not permitted from the current state.
        """
        current = case.status
        allowed = VALID_TRANSITIONS.get(current, set())

        if new_status == current:
            raise ValueError(
                f"Case is already in status '{current.value}'."
            )

        if new_status not in allowed:
            allowed_names = [s.value for s in allowed]
            raise ValueError(
                f"Cannot transition from '{current.value}' to '{new_status.value}'. "
                f"Permitted transitions from '{current.value}': "
                f"{allowed_names if allowed_names else ['none — this is a terminal state']}."
            )

        previous_status = case.status.value
        case.status = new_status
        self.db.add(case)
        await self.db.flush()

        await self.audit.log(
            action="CASE_STATUS_CHANGED",
            actor_id=actor_id,
            case_id=case.id,
            detail={
                "from": previous_status,
                "to": new_status.value,
                "reason": reason,
            },
        )

        return case

    # ─── Investigator reassignment ────────────────────────────────────────────

    async def reassign_investigator(
        self,
        case: Case,
        new_investigator: User,
        actor_id: str,
        reason: Optional[str] = None,
    ) -> Case:
        """
        Reassign the lead investigator on a case.

        Supervisor-only operation (enforced at the route level, not here).
        Cannot reassign a Closed or Archived case — those are terminal.

        Args:
            case:             The Case ORM object.
            new_investigator: The User ORM object of the new investigator.
            actor_id:         UUID of the supervisor making the change.
            reason:           Optional explanation for the change.

        Returns:
            The updated Case ORM object (flushed, not committed).

        Raises:
            ValueError: If the case is in a terminal state, or if the new
                        investigator doesn't have an appropriate role.
        """
        from app.models.user import UserRole

        # Terminal cases should not be modified
        if case.status in {CaseStatus.Archived, CaseStatus.Closed}:
            raise ValueError(
                f"Cannot reassign investigator on a '{case.status.value}' case. "
                "Terminal cases are immutable."
            )

        # Only investigators and supervisors should be assigned cases
        if new_investigator.role == UserRole.readonly:
            raise ValueError(
                f"Cannot assign case to user '{new_investigator.email}' — "
                "readonly users cannot be lead investigators."
            )

        previous_id = case.investigator_id
        case.investigator_id = new_investigator.id
        self.db.add(case)
        await self.db.flush()

        await self.audit.log(
            action="CASE_INVESTIGATOR_REASSIGNED",
            actor_id=actor_id,
            case_id=case.id,
            detail={
                "from_investigator_id": previous_id,
                "to_investigator_id": new_investigator.id,
                "to_investigator_name": new_investigator.name,
                "reason": reason,
            },
        )

        return case