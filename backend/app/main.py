"""
app/main.py

FastAPI application entry point for DFECPIMS.

This file:
  1. Creates the FastAPI app instance
  2. Configures CORS middleware
  3. Registers all API routers
  4. Provides a startup event to verify DB connectivity
  5. Exposes a health check endpoint

Running locally:
  uvicorn app.main:app --reload --port 8000

Production:
  uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import router as auth_router
from app.core.database import engine


# ─── Lifespan (startup / shutdown) ───────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Runs startup checks before the app begins accepting requests,
    and handles cleanup on shutdown.

    On startup:
      - Verifies database connectivity by running a lightweight query.
        If the DB is unreachable, the app refuses to start rather than
        serving requests that will all fail with 500 errors.

    On shutdown:
      - Disposes the connection pool cleanly.
    """
    # Startup
    from sqlalchemy import text
    from app.core.database import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        print("✓ Database connection verified.")
    except Exception as e:
        # Log and re-raise — don't silently start with a broken DB
        print(f"✗ Database connection failed: {e}")
        raise

    yield  # App runs here

    # Shutdown
    await engine.dispose()
    print("✓ Database connection pool disposed.")


# ─── App instance ─────────────────────────────────────────────────────────────

app = FastAPI(
    title="DFECPIMS",
    description=(
        "Digital Forensics Evidence Collection and Process Integrity Management System. "
        "Provides authenticated, role-based access to case management, evidence ingestion "
        "with SHA-256 hash verification, append-only audit logging, and PDF chain-of-custody reports."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)


# ─── CORS ─────────────────────────────────────────────────────────────────────

# Parse allowed origins from environment variable (comma-separated list).
# In production, this should be set to your frontend's domain only.
_cors_origins_raw = os.environ.get(
    "CORS_ORIGINS",
    "http://localhost:5173,http://localhost:3000",
)
cors_origins = [o.strip() for o in _cors_origins_raw.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,          # Required for Bearer token in Authorization header
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Routers ──────────────────────────────────────────────────────────────────

# Prefix all API routes with /api/v1 so we have room to version later.
API_PREFIX = "/api/v1"

app.include_router(auth_router, prefix=API_PREFIX)
# Remaining routers (cases, evidence, audit, reports) will be registered
# here as each module is built:
#   app.include_router(cases_router, prefix=API_PREFIX)
#   app.include_router(evidence_router, prefix=API_PREFIX)
#   app.include_router(audit_router, prefix=API_PREFIX)
#   app.include_router(reports_router, prefix=API_PREFIX)


# ─── Health check ─────────────────────────────────────────────────────────────

@app.get("/health", tags=["System"], summary="Health check")
async def health_check() -> dict:
    """
    Lightweight health check endpoint.

    Returns 200 if the API is running. Does NOT check DB connectivity —
    that runs at startup. Load balancers / monitoring tools can poll this.
    """
    return {"status": "ok", "service": "DFECPIMS"}