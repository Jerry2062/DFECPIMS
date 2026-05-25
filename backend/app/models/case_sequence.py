"""
models/case_sequence.py

CaseSequence — helper table for generating CASE-YYYY-NNNN IDs.

Why not use a Postgres SEQUENCE?
  Postgres sequences are fast and gap-tolerant, but a bare sequence gives us
  a global counter. We need a per-year counter that resets to 1 each January.
  A single-row-per-year table achieves this cleanly. The counter is incremented
  inside a transaction with SELECT ... FOR UPDATE to prevent race conditions
  under concurrent case creation.

  If you're running a high-throughput system (hundreds of cases per second),
  swap this for a Postgres SEQUENCE per year — but honestly, that's not a
  realistic scenario for a forensic evidence system.

Similarly, EvidenceSequence handles EV-NNN IDs (global, no reset).
We keep it in this file because it's tiny.
"""

from sqlalchemy import Column, Integer, String
from .base import Base


class CaseSequence(Base):
    """
    Tracks the current sequence number for case IDs, keyed by year.

    Example rows:
      year=2024, last_sequence=42   → next ID will be CASE-2024-0043
      year=2025, last_sequence=7    → next ID will be CASE-2025-0008
    """

    __tablename__ = "case_sequence"

    year = Column(
        Integer,
        primary_key=True,
        comment="Calendar year this sequence counter applies to",
    )

    last_sequence = Column(
        Integer,
        nullable=False,
        default=0,
        comment="The highest sequence number issued for this year; starts at 0",
    )

    def __repr__(self) -> str:
        return f"<CaseSequence year={self.year} last={self.last_sequence}>"


class EvidenceSequence(Base):
    """
    Global sequence counter for evidence IDs (EV-NNN).

    Single row, always id=1. Incremented atomically using SELECT FOR UPDATE.
    No year-based reset — evidence IDs are globally unique and never reused.
    """

    __tablename__ = "evidence_sequence"

    id = Column(
        Integer,
        primary_key=True,
        default=1,
        comment="Always 1 — this is a single-row counter table",
    )

    last_sequence = Column(
        Integer,
        nullable=False,
        default=0,
        comment="The highest EV-NNN sequence number issued",
    )

    def __repr__(self) -> str:
        return f"<EvidenceSequence last={self.last_sequence}>"