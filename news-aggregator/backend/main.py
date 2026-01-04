"""Main FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

from config import settings
from database import init_db
from services.scheduler import start_scheduler, stop_scheduler
from services.proxy_manager import proxy_manager


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan handler for startup/shutdown."""
    # Startup
    await init_db()
    start_scheduler()
    proxy_manager.start_background_search()
    yield
    # Shutdown
    proxy_manager.stop_background_search()
    stop_scheduler()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "healthy"}


# Import and include routers
from api import items, sources, connectors, rules, stats, email, proxies  # noqa: E402

app.include_router(items.router, prefix=settings.api_prefix, tags=["items"])
app.include_router(sources.router, prefix=settings.api_prefix, tags=["sources"])
app.include_router(connectors.router, prefix=settings.api_prefix, tags=["connectors"])
app.include_router(rules.router, prefix=settings.api_prefix, tags=["rules"])
app.include_router(stats.router, prefix=settings.api_prefix, tags=["stats"])
app.include_router(email.router, prefix=settings.api_prefix, tags=["email"])
app.include_router(proxies.router, prefix=settings.api_prefix, tags=["proxies"])
