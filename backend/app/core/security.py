"""
core/security.py

Cryptographic utilities for DFECPIMS authentication.

Responsibilities:
  1. Password hashing and verification (bcrypt via passlib)
  2. JWT token creation and decoding (HS256 via python-jose)

Nothing here touches the database. This module is pure crypto logic
that can be imported anywhere without pulling in SQLAlchemy.

Token payload structure:
  {
    "sub":  "<user_uuid>",       # Subject — user's database ID
    "role": "<UserRole value>",  # Embedded so guards don't need DB lookup
    "name": "<display name>",    # Convenience — avoids extra DB query for display
    "exp":  <unix timestamp>     # Expiry — set by create_access_token()
  }
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.models.user import UserRole


# ─── Configuration ────────────────────────────────────────────────────────────

SECRET_KEY: str = os.environ.get(
    "SECRET_KEY",
    # Fallback is development-only. In production this must be set explicitly.
    "dev-secret-DO-NOT-USE-IN-PRODUCTION-generate-with-openssl-rand-hex-32",
)

JWT_ALGORITHM: str = os.environ.get("JWT_ALGORITHM", "HS256")

ACCESS_TOKEN_EXPIRE_MINUTES: int = int(
    os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "480")  # 8 hours default
)


# ─── Password hashing ─────────────────────────────────────────────────────────

# CryptContext manages multiple hashing schemes and handles upgrades gracefully.
# bcrypt is the current active scheme. deprecated="auto" means if we ever add
# a newer scheme, old hashes are automatically flagged for rehash on next login.
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain_password: str) -> str:
    """
    Hash a plaintext password using bcrypt.

    Returns the bcrypt hash string (includes salt, cost factor, and algorithm
    identifier — it's a self-contained string, not just the hash bytes).

    Args:
        plain_password: The raw password string from the user's input.

    Returns:
        A bcrypt hash string safe to store in the database.
    """
    return pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a plaintext password against a stored bcrypt hash.

    This is a constant-time comparison — passlib handles timing-attack
    prevention internally, so don't roll your own comparison here.

    Args:
        plain_password:  The raw password from the login request.
        hashed_password: The stored bcrypt hash from the database.

    Returns:
        True if the password matches, False otherwise.
    """
    return pwd_context.verify(plain_password, hashed_password)


# ─── JWT tokens ───────────────────────────────────────────────────────────────

def create_access_token(
    user_id: str,
    role: UserRole,
    name: str,
    expires_delta: Optional[timedelta] = None,
) -> str:
    """
    Create a signed JWT access token for an authenticated user.

    Args:
        user_id:       The user's UUID (stored as the 'sub' claim).
        role:          The user's role (embedded in payload to avoid DB lookup).
        name:          The user's display name (convenience for frontend).
        expires_delta: Optional custom expiry duration. Defaults to
                       ACCESS_TOKEN_EXPIRE_MINUTES from environment.

    Returns:
        A signed JWT string to be returned to the client as Bearer token.
    """
    if expires_delta is None:
        expires_delta = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    now = datetime.now(timezone.utc)
    expire = now + expires_delta

    payload = {
        "sub": user_id,          # JWT standard: subject identifier
        "role": role.value,      # Role value (string) for role-based guards
        "name": name,            # Display name for UI convenience
        "iat": now,              # Issued at — useful for audit / token rotation
        "exp": expire,           # Expiry — jose validates this automatically
    }

    return jwt.encode(payload, SECRET_KEY, algorithm=JWT_ALGORITHM)


class TokenPayload:
    """
    Parsed and validated JWT payload.

    Not a Pydantic model (intentionally) — we construct this ourselves after
    decoding the token so we have typed access to the claims we care about,
    without pulling in Pydantic validation overhead on every request.
    """

    def __init__(self, sub: str, role: str, name: str) -> None:
        self.user_id: str = sub
        self.name: str = name

        # Validate the role string matches a known UserRole value.
        # If someone tampers the token payload somehow, this catches it.
        try:
            self.role: UserRole = UserRole(role)
        except ValueError:
            raise ValueError(f"Invalid role in token: {role!r}")

    def __repr__(self) -> str:
        return f"<TokenPayload user_id={self.user_id!r} role={self.role!r}>"


def decode_access_token(token: str) -> TokenPayload:
    """
    Decode and validate a JWT access token.

    Validates:
      - Signature (using SECRET_KEY and JWT_ALGORITHM)
      - Expiry ('exp' claim — jose raises ExpiredSignatureError if past)
      - Presence of required claims ('sub', 'role', 'name')

    Args:
        token: The raw JWT string from the Authorization header.

    Returns:
        A TokenPayload with typed access to the user's ID, role, and name.

    Raises:
        JWTError:   If the token is malformed, signature is invalid, or expired.
        ValueError: If the 'role' claim is not a valid UserRole value.
    """
    # jose raises JWTError for any validation failure including expiry.
    # Callers (the FastAPI dependency) should catch this and return 401.
    payload = jwt.decode(token, SECRET_KEY, algorithms=[JWT_ALGORITHM])

    user_id: Optional[str] = payload.get("sub")
    role: Optional[str] = payload.get("role")
    name: Optional[str] = payload.get("name", "")

    if user_id is None or role is None:
        raise JWTError("Token is missing required claims (sub, role)")

    return TokenPayload(sub=user_id, role=role, name=name or "")