"""
services/auth_service.py

Authentication business logic for DFECPIMS.

Handles the login flow: look up user → verify password → issue token.
Audit logging now delegates to AuditService rather than duplicating
the log entry construction inline. The old _log_event helper is removed.

Separating this from user_service.py keeps the service layer cohesive:
  user_service   → CRUD operations on users
  auth_service   → login flow + auth-specific audit logging
"""

from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    create_access_token,
    verify_password,
)
from app.models.user import User
from app.schemas.auth import LoginRequest, TokenResponse
from app.services.audit_service import AuditService
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
        self.audit = AuditService(db)  # Shared writer — no more _log_event duplication

    async def login(
        self,
        data: LoginRequest,
        client_ip: Optional[str] = None,
    ) -> TokenResponse:
        """
        Authenticate a user and return a JWT token response.

        Flow:
          1. Look up user by email
          2. Verify password against stored bcrypt hash (timing-safe even on miss)
          3. Write audit log entry (success or failure) via AuditService
          4. Commit the audit entry
          5. Return TokenResponse with JWT

        Args:
            data:      Validated LoginRequest (email + password).
            client_ip: Optional client IP for audit log context.

        Returns:
            TokenResponse containing the JWT and user metadata.

        Raises:
            ValueError: If credentials are invalid. Generic message to prevent
                        user enumeration — don't reveal whether it was the email
                        or the password that failed.
        """
        # Step 1: Look up user by email
        user: Optional[User] = await self.user_service.get_by_email(data.email)

        # Step 2: Verify password.
        # We always call verify_password even if user is None, comparing against
        # a dummy hash. This makes the response time roughly equal whether the
        # email exists or not — prevents timing-based user enumeration.
        dummy_hash = "$2b$12$KIXEaEfn6sWPLbVnFRvtbe9XlnDe5C7aGw9K4vRL5GU.umZCx5lom"
        stored_hash = user.hashed_password if user else dummy_hash
        password_valid = verify_password(data.password, stored_hash)

        if user is None or not password_valid:
            await self.audit.log(
                action="USER_LOGIN_FAILED",
                actor_id=user.id if user else None,
                detail={
                    "email": data.email,
                    "reason": "invalid_credentials",
                    "client_ip": client_ip,
                },
            )
            await self.db.commit()
            raise ValueError("Invalid email or password.")

        # Step 3: Successful login
        await self.audit.log(
            action="USER_LOGIN",
            actor_id=user.id,
            detail={
                "client_ip": client_ip,
                "role": user.role.value,
            },
        )
        await self.db.commit()

        # Step 4: Issue JWT
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