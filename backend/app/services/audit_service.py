"""
services/audit_service.py

Shared audit log writer used by all service modules.

The _log_event pattern was first written inside AuthService. Rather than
copying it into CaseService, EvidenceService, and every future service,
it lives here as a standalone utility that any service can call.

Design:
  - AuditService is not a standalone FastAPI dependency — it doesn't own
    a session. It takes one at construction time, same as other services.
  - It never commits. The calling service controls transaction boundaries.
  - Every write is a flush() so the row gets an ID immediately and is
    visible within the transaction, even before the commit.

Action code conventions (UPPER_SNAKE_CASE):
  Auth:      USER_LOGIN, USER_LOGIN_FAILED, USER_REGISTERED, PASSWORD_CHANGED
  Cases:     CASE_CREATED, CASE_UPDATED, CASE_STATUS_CHANGED,
             CASE_INVESTIGATOR_REASSIGNED, CASE_ACCESSED
  Evidence:  EVIDENCE_UPLOADED, EVIDENCE_ACCESSED
  Hashing:   HASH_VERIFIED, HASH_FAILED
  Reports:   REPORT_EXPORTED
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog


class AuditService:
    """
    Writes append-only audit log entries.

    Usage:
        audit = AuditService(db)
        await audit.log(
            action="CASE_CREATED",
            actor_id=current_user.user_id,
            case_id=case.id,
            detail={"title": case.title, "severity": case.severity},
        )
        # Then commit in the calling service or route handler.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def log(
        self,
        action: str,
        actor_id: Optional[str],
        case_id: Optional[str] = None,
        detail: Optional[dict[str, Any]] = None,
    ) -> AuditLog:
        """
        Write a single audit log entry.

        Args:
            action:   UPPER_SNAKE_CASE action code. See module docstring for conventions.
            actor_id: UUID of the user performing the action. Nullable for system events.
            case_id:  Case ID this event relates to. Nullable for non-case events.
            detail:   Dict of contextual data. Will be JSON-serialized for storage.
                      Keep this small — it's for human reviewers, not machine parsing.

        Returns:
            The AuditLog ORM object (flushed but not committed).
        """
        entry = AuditLog(
            id=str(uuid.uuid4()),
            case_id=case_id,
            actor_id=actor_id,
            action=action,
            # Serialize detail dict to JSON string, or None if nothing provided.
            detail=json.dumps(detail) if detail is not None else None,
            timestamp=datetime.now(timezone.utc),
        )

        self.db.add(entry)
        # flush() assigns the row an ID and makes it visible within this
        # transaction without committing. Caller decides when to commit.
        await self.db.flush()

        return entry