"""
schemas/auth.py

Pydantic request and response schemas for the authentication API.

These are the shapes of JSON bodies and responses — not database models.
Pydantic validates incoming data (types, constraints) and serializes
outgoing data cleanly, stripping fields like hashed_password automatically.

Naming convention:
  <Entity>Create   — fields needed to create a resource (POST body)
  <Entity>Response — fields returned to the client (never includes secrets)
  <Entity>Login    — login-specific request body
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.models.user import UserRole


# ─── Login ────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    """
    POST /auth/login request body.

    Standard email + password. We use EmailStr so Pydantic validates
    the email format before we even touch the database.
    """
    email: EmailStr
    password: str = Field(min_length=1, description="User's plaintext password")


class TokenResponse(BaseModel):
    """
    POST /auth/login response body.

    Returns the JWT and enough metadata for the frontend to:
      1. Store the token for subsequent requests
      2. Display the user's name and role without an extra /me call
      3. Know when to redirect the user back to login (expires_at)
    """
    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int
    user_id: str
    name: str
    role: UserRole


# ─── User registration ────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    """
    POST /auth/register request body.

    Only supervisors can call this endpoint (enforced by the route guard).
    The password field has a minimum length enforced here — the
    actual hashing happens in the service layer.
    """
    name: str = Field(
        min_length=2,
        max_length=255,
        description="Full display name of the new user",
    )
    email: EmailStr
    password: str = Field(
        min_length=8,
        description="Must be at least 8 characters",
    )
    role: UserRole = Field(
        default=UserRole.readonly,
        description="System role for the new user. Defaults to readonly.",
    )

    @field_validator("password")
    @classmethod
    def password_not_email(cls, v: str, info) -> str:
        """
        Prevent the rookie mistake of setting password == email.
        Crude but catches the obvious case.
        """
        # Access other field values via info.data (Pydantic v2 style)
        email = info.data.get("email", "")
        if v.lower() == str(email).lower():
            raise ValueError("Password must not match the email address.")
        return v


class UserResponse(BaseModel):
    """
    Response schema for user data — safe to return to clients.

    Deliberately excludes `hashed_password`. Pydantic v2's `model_config`
    with `from_attributes=True` lets us construct this directly from an ORM
    User object: UserResponse.model_validate(user_orm_object).
    """
    id: str
    name: str
    email: str
    role: UserRole
    created_at: datetime

    model_config = {"from_attributes": True}


# ─── Password change (self-service) ───────────────────────────────────────────

class PasswordChangeRequest(BaseModel):
    """
    POST /auth/change-password request body.

    Users can change their own password. They must provide the current
    password for verification (prevents someone changing a password via
    a stolen session without knowing the original).
    """
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8)

    @field_validator("new_password")
    @classmethod
    def new_not_same_as_current(cls, v: str, info) -> str:
        current = info.data.get("current_password", "")
        if v == current:
            raise ValueError("New password must differ from the current password.")
        return v


# ─── Token introspection (/auth/me) ───────────────────────────────────────────

class MeResponse(BaseModel):
    """
    GET /auth/me response — returns the currently authenticated user's info
    derived from the JWT payload (no DB query required).
    """
    user_id: str
    name: str
    role: UserRole