"""
models/evidence.py

Evidence model — represents a single piece of digital evidence attached to a case.

SHA-256 hash is computed server-side at the moment of file ingestion.
It is never modified after initial storage; the hash is the ground truth
for chain-of-custody integrity.

`last_verified_at` is updated each time the hash verification endpoint runs.
`integrity_status` reflects the result of the most recent verification:
  - pending  : never been verified since ingestion
  - verified : last check passed (hash matched)
  - failed   : last check detected mismatch — evidence may be compromised

Evidence IDs follow EV-NNN (zero-padded, globally unique within the system).
Like case IDs, these are generated at the application layer.

`write_blocker_used` is a boolean flag — it's the investigator's declaration
that a hardware or software write blocker was in place during acquisition.
It doesn't technically prevent writes (we can't enforce that), but it is a
required chain-of-custody field in most forensic standards (ACPO, SWGDE).
"""

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    String,
    Text,
    BigInteger,
    Boolean,
    Enum as SAEnum,
    DateTime,
    ForeignKey,
)
from sqlalchemy.orm import relationship

from .base import Base


class IntegrityStatus(str, enum.Enum):
    pending = "pending"
    verified = "verified"
    failed = "failed"


class Evidence(Base):
    """
    A single digital evidence item within a case.

    Constraints:
      - sha256_hash is set once at ingestion and should never be updated
        (this is enforced at the service layer, not DB-level)
      - storage_path is the server-side path where the file was written
        after upload; it must exist for hash verification to work
    """

    __tablename__ = "evidence"

    id = Column(
        String(10),
        primary_key=True,
        comment="Human-readable evidence ID in EV-NNN format",
    )

    case_id = Column(
        String(20),
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="FK to cases.id — the case this evidence belongs to",
    )

    filename = Column(
        String(500),
        nullable=False,
        comment="Original filename as uploaded by the investigator",
    )

    file_type = Column(
        String(100),
        nullable=True,
        comment="MIME type detected server-side (e.g. image/jpeg, application/pdf)",
    )

    file_size_bytes = Column(
        BigInteger,
        nullable=False,
        comment="File size in bytes, recorded at ingestion time",
    )

    storage_path = Column(
        String(1000),
        nullable=False,
        comment="Absolute server-side path where the file is stored",
    )

    sha256_hash = Column(
        String(64),
        nullable=False,
        comment="SHA-256 hex digest computed server-side at ingestion — do not modify",
    )

    acquired_by = Column(
        String(36),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        comment="FK to users.id — who collected/uploaded this evidence",
    )

    acquired_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        comment="UTC timestamp when this evidence was ingested into the system",
    )

    location_description = Column(
        String(1000),
        nullable=True,
        comment="Physical or logical location where evidence was acquired (free text)",
    )

    notes = Column(
        Text,
        nullable=True,
        comment="Additional investigator notes about this evidence item",
    )

    write_blocker_used = Column(
        Boolean,
        nullable=False,
        default=False,
        comment="True if investigator declares a write blocker was used during acquisition",
    )

    last_verified_at = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="UTC timestamp of the most recent hash verification run; NULL if never verified",
    )

    integrity_status = Column(
        SAEnum(IntegrityStatus, name="integrity_status", create_type=True),
        nullable=False,
        default=IntegrityStatus.pending,
        comment="Result of the most recent hash verification",
    )

    # --- Relationships ---

    case = relationship(
        "Case",
        back_populates="evidence_items",
        foreign_keys=[case_id],
        lazy="joined",
    )

    acquired_by_user = relationship(
        "User",
        back_populates="evidence_acquired",
        foreign_keys=[acquired_by],
        lazy="joined",
    )

    def __repr__(self) -> str:
        return (
            f"<Evidence id={self.id!r} filename={self.filename!r} "
            f"integrity={self.integrity_status!r}>"
        )