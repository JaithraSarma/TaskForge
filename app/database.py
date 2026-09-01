"""
Async database engine and session management for the FastAPI API layer.

IMPORTANT: This module uses asyncpg (async driver). It must ONLY be used
in the FastAPI context. Celery workers use psycopg2 (sync) — see worker/tasks.py.
"""

from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

settings = get_settings()

if settings.database_url.startswith("sqlite"):
    engine = create_async_engine(
        settings.database_url,
        echo=settings.debug,
        pool_pre_ping=True,
        pool_recycle=300,
    )
else:
    engine = create_async_engine(
        settings.database_url,
        echo=settings.debug,
        pool_size=20,
        max_overflow=10,
        pool_pre_ping=True,
        pool_recycle=300,
    )

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """SQLAlchemy declarative base."""

    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async database session."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """Create all tables. Safe to run concurrently from multiple workers.

    A PostgreSQL advisory lock serializes concurrent create_all calls so two
    Uvicorn workers do not race on creating the job_status enum type. SQLite,
    used in tests, has no advisory locks and needs no serialization.
    """
    async with engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            # Arbitrary constant lock key shared by all workers.
            await conn.execute(text("SELECT pg_advisory_xact_lock(9123456789)"))
        await conn.run_sync(Base.metadata.create_all)
