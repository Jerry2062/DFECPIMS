"""
services/user_service.py

Database operations for user management.

The service layer sits between route handlers and the database.
Route handlers stay thin (validate input, call service, return response).
Business logic and DB queries live here.

All methods accept an AsyncSession and return ORM User objects or None.
Callers (route handlers) are responsible for committing the session —
this keeps transaction boundaries explicit and prevents silent commits.
"""

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.user import User, UserRole
from app.schemas.auth import UserCreate


class UserService:
    """
    Handles user lookup, creation, and credential management.

    Instantiate with a database session:
        service = UserService(db)
        user = await service.get_by_email("a@b.com")
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_by_id(self, user_id: str) -> Optional[User]:
        """
        Fetch a user by their UUID primary key.

        Returns None if not found — callers should handle the None case
        and return an appropriate 404, not assume the user exists.
        """
        result = await self.db.execute(
            select(User).where(User.id == user_id)
        )
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> Optional[User]:
        """
        Fetch a user by email address (case-insensitive).

        Used during login to look up the user before verifying their password.
        Returns None if no user with that email exists.
        """
        result = await self.db.execute(
            # lower() on both sides handles the case where someone registers
            # with "User@Example.com" and logs in with "user@example.com".
            select(User).where(User.email == email.lower().strip())
        )
        return result.scalar_one_or_none()

    async def create_user(self, data: UserCreate) -> User:
        """
        Create a new user account.

        Hashes the password before storing. Generates a UUID for the user ID.
        Does NOT commit — caller must call `await db.commit()` after.

        Args:
            data: Validated UserCreate schema from the request body.

        Returns:
            The newly created User ORM object (not yet committed).

        Raises:
            ValueError: If a user with the same email already exists.
        """
        # Check for duplicate email before attempting insert.
        # The DB also has a unique constraint, but a friendly error here
        # is better than surfacing a raw IntegrityError to the client.
        existing = await self.get_by_email(data.email)
        if existing is not None:
            raise ValueError(f"A user with email '{data.email}' already exists.")

        user = User(
            id=str(uuid.uuid4()),
            name=data.name.strip(),
            # Normalize email to lowercase at storage time
            email=data.email.lower().strip(),
            hashed_password=hash_password(data.password),
            role=data.role,
        )

        self.db.add(user)
        # Flush to run the INSERT and populate any server-side defaults,
        # but don't commit — the caller controls transaction boundaries.
        await self.db.flush()

        return user

    async def update_password(
        self,
        user: User,
        new_password: str,
    ) -> None:
        """
        Update a user's password hash in place.

        Does NOT commit — caller must call `await db.commit()`.

        Args:
            user:         The User ORM object to update (must be attached to session).
            new_password: The new plaintext password (will be hashed here).
        """
        user.hashed_password = hash_password(new_password)
        self.db.add(user)
        await self.db.flush()

    async def list_users(self) -> list[User]:
        """
        Return all users ordered by creation date.

        Supervisors may want to see the full user list for account management.
        This is intentionally simple — add pagination if the user count grows.
        """
        result = await self.db.execute(
            select(User).order_by(User.created_at)
        )
        return list(result.scalars().all())
