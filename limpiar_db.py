import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from config import settings
from database.models import Base

async def clean_db():
    print(f"Connecting to {settings.database_url}")
    engine = create_async_engine(settings.database_url, echo=True)
    async with engine.begin() as conn:
        print("Dropping all tables...")
        await conn.run_sync(Base.metadata.drop_all)
        print("Recreating all tables...")
        await conn.run_sync(Base.metadata.create_all)
    print("Database cleaned successfully.")

if __name__ == "__main__":
    asyncio.run(clean_db())
