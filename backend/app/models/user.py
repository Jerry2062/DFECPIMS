"""
models/user.py

User model for DFECPIMS authentication and role management.

Roles:
  - investigator  : can create cases, upload evidence, run verifications
  - supervisor    : all investigator rights + can archive/close cases, export reports
  - readonly      : read-only access to cases and evidence; cannot write anything

Passwords are stored as bcrypt hashes via passlib (never plaintext).
The model itself doesn't handle hashing — that lives in the auth service —
but the field name `hashed_password` signals intent clearly.
"""

import enum
from datetime import datetime, timezone

from sqlalchemy import Column, String, Enum as SAEnum, DateTime
from sqlalchemy.orm import relationship

from .base import Base


class UserRole(str, enum.Enum):
    """
    String enum so role values serialize naturally to/from JSON
    and can be embedded directly in JWT payloads without extra mapping.
    """
    investigator = "investigator"
    supervisor = "supervisor"
    readonly = "readonly"


class User(Base):
    """
    Represents an authenticated system user.

    Relationships:
      - cases_assigned : cases where this user is the lead investigator
      - evidence_acquired : evidence items collected by this user
      - audit_entries : audit log rows where this user was the actor
    """

    __tablename__ = "users"

    id = Column(
        String(36),
        primary_key=True,
        # UUIDs stored as strings for cross-DB portability.
        # Generated at application layer (uuid.uuid4()) before insert.
        comment="UUID v4, generated at application layer",
    )

    name = Column(
        String(255),
        nullable=False,
        comment="Full display name of the user",
    )

    email = Column(
        String(255),
        nullable=False,
        unique=True,
        index=True,
        comment="Login email — must be unique across the system",
    )

    hashed_password = Column(
        String(255),
        nullable=False,
        comment="bcrypt hash of the user's password, never plaintext",
    )

    role = Column(
        SAEnum(UserRole, name="user_role", create_type=True),
        nullable=False,
        default=UserRole.readonly,
        comment="System role controlling what this user is permitted to do",
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        # We set this at the application layer rather than relying on
        # server_default so that it's always UTC-aware in Python too.
        default=lambda: datetime.now(timezone.utc),
        comment="UTC timestamp of account creation",
    )

    # --- Relationships ---

    cases_assigned = relationship(
        "Case",
        back_populates="investigator",
        foreign_keys="Case.investigator_id",
        lazy="select",
    )

    evidence_acquired = relationship(
        "Evidence",
        back_populates="acquired_by_user",
        foreign_keys="Evidence.acquired_by",
        lazy="select",
    )

    audit_entries = relationship(
        "AuditLog",
        back_populates="actor",
        foreign_keys="AuditLog.actor_id",
        lazy="select",
    )

    def __repr__(self) -> str:
        return f"<User id={self.id!r} email={self.email!r} role={self.role!r}>"