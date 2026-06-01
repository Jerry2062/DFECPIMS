import asyncio
import os
import uuid
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import text
from app.core.security import hash_password

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:DFECPIMS@localhost:5432/dfecpims"
)

async def seed():
    engine = create_async_engine(DATABASE_URL, echo=False)
    AsyncSession = async_sessionmaker(bind=engine, expire_on_commit=False)
    
    async with AsyncSession() as session:
        user_id = str(uuid.uuid4())
        # Password must be under 72 bytes for bcrypt
        password = "DFECPIMS"
        
        await session.execute(text("""
            INSERT INTO users (id, name, email, hashed_password, role, created_at)
            VALUES (:id, :name, :email, :password, 'supervisor', NOW())
        """), {
            "id": user_id,
            "name": "System Administrator",
            "email": "jerryokechukwu96@gmail.com",
            "password": hash_password(password)
        })
        await session.commit()
        print(f"Supervisor created successfully!")
        print(f"Email: jerryokechukwu96@gmail.com")
        print(f"Password: {password}")
    
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(seed())