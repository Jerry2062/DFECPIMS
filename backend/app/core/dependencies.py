"""
core/dependencies.py

FastAPI dependency functions for authentication and role-based access control.

These are injected into route handlers via `Depends()`. They handle the full
auth chain: extract Bearer token → decode JWT → validate role → return user.

Three guard levels:
  get_current_user      — any valid JWT (all authenticated users)
  require_investigator  — investigator or supervisor
  require_supervisor    — supervisor only

Usage in route handlers:
  from app.core.dependencies import get_current_user, require_investigator, require_supervisor

  @router.get("/cases")
  async def list_cases(current_user: TokenPayload = Depends(get_current_user)):
      ...

  @router.post("/cases")
  async def create_case(current_user: TokenPayload = Depends(require_investigator)):
      ...

  @router.delete("/cases/{id}/archive")
  async def archive_case(current_user: TokenPayload = Depends(require_supervisor)):
      ...

The TokenPayload returned by all guards contains:
  .user_id  — str UUID of the authenticated user
  .role     — UserRole enum value
  .name     — display name (for audit log attribution without a DB query)
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError

from app.core.security import TokenPayload, decode_access_token
from app.models.user import UserRole


# HTTPBearer extracts the token from the "Authorization: Bearer <token>" header.
# auto_error=True means FastAPI returns 403 automatically if the header is absent
# or malformed — we don't need to handle that case ourselves.
bearer_scheme = HTTPBearer(auto_error=True)


# ─── Base dependency ──────────────────────────────────────────────────────────

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> TokenPayload:
    """
    FastAPI dependency: validates the Bearer JWT and returns the token payload.

    This is the base authentication dependency — it only checks that the token
    is valid and not expired. It does NOT enforce a specific role.

    Raises HTTP 401 if:
      - The token is missing (handled by HTTPBearer before we're called)
      - The token is expired
      - The token signature is invalid
      - Required claims (sub, role) are missing
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials. Token may be missing, expired, or invalid.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        token_payload = decode_access_token(credentials.credentials)
    except (JWTError, ValueError):
        # Both JWTError (bad/expired token) and ValueError (invalid role in payload)
        # should surface as 401, not 500. Don't leak the internal exception.
        raise credentials_exception

    return token_payload


# ─── Role guards ──────────────────────────────────────────────────────────────

async def require_investigator(
    current_user: TokenPayload = Depends(get_current_user),
) -> TokenPayload:
    """
    FastAPI dependency: requires investigator or supervisor role.

    Use this on routes that create or modify data: case creation, evidence
    upload, hash verification runs, etc.

    A supervisor passes this guard too — supervisors have all investigator
    permissions plus their own (archiving, closing cases, report export).

    Raises HTTP 403 if the authenticated user is 'readonly'.
    """
    allowed_roles = {UserRole.investigator, UserRole.supervisor}

    if current_user.role not in allowed_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Access denied. Role '{current_user.role.value}' cannot perform "
                "this action. Required: investigator or supervisor."
            ),
        )

    return current_user


async def require_supervisor(
    current_user: TokenPayload = Depends(get_current_user),
) -> TokenPayload:
    """
    FastAPI dependency: requires supervisor role exclusively.

    Use this on routes with elevated authority:
      - Creating or deactivating user accounts
      - Archiving or closing cases
      - Exporting PDF chain-of-custody reports

    Raises HTTP 403 if the authenticated user is investigator or readonly.
    """
    if current_user.role != UserRole.supervisor:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Access denied. Role '{current_user.role.value}' is not permitted "
                "here. Required: supervisor."
            ),
        )

    return current_user