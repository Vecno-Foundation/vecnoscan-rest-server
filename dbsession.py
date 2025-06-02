import logging
import os

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import declarative_base, sessionmaker

_logger = logging.getLogger(__name__)

# Fetch pool size and max overflow values from environment variables
pool_size = int(os.getenv("POOL_SIZE", 50))  # Default to 50 if not set
max_overflow = int(os.getenv("MAX_OVERFLOW", 100))  # Default to 500 if not set

# Update engine creation to use pool_size and max_overflow
engine = create_async_engine(
    os.getenv("ASYNC_SQL_URI"),
    pool_pre_ping=True,
    echo=False,
    pool_size=pool_size,  # Apply pool_size
    max_overflow=max_overflow  # Apply max_overflow
)

Base = declarative_base()

# Configure sessionmaker for async session
async_session = sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession
)

async def create_all(drop=False):
    async with engine.begin() as conn:
        if drop:
            await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
