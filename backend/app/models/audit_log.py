"""
models/audit_log.py

AuditLog model — the forensic paper trail for every meaningful event in DFECPIMS.

CRITICAL DESIGN NOTES:
  1. This table is INSERT-ONLY at the database level.
     A PostgreSQL trigger (defined in migrations/audit_trigger.sql) raises an
     exception if any UPDATE or DELETE is attempted — from any user, including
     the DB owner. Application-level code must never attempt to modify rows.

  2. The `action` field uses short, machine-readable strings (UPPERCASE_SNAKE_CASE)
     so that audit logs can be filtered/grouped programmatically:
       CASE_CREATED, CASE_STATUS_CHANGED, EVIDENCE_UPLOADED, EVIDENCE_ACCESSED,
       HASH_VERIFIED, HASH_FAILED, REPORT_EXPORTED, USER_LOGIN, etc.

  3. `detail` is free-form JSON-serializable text. The convention is to store
     a JSON string with before/after values for changes, or contextual metadata
     for access events. This keeps the schema simple without losing information.

  4. AuditLog rows have no `updated_at` — they are immutable by definition.

  5. The `case_id` FK is nullable because some events (e.g. USER_LOGIN) are
     not scoped to a specific case.
"""

from datetime import datetime, timezone

from sqlalchemy import Column, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship

from .base import Base


class AuditLog(Base):
    """
    Append-only audit log entry.

    Never attempt to UPDATE or DELETE rows from this table —
    the database-level trigger will reject it and raise an exception.
    """

    __tablename__ = "audit_log"

    id = Column(
        String(36),
        primary_key=True,
        comment="UUID v4, generated at application layer",
    )

    case_id = Column(
        String(20),
        ForeignKey("cases.id", ondelete="SET NULL"),
        nullable=True,  # System-wide events (login, etc.) have no case scope
        index=True,
        comment="FK to cases.id; NULL for system-level events not tied to a case",
    )

    actor_id = Column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,  # Nullable so log survives even if user is deleted
        index=True,
        comment="FK to users.id — the user who triggered this event",
    )

    action = Column(
        String(100),
        nullable=False,
        index=True,
        comment="Short machine-readable action code, e.g. CASE_CREATED, HASH_VERIFIED",
    )

    detail = Column(
        Text,
        nullable=True,
        comment="JSON string with additional context: before/after values, metadata, etc.",
    )

    timestamp = Column(
        DateTime(timezone=True),
        nullable=False,
        # Always UTC. Set at application layer so we have a Python-side
        # datetime object immediately — useful for returning in API responses
        # without a round-trip to the DB.
        default=lambda: datetime.now(timezone.utc),
        index=True,
        comment="UTC timestamp when this event occurred",
    )

    # --- Relationships ---

    case = relationship(
        "Case",
        back_populates="audit_logs",
        foreign_keys=[case_id],
        lazy="select",
    )

    actor = relationship(
        "User",
        back_populates="audit_entries",
        foreign_keys=[actor_id],
        lazy="joined",  # Almost always needed to display "who did this"
    )

    def __repr__(self) -> str:
        return (
            f"<AuditLog id={self.id!r} action={self.action!r} "
            f"actor_id={self.actor_id!r} timestamp={self.timestamp!r}>"
        )