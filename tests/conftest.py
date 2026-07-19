"""
Shared pytest fixtures.

Tests run against an in-memory SQLite database so they need no external
services (PostgreSQL, Redis, a browser or a Telegram token). The database URL
is forced before the application settings singleton is created.
"""

import os

# Force an isolated in-memory database and disable optional integrations
# *before* anything imports app.config (settings are a cached singleton).
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["BOT_TOKEN"] = ""
os.environ["SMART_MODE"] = "true"

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession

from app.database.models import Base


@pytest_asyncio.fixture
async def engine():
    """Create a fresh in-memory database engine with all tables."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session(engine) -> AsyncSession:
    """Provide an AsyncSession bound to the in-memory engine."""
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
