"""
FastAPI application server.
Handles CORS, middleware, and route registration.
"""

from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from app.config import get_settings
from app.database import init_db, close_db
from app.api.routes import router


def create_app() -> FastAPI:
    """
    Create and configure the FastAPI application.

    Returns:
        Configured FastAPI app instance.
    """
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Application lifespan handler."""
        logger.info("Starting API server...")
        await init_db()
        logger.info("Database initialized")
        yield
        logger.info("Shutting down API server...")
        await close_db()
        logger.info("API server stopped")

    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        description="GTA5RP Apartment Monitoring System",
        lifespan=lifespan,
    )

    # CORS middleware. Never combine a wildcard origin with credentials: it is
    # insecure and browsers ignore it. Use the configured allow-list instead.
    cors_origins = settings.api.cors_origins
    if "*" in cors_origins:
        logger.warning(
            "CORS_ORIGINS contains '*'; dropping it and disabling credentials. "
            "List explicit frontend origins instead."
        )
        cors_origins = [o for o in cors_origins if o != "*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )

    # Register routes
    app.include_router(router)

    logger.info(f"API server created: {settings.app_name} v{settings.version}")
    return app


def get_application() -> FastAPI:
    """Get application instance (for uvicorn)."""
    return create_app()