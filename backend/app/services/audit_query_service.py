"""
services/audit_query_service.py

Read-only query interface for the audit log.

This is intentionally separate from AuditService (which only writes).
Keeping reads and writes in separate classes makes the boundary explicit:
  AuditService       → INSERT only, used internally by other services
  AuditQueryService  → SELECT only, used by the audit API endpoints

Query capabilities:
  - Filter by case_id (case-scoped view)
  - Filter by actor_id (what did this user do?)
  - Filter by action code (show me all HASH_FAILED events)
  - Filter by time range (from_timestamp / to_timestamp)
  - Paginate results (mandatory — audit logs can be very long)
  - All filters combine with AND logic

The result rows are returned as AuditLogEntry Pydantic objects with:
  - detail deserialized from JSON string to dict
  - actor_name populated from the joined User relationship (or None if
    the user was deleted — SET NULL FK handles that gracefully)

Ordering is always timestamp DESC (most recent first) for the live
audit view, and timestamp ASC for the PDF report chain-of-custody section.
The ordering direction is a parameter so both use cases are served here.
"""

import math
from datetime import datetime
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models.audit_log import AuditLog
from app.models.user import User
from app.schemas.audit import AuditLogEntry, AuditLogListResponse


class AuditQueryService:
    """
    Read-only query interface for audit log entries.

    Usage:
        service = AuditQueryService(db)

        # Case-scoped, most recent first
        result = await service.query(
            case_id="CASE-2026-0001",
            page=1,
            page_size=50,
        )

        # System-wide, filtered by action, oldest first (for PDF report)
        result = await service.query(
            action="HASH_FAILED",
            ascending=True,
        )
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def query(
        self,
        case_id: Optional[str] = None,
        actor_id: Optional[str] = None,
        action: Optional[str] = None,
        from_timestamp: Optional[datetime] = None,
        to_timestamp: Optional[datetime] = None,
        page: int = 1,
        page_size: int = 50,
        ascending: bool = False,
    ) -> AuditLogListResponse:
        """
        Query audit log entries with optional filters and pagination.

        All filter parameters are optional and combine with AND logic.
        Returns an AuditLogListResponse with deserialized detail dicts
        and actor names resolved from the joined User relationship.

        Args:
            case_id:        Return only entries for this case.
                            Pass None for system-wide query.
            actor_id:       Return only entries where this user was the actor.
            action:         Return only entries with this exact action code.
                            Case-sensitive — use UPPER_SNAKE_CASE exactly.
            from_timestamp: Return only entries at or after this UTC timestamp.
            to_timestamp:   Return only entries at or before this UTC timestamp.
            page:           1-indexed page number.
            page_size:      Results per page. Hard-capped at 200.
            ascending:      If True, sort oldest-first (for PDF reports).
                            Default False = newest-first (for live audit view).

        Returns:
            AuditLogListResponse with items and pagination metadata.
        """
        page_size = min(page_size, 200)
        offset = (page - 1) * page_size

        # Build base query with actor join for name resolution.
        # joinedload ensures actor data comes in the same query rather than
        # triggering a lazy load for each row — critical for list endpoints.
        base_query = (
            select(AuditLog)
            .options(joinedload(AuditLog.actor))
        )

        # Apply filters
        if case_id is not None:
            base_query = base_query.where(AuditLog.case_id == case_id)

        if actor_id is not None:
            base_query = base_query.where(AuditLog.actor_id == actor_id)

        if action is not None:
            # Exact match on action code. For prefix matching (e.g. all CASE_* events),
            # callers can use action="CASE_" with a future startswith filter — not
            # implemented here to keep the interface simple and avoid SQL injection
            # risks from partial LIKE patterns passed directly from query params.
            base_query = base_query.where(AuditLog.action == action)

        if from_timestamp is not None:
            base_query = base_query.where(AuditLog.timestamp >= from_timestamp)

        if to_timestamp is not None:
            base_query = base_query.where(AuditLog.timestamp <= to_timestamp)

        # Count total matching rows (before pagination)
        count_result = await self.db.execute(
            select(func.count()).select_from(base_query.subquery())
        )
        total = count_result.scalar_one()

        # Apply ordering
        if ascending:
            ordered_query = base_query.order_by(AuditLog.timestamp.asc())
        else:
            ordered_query = base_query.order_by(AuditLog.timestamp.desc())

        # Fetch the page
        result = await self.db.execute(
            ordered_query.offset(offset).limit(page_size)
        )
        entries = list(result.scalars().unique().all())

        # Build response objects — resolve actor_name from relationship
        items = []
        for entry in entries:
            # entry.actor is the joined User ORM object (or None if user was deleted)
            actor_name = entry.actor.name if entry.actor else None

            items.append(
                AuditLogEntry(
                    id=entry.id,
                    case_id=entry.case_id,
                    actor_id=entry.actor_id,
                    actor_name=actor_name,
                    action=entry.action,
                    detail=entry.detail,   # deserialized by AuditLogEntry validator
                    timestamp=entry.timestamp,
                )
            )

        total_pages = math.ceil(total / page_size) if total > 0 else 1

        return AuditLogListResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
            case_id=case_id,
        )

    async def get_all_for_case(
        self,
        case_id: str,
        ascending: bool = True,
    ) -> list[AuditLogEntry]:
        """
        Return ALL audit entries for a case without pagination.

        Used by the PDF report generator (Module 7) which needs the complete
        audit trail in chronological order to render the chain-of-custody section.

        Do NOT expose this through the API directly — it has no size bound.
        Only call it from trusted internal code (report generation).

        Args:
            case_id:   The case to fetch entries for.
            ascending: Ordering direction. True = oldest-first (default for reports).

        Returns:
            Complete list of AuditLogEntry objects for the case.
        """
        order_col = AuditLog.timestamp.asc() if ascending else AuditLog.timestamp.desc()

        result = await self.db.execute(
            select(AuditLog)
            .options(joinedload(AuditLog.actor))
            .where(AuditLog.case_id == case_id)
            .order_by(order_col)
        )
        entries = list(result.scalars().unique().all())

        items = []
        for entry in entries:
            actor_name = entry.actor.name if entry.actor else None
            items.append(
                AuditLogEntry(
                    id=entry.id,
                    case_id=entry.case_id,
                    actor_id=entry.actor_id,
                    actor_name=actor_name,
                    action=entry.action,
                    detail=entry.detail,
                    timestamp=entry.timestamp,
                )
            )

        return items