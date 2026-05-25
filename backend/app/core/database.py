"""
core/database.py

Database engine and session factory for DFECPIMS.

Uses SQLAlchemy's async engine (asyncpg driver) so FastAPI route handlers
can be fully async without blocking the event loop during DB I/O.

Session lifecycle:
  - Each request gets its own session via `get_db` dependency injection.
  - Sessions are committed by the route handler (or service layer).
  - Rollback happens automatically on exception within the `async with` block.
  - Sessions are always closed at the end of the request, even on error.

Environment variables expected:
  DATABASE_URL  — full async DSN, e.g.:
                  postgresql+asyncpg://user:pass@localhost:5432/dfecpims
"""

import os
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# --- Engine ---

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://dfecpims:dfecpims@localhost:5432/dfecpims",
)

engine = create_async_engine(
    DATABASE_URL,
    # pool_pre_ping sends a lightweight "SELECT 1" before handing out a
    # connection, so stale connections from the pool don't surface as errors.
    pool_pre_ping=True,
    # echo=True logs all SQL to stdout — handy in dev, disable in prod.
    echo=os.environ.get("SQL_ECHO", "false").lower() == "true",
    # Reasonable defaults; tune for your expected load.
    pool_size=10,
    max_overflow=20,
)

# --- Session factory ---

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    # expire_on_commit=False means ORM objects remain usable after commit
    # without triggering lazy loads. Important in async context because
    # lazy loads would need another await, and we'd need the session still open.
)


# --- Dependency ---

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that yields a database session per request.

    Usage in route handlers:
        @router.get("/cases")
        async def list_cases(db: AsyncSession = Depends(get_db)):
            ...

    The session is automatically closed (and rolled back on error)
    after the route handler returns, via the try/finally block.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()