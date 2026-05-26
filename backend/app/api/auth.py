"""
api/auth.py

Authentication route handlers for DFECPIMS.

Endpoints:
  POST /auth/login            — open (no auth required)
  POST /auth/register         — supervisor only
  GET  /auth/me               — any authenticated user
  POST /auth/change-password  — any authenticated user (self-service)
  GET  /auth/users            — supervisor only (user list)

All route handlers are intentionally thin:
  - Parse and validate input (Pydantic handles this)
  - Call the appropriate service method
  - Return the appropriate response schema

Business logic lives in the service layer, not here.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import (
    get_current_user,
    require_supervisor,
)
from app.core.security import TokenPayload, verify_password
from app.schemas.auth import (
    LoginRequest,
    MeResponse,
    PasswordChangeRequest,
    TokenResponse,
    UserCreate,
    UserResponse,
)
from app.services.auth_service import AuthService
from app.services.user_service import UserService


router = APIRouter(prefix="/auth", tags=["Authentication"])


# ─── POST /auth/login ─────────────────────────────────────────────────────────

@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Authenticate and receive a JWT token",
    status_code=status.HTTP_200_OK,
)
async def login(
    data: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """
    Authenticate with email and password.

    Returns a JWT Bearer token. Include it in subsequent requests as:
        Authorization: Bearer <token>

    The token expires after the configured duration (default: 8 hours).
    Login failures are written to the audit log regardless of reason.
    """
    # Extract client IP for audit log context (best-effort — may be None
    # behind some proxies; that's acceptable for a forensic audit trail).
    client_ip: Optional[str] = request.client.host if request.client else None

    service = AuthService(db)

    try:
        return await service.login(data, client_ip=client_ip)
    except ValueError as e:
        # ValueError from the service means invalid credentials.
        # Return 401, not 422 (which Pydantic raises for bad request format).
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},
        )


# ─── POST /auth/register ──────────────────────────────────────────────────────

@router.post(
    "/register",
    response_model=UserResponse,
    summary="Create a new user account (supervisor only)",
    status_code=status.HTTP_201_CREATED,
)
async def register(
    data: UserCreate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(require_supervisor),
) -> UserResponse:
    """
    Create a new DFECPIMS user account.

    Restricted to supervisors — this is not a self-signup endpoint.
    A supervisor specifies the new user's role at creation time.

    Returns the created user record (without the password hash, obviously).
    """
    service = UserService(db)

    try:
        user = await service.create_user(data)
        await db.commit()
        await db.refresh(user)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )

    return UserResponse.model_validate(user)


# ─── GET /auth/me ─────────────────────────────────────────────────────────────

@router.get(
    "/me",
    response_model=MeResponse,
    summary="Return the currently authenticated user's identity",
    status_code=status.HTTP_200_OK,
)
async def get_me(
    current_user: TokenPayload = Depends(get_current_user),
) -> MeResponse:
    """
    Return identity information for the currently authenticated user.

    This endpoint reads from the JWT payload — no database query.
    Useful for the frontend to display the logged-in user's name and role
    after a page refresh without requiring a separate /users/me DB hit.
    """
    return MeResponse(
        user_id=current_user.user_id,
        name=current_user.name,
        role=current_user.role,
    )


# ─── POST /auth/change-password ───────────────────────────────────────────────

@router.post(
    "/change-password",
    summary="Change your own password",
    status_code=status.HTTP_200_OK,
)
async def change_password(
    data: PasswordChangeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> dict:
    """
    Change the authenticated user's password.

    Requires the current password for verification before accepting
    the new one. This prevents a stolen token from being used to
    lock the real user out of their account.
    """
    user_service = UserService(db)
    user = await user_service.get_by_id(current_user.user_id)

    if user is None:
        # Shouldn't happen if the token is valid, but be safe
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    # Verify the current password before accepting the new one
    if not verify_password(data.current_password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect.",
        )

    await user_service.update_password(user, data.new_password)
    await db.commit()

    return {"message": "Password updated successfully."}


# ─── GET /auth/users ──────────────────────────────────────────────────────────

@router.get(
    "/users",
    response_model=list[UserResponse],
    summary="List all user accounts (supervisor only)",
    status_code=status.HTTP_200_OK,
)
async def list_users(
    db: AsyncSession = Depends(get_db),
    _: TokenPayload = Depends(require_supervisor),
) -> list[UserResponse]:
    """
    Return a list of all registered users.

    Supervisor-only. Useful for account management and auditing who
    has access to the system.
    """
    service = UserService(db)
    users = await service.list_users()
    return [UserResponse.model_validate(u) for u in users]