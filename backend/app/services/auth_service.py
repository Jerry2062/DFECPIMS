"""
services/auth_service.py

Authentication business logic for DFECPIMS.

Handles the login flow: look up user → verify password → issue token.
Also writes audit log entries for auth events (login success/failure).

Separating this from user_service.py keeps the service layer cohesive:
  user_service   → CRUD operations on users
  auth_service   → login flow + auth-specific audit logging
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    create_access_token,
    verify_password,
)
from app.models.audit_log import AuditLog
from app.models.user import User
from app.schemas.auth import LoginRequest, TokenResponse
from app.services.user_service import UserService


class AuthService:
    """
    Handles authentication flows including login and audit logging.

    Usage:
        service = AuthService(db)
        token_response = await service.login(login_request, client_ip="x.x.x.x")
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.user_service = UserService(db)

    async def login(
        self,
        data: LoginRequest,
        client_ip: Optional[str] = None,
    ) -> TokenResponse:
        """
        Authenticate a user and return a JWT token response.

        Flow:
          1. Look up user by email
          2. Verify password against stored bcrypt hash
          3. Write audit log entry (success or failure)
          4. Return TokenResponse with JWT

        Args:
            data:      Validated LoginRequest (email + password).
            client_ip: Optional client IP for audit log context.

        Returns:
            TokenResponse containing the JWT and user metadata.

        Raises:
            ValueError: If credentials are invalid (generic message to prevent
                        user enumeration — don't tell the attacker whether it was
                        the email or the password that was wrong).
        """
        # Step 1: Look up user
        user: Optional[User] = await self.user_service.get_by_email(data.email)

        # Step 2: Verify password
        # We verify even if user is None (using a dummy hash) to prevent
        # timing attacks that could enumerate valid email addresses.
        # If user is None, we compare against a dummy hash and always fail.
        dummy_hash = "$2b$12$KIXEaEfn6sWPLbVnFRvtbe9XlnDe5C7aGw9K4vRL5GU.umZCx5lom"
        stored_hash = user.hashed_password if user else dummy_hash
        password_valid = verify_password(data.password, stored_hash)

        if user is None or not password_valid:
            # Write failed login attempt to audit log
            await self._log_event(
                action="USER_LOGIN_FAILED",
                actor_id=user.id if user else None,
                detail=json.dumps({
                    "email": data.email,
                    "reason": "invalid_credentials",
                    "client_ip": client_ip,
                }),
            )
            await self.db.commit()

            # Generic message — don't leak which part was wrong
            raise ValueError("Invalid email or password.")

        # Step 3: Write successful login to audit log
        await self._log_event(
            action="USER_LOGIN",
            actor_id=user.id,
            detail=json.dumps({
                "client_ip": client_ip,
                "role": user.role.value,
            }),
        )
        await self.db.commit()

        # Step 4: Issue token
        token = create_access_token(
            user_id=user.id,
            role=user.role,
            name=user.name,
        )

        return TokenResponse(
            access_token=token,
            token_type="bearer",
            expires_in_minutes=ACCESS_TOKEN_EXPIRE_MINUTES,
            user_id=user.id,
            name=user.name,
            role=user.role,
        )

    async def _log_event(
        self,
        action: str,
        actor_id: Optional[str],
        detail: Optional[str] = None,
        case_id: Optional[str] = None,
    ) -> None:
        """
        Internal helper: write an append-only audit log entry.

        Does NOT commit — the caller controls the transaction.
        This is a thin wrapper so we don't duplicate AuditLog construction.

        Note: The AuditLog trigger at the DB level prevents any UPDATE or DELETE,
        so even if application code bugs out and calls this twice, the worst
        outcome is two log entries — not a corrupted or missing one.
        """
        log_entry = AuditLog(
            id=str(uuid.uuid4()),
            case_id=case_id,
            actor_id=actor_id,
            action=action,
            detail=detail,
            timestamp=datetime.now(timezone.utc),
        )
        self.db.add(log_entry)
        await self.db.flush()