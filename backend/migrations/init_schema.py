"""
migrations/init_schema.py

Manual migration script to create all DFECPIMS tables.

This is an alternative to Alembic for teams that prefer explicit control.
Run with: python migrations/init_schema.py

For Alembic users: the models are already defined in app/models/ and Alembic
can autogenerate from them. This script is provided as a standalone option
and as documentation of the full DDL intent.

After running this, execute:
  psql -d dfecpims -f migrations/audit_trigger.sql

to install the INSERT-only trigger on audit_log.
"""

import asyncio
import os

from sqlalchemy.ext.asyncio import create_async_engine

# Import Base and all models so SQLAlchemy's metadata is populated
from app.models import (  # noqa: F401 — side-effect imports to register models
    Base,
    User,
    Case,
    Evidence,
    AuditLog,
    CaseSequence,
)
from app.models.case_sequence import EvidenceSequence  # noqa: F401


DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://dfecpims:dfecpims@localhost:5432/dfecpims",
)


async def create_tables() -> None:
    """
    Creates all tables defined in the SQLAlchemy metadata.

    Uses `checkfirst=True` internally (via create_all) so it's safe to
    run multiple times — existing tables are not dropped or modified.
    """
    engine = create_async_engine(DATABASE_URL, echo=True)

    print("Creating DFECPIMS database schema...")
    async with engine.begin() as conn:
        # run_sync bridges the async connection to the sync create_all API
        await conn.run_sync(Base.metadata.create_all)

    await engine.dispose()
    print("\nSchema created successfully.")
    print("\nNext step: install the audit log trigger:")
    print("  psql -d dfecpims -f migrations/audit_trigger.sql")


async def seed_evidence_sequence() -> None:
    """
    Ensures the EvidenceSequence table has its single seed row (id=1, last=0).
    Safe to call multiple times — INSERT ... ON CONFLICT DO NOTHING.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from sqlalchemy import text

    engine = create_async_engine(DATABASE_URL, echo=False)
    AsyncSession = async_sessionmaker(bind=engine, expire_on_commit=False)

    async with AsyncSession() as session:
        await session.execute(
            text(
                "INSERT INTO evidence_sequence (id, last_sequence) VALUES (1, 0) "
                "ON CONFLICT (id) DO NOTHING"
            )
        )
        await session.commit()

    await engine.dispose()
    print("EvidenceSequence seed row ensured.")


if __name__ == "__main__":
    asyncio.run(create_tables())
    asyncio.run(seed_evidence_sequence())