# dbsession.py
import logging
import os

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import declarative_base, sessionmaker

_logger = logging.getLogger(__name__)

Base = declarative_base()


def _make_engine(uri: str):
    """
    Create an async SQLAlchemy engine with sensible defaults and tuning.
    """
    if not uri or not uri.strip():
        raise ValueError("Database URI is empty or not provided")

    return create_async_engine(
        uri,
        pool_pre_ping=True,
        connect_args={"server_settings": {"enable_seqscan": "off"}},  # Helps Postgres performance
        pool_size=int(os.getenv("SQL_POOL_SIZE", "15")),
        max_overflow=int(os.getenv("SQL_POOL_MAX_OVERFLOW", "0")),
        pool_recycle=int(os.getenv("SQL_POOL_RECYCLE_SECONDS", "1200")),
        echo=os.getenv("DEBUG", "").lower() in ("true", "1", "yes"),
        future=True,
    )


# Primary database (used for transactions, UTXOs, balances, etc.)
primary_uri = os.getenv("SQL_URI")
if not primary_uri:
    raise RuntimeError("SQL_URI environment variable is required")

primary_engine = _make_engine(primary_uri)
async_session_factory = sessionmaker(
    primary_engine,
    expire_on_commit=False,
    class_=AsyncSession
)


def async_session():
    """
    Use as context manager:
        async with async_session() as s:
            ...
    """
    return async_session_factory()


# Optional read-optimized database for heavy block queries
blocks_uri = os.getenv("SQL_URI_BLOCKS")

if blocks_uri and blocks_uri.strip():
    _logger.info(f"Using separate database for block queries: {blocks_uri.split('@')[-1]}")
    blocks_engine = _make_engine(blocks_uri)
    async_session_blocks_factory = sessionmaker(
        blocks_engine,
        expire_on_commit=False,
        class_=AsyncSession
    )

    def async_session_blocks():
        return async_session_blocks_factory()
else:
    _logger.info("SQL_URI_BLOCKS not set – falling back to primary database for block queries")
    def async_session_blocks():
        return async_session_factory()


# Optional: Make it clear what is exported
__all__ = ["async_session", "async_session_blocks", "Base"]