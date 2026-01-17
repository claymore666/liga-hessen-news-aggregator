"""Admin endpoints for health checks."""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from models import Item, Source

logger = logging.getLogger(__name__)
router = APIRouter()


class HealthCheckResponse(BaseModel):
    """Full system health status."""

    status: str
    instance_type: str
    llm_enabled: bool
    scheduler_running: bool
    scheduler_jobs: list[dict]
    llm_available: bool
    llm_provider: str | None
    proxy_count: int
    proxy_working: int
    database_ok: bool
    items_count: int
    sources_count: int


@router.get("/admin/health", response_model=HealthCheckResponse)
async def get_system_health(
    db: AsyncSession = Depends(get_db),
) -> HealthCheckResponse:
    """Get comprehensive system health status.

    Combines scheduler, LLM, proxy, and database status in one call.
    """
    from services.scheduler import scheduler, get_job_status
    from services.proxy_manager import proxy_manager
    from services.llm.ollama import OllamaProvider

    # Scheduler status
    scheduler_running = scheduler.running
    scheduler_jobs = get_job_status() if scheduler_running else []

    # LLM status
    llm_available = False
    llm_provider = None
    try:
        provider = OllamaProvider(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
        )
        llm_available = await provider.is_available()
        if llm_available:
            llm_provider = "ollama"
        elif settings.openrouter_api_key:
            llm_provider = "openrouter"
            llm_available = True
    except Exception as e:
        logger.debug(f"LLM health check failed: {e}")

    # Proxy status
    proxy_count = len(proxy_manager.working_proxies)
    proxy_working = proxy_count

    # Database status
    database_ok = True
    items_count = 0
    sources_count = 0
    try:
        items_count = await db.scalar(select(func.count(Item.id))) or 0
        sources_count = await db.scalar(select(func.count(Source.id))) or 0
    except Exception as e:
        logger.warning(f"Database health check failed: {e}")
        database_ok = False

    overall_status = "healthy"
    if not scheduler_running or not database_ok:
        overall_status = "degraded"
    if not database_ok:
        overall_status = "unhealthy"

    return HealthCheckResponse(
        status=overall_status,
        instance_type=settings.instance_type,
        llm_enabled=settings.llm_enabled,
        scheduler_running=scheduler_running,
        scheduler_jobs=scheduler_jobs,
        llm_available=llm_available,
        llm_provider=llm_provider,
        proxy_count=proxy_count,
        proxy_working=proxy_working,
        database_ok=database_ok,
        items_count=items_count,
        sources_count=sources_count,
    )
