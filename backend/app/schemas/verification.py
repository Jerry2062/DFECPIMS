"""
schemas/verification.py

Pydantic response schemas for the Hash Verification API.

Three possible verification outcomes:
  VERIFIED      — file exists, re-hash matches the stored SHA-256 exactly
  HASH_MISMATCH — file exists, but the re-hash does NOT match stored SHA-256
                  This is the worst outcome — it means the stored evidence
                  has been modified since ingestion. Treat as a security event.
  FILE_MISSING  — storage_path does not exist on disk
                  Could mean the file was deleted, moved, or the storage
                  volume was unmounted. Also a serious integrity failure.

VerificationResult is the per-item response.
BulkVerificationResult wraps multiple VerificationResults for the case-level
bulk verification endpoint.

The response always includes:
  - The original stored hash (ground truth)
  - The freshly computed hash (what's on disk right now)
  - The outcome as a clear enum string
  - The timestamp of this verification run
  - A human-readable verdict message

This gives the forensic analyst everything they need to document the
integrity check in their report without needing to run a second query.
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class VerificationOutcome(str, Enum):
    """
    The three possible outcomes of a hash verification run.
    String enum so it serializes cleanly to JSON without mapping.
    """
    VERIFIED = "VERIFIED"
    HASH_MISMATCH = "HASH_MISMATCH"
    FILE_MISSING = "FILE_MISSING"


class VerificationResult(BaseModel):
    """
    Result of a single evidence hash verification run.

    Always returned from:
      POST /cases/{case_id}/evidence/{evidence_id}/verify
    And included in bulk verification results.
    """
    evidence_id: str
    case_id: str
    filename: str

    outcome: VerificationOutcome = Field(
        description=(
            "VERIFIED: hash matches. "
            "HASH_MISMATCH: file has been modified. "
            "FILE_MISSING: file not found at storage path."
        )
    )

    stored_hash: str = Field(
        description="The SHA-256 hash computed and stored at ingestion time — ground truth"
    )
    computed_hash: Optional[str] = Field(
        default=None,
        description=(
            "The SHA-256 hash freshly computed from the file on disk. "
            "None if the file could not be read (FILE_MISSING outcome)."
        ),
    )

    verified_at: datetime = Field(
        description="UTC timestamp of this verification run"
    )

    verdict: str = Field(
        description="Human-readable summary of the verification outcome"
    )

    # Convenience boolean — lets the frontend show pass/fail without
    # checking the outcome enum string
    passed: bool = Field(
        description="True only if outcome is VERIFIED"
    )


class BulkVerificationSummary(BaseModel):
    """
    Summary of a bulk verification run across all evidence in a case.

    Returned from:
      POST /cases/{case_id}/verify-all
    """
    case_id: str
    total_items: int
    verified_count: int
    mismatch_count: int
    missing_count: int
    all_passed: bool = Field(
        description="True only if every evidence item passed verification"
    )
    verified_at: datetime = Field(
        description="UTC timestamp when the bulk run was initiated"
    )
    results: list[VerificationResult] = Field(
        description="Per-item verification results in acquisition order"
    )