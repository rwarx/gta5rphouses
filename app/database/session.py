"""
Database session management using SQLAlchemy 2.0 async.
Provides async session factory and helper functions.
"""

from typing import AsyncGenerator, Optional
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
    async_sessionmaker,
    AsyncEngine,
)
from sqlalchemy import event, text
from loguru import logger

from app.config import get_settings
from app.database.models import Base


class DatabaseSession:
    """
    Manages database engine and session lifecycle.
    Implements singleton pattern for engine and session factory.
    """

    _engine: Optional[AsyncEngine] = None
    _session_factory: Optional[async_sessionmaker[AsyncSession]] = None

    @classmethod
    async def init(cls, database_url: Optional[str] = None) -> None:
        """
        Initialize database engine and session factory.

        Args:
            database_url: Database connection string. If None, loads from settings.
        """
        if cls._engine is not None:
            logger.warning("Database already initialized, skipping")
            return

        settings = get_settings()
        url = database_url or settings.database.database_url

        logger.info(f"Initializing database connection to {url}")

        # SQLite doesn't support pool_size/max_overflow
        engine_kwargs = {
            "echo": False,
        }
        
        if "sqlite" not in url:
            engine_kwargs["pool_pre_ping"] = True
            engine_kwargs["pool_size"] = 10
            engine_kwargs["max_overflow"] = 20
        else:
            # For SQLite: allow cross-thread, 15s busy timeout
            engine_kwargs["connect_args"] = {
                "check_same_thread": False,
                "timeout": 15,
            }

        cls._engine = create_async_engine(url, **engine_kwargs)

        # Set SQLite PRAGMAs for better concurrent access
        if "sqlite" in url:
            @event.listens_for(cls._engine.sync_engine, "connect")
            def _set_sqlite_pragmas(dbapi_connection, connection_record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL;")
                cursor.execute("PRAGMA foreign_keys=ON;")
                cursor.close()

        cls._session_factory = async_sessionmaker(
            cls._engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

        # Auto-create all tables on init (for SQLite development)
        async with cls._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        
        logger.info("Database initialized successfully")

    @classmethod
    async def close(cls) -> None:
        """Close database engine and dispose of connections."""
        if cls._engine is None:
            return

        logger.info("Closing database connections...")
        await cls._engine.dispose()
        cls._engine = None
        cls._session_factory = None
        logger.info("Database connections closed")

    @classmethod
    def get_session_factory(cls) -> async_sessionmaker[AsyncSession]:
        """Get the session factory."""
        if cls._session_factory is None:
            raise RuntimeError("Database not initialized. Call init() first.")
        return cls._session_factory

    @classmethod
    async def get_session(cls) -> AsyncGenerator[AsyncSession, None]:
        """
        Get an async database session.
        Yields a session that automatically closes after use.
        """
        if cls._session_factory is None:
            raise RuntimeError("Database not initialized. Call init() first.")

        async with cls._session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception as e:
                await session.rollback()
                logger.error(f"Database session error: {e}")
                raise
            finally:
                await session.close()

    @classmethod
    @asynccontextmanager
    async def get_session_context(cls) -> AsyncGenerator[AsyncSession, None]:
        """
        Context manager for database sessions.
        Usage: async with DatabaseSession.get_session_context() as session:
        """
        if cls._session_factory is None:
            raise RuntimeError("Database not initialized. Call init() first.")

        async with cls._session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception as e:
                await session.rollback()
                logger.error(f"Database session error: {e}")
                raise
            finally:
                await session.close()

    @classmethod
    async def create_all(cls) -> None:
        """Create all tables (useful for development)."""
        if cls._engine is None:
            raise RuntimeError("Database not initialized. Call init() first.")

        logger.info("Creating database tables...")
        async with cls._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables created successfully")

    @classmethod
    async def drop_all(cls) -> None:
        """Drop all tables (useful for testing)."""
        if cls._engine is None:
            raise RuntimeError("Database not initialized. Call init() first.")

        logger.warning("Dropping all database tables...")
        async with cls._engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        logger.warning("All database tables dropped")


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency injection for FastAPI."""
    async for session in DatabaseSession.get_session():
        yield session


async def init_db() -> None:
    """Initialize database for application startup."""
    await DatabaseSession.init()


async def close_db() -> None:
    """Close database for application shutdown."""
    await DatabaseSession.close()