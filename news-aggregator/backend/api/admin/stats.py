"""Admin endpoints for statistics."""

import logging
from datetime import datetime, timedelta

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db, json_extract_path
from models import Item, Source, Rule

logger = logging.getLogger(__name__)
router = APIRouter()


class DatabaseStatsResponse(BaseModel):
    """Database statistics."""
    items_count: int
    sources_count: int
    rules_count: int
    items_with_summary: int
    items_without_summary: int


class WorkerStatus(BaseModel):
    """Status of a background worker."""
    running: bool
    paused: bool
    stopped_due_to_errors: bool = False
    service_available: bool | None = None  # For workers that depend on external services
    stats: dict


class SchedulerStatus(BaseModel):
    """Status of the scheduler."""
    running: bool
    jobs: list[dict]


class ProcessingQueueStats(BaseModel):
    """Statistics about the processing queue."""
    total: int
    by_retry_priority: dict[str, int]
    awaiting_classifier: int
    awaiting_dedup: int
    awaiting_vectordb: int


class ItemStats(BaseModel):
    """Statistics about items."""
    total: int
    by_priority: dict[str, int]
    unread: int
    starred: int


class CerebrasStats(BaseModel):
    """Cerebras provider availability and rate limits."""
    configured: bool
    available: bool
    model: str
    rate_limits: dict | None = None


class SystemStatsResponse(BaseModel):
    """Comprehensive system statistics for dashboard."""
    scheduler: SchedulerStatus
    llm_worker: WorkerStatus
    cerebras: CerebrasStats | None = None
    classifier_worker: WorkerStatus
    dedup_worker: WorkerStatus
    processing_queue: ProcessingQueueStats
    items: ItemStats
    timestamp: str


@router.get("/admin/db-stats", response_model=DatabaseStatsResponse)
async def get_database_stats(
    db: AsyncSession = Depends(get_db),
) -> DatabaseStatsResponse:
    """Get database statistics."""
    items_count = await db.scalar(select(func.count(Item.id))) or 0
    sources_count = await db.scalar(select(func.count(Source.id))) or 0
    rules_count = await db.scalar(select(func.count(Rule.id))) or 0

    with_summary = await db.scalar(
        select(func.count(Item.id)).where(Item.summary.isnot(None))
    ) or 0

    return DatabaseStatsResponse(
        items_count=items_count,
        sources_count=sources_count,
        rules_count=rules_count,
        items_with_summary=with_summary,
        items_without_summary=items_count - with_summary,
    )


@router.get("/admin/stats", response_model=SystemStatsResponse)
async def get_system_stats(
    db: AsyncSession = Depends(get_db),
) -> SystemStatsResponse:
    """Get comprehensive system statistics for dashboard.

    Returns status of scheduler, workers, processing queue, and items.
    """
    from services.scheduler import scheduler, get_job_status
    from services.worker_status import read_state, read_stats

    # Scheduler status - read from DB, fall back to local
    sched_state = await read_state("scheduler")
    sched_stats = await read_stats("scheduler")
    scheduler_running = sched_state.get("running", False) or scheduler.running
    # Local scheduler has live jobs; non-leader reads from DB
    jobs = get_job_status() if scheduler.running else sched_stats.get("jobs", [])
    scheduler_status = SchedulerStatus(
        running=scheduler_running,
        jobs=jobs,
    )

    # LLM Worker status from DB
    llm_state = await read_state("llm")
    llm_stats = await read_stats("llm")
    llm_worker_status = WorkerStatus(
        running=llm_state.get("running", False),
        paused=llm_state.get("paused", False),
        stopped_due_to_errors=llm_state.get("stopped_due_to_errors", False),
        stats={k: v for k, v in llm_stats.items() if k not in ("fresh_queue_size", "synced_at")} or
              {"fresh_processed": 0, "backlog_processed": 0, "errors": 0},
    )

    # Cerebras provider status (with rate limits from cached state)
    from config import settings
    cerebras_stats = None
    if settings.cerebras_api_key:
        from services.llm.cerebras import CerebrasProvider, get_rate_limiter
        cerebras = CerebrasProvider(
            api_key=settings.cerebras_api_key,
            model=settings.cerebras_model,
            timeout=settings.cerebras_timeout,
        )
        cerebras_available = await cerebras.is_available()
        # Use cached rate limiter state (updated on every API call)
        # Only fetch fresh limits if no cached data exists
        rate_limiter = get_rate_limiter()
        rate_limits = rate_limiter.get_status()
        if rate_limits is None and cerebras_available:
            try:
                rate_limits_full = await cerebras.get_rate_limits()
                if rate_limits_full:
                    rate_limits = rate_limits_full.get("requests")
            except Exception as e:
                logger.debug(f"Failed to fetch Cerebras rate limits: {e}")
        cerebras_stats = CerebrasStats(
            configured=True,
            available=cerebras_available,
            model=settings.cerebras_model,
            rate_limits=rate_limits,
        )

    # Classifier Worker status from DB + actual service reachability
    clf_state = await read_state("classifier")
    clf_stats = await read_stats("classifier")
    # Check if the classifier API is actually reachable (direct health check)
    classifier_reachable = False
    try:
        from config import settings
        if settings.classifier_url:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
                resp = await client.get(f"{settings.classifier_url.rstrip('/')}/health")
                classifier_reachable = resp.status_code == 200
    except Exception:
        pass
    classifier_worker_status = WorkerStatus(
        running=clf_state.get("running", False),
        paused=clf_state.get("paused", False),
        stopped_due_to_errors=clf_state.get("stopped_due_to_errors", False),
        service_available=classifier_reachable,
        stats={k: v for k, v in clf_stats.items() if k != "synced_at"} or
              {"processed": 0, "errors": 0},
    )

    # Dedup Worker status from DB
    dedup_state = await read_state("dedup")
    dedup_stats = await read_stats("dedup")
    dedup_worker_status = WorkerStatus(
        running=dedup_state.get("running", False),
        paused=dedup_state.get("paused", False),
        stopped_due_to_errors=dedup_state.get("stopped_due_to_errors", False),
        stats={k: v for k, v in dedup_stats.items() if k != "synced_at"} or
              {"phase1_checked": 0, "phase2_checked": 0, "duplicates_found": 0, "errors": 0},
    )

    # Processing queue stats — combine into fewer queries
    retry_priority = json_extract_path(Item.metadata_, "retry_priority")

    queue_total = await db.scalar(
        select(func.count(Item.id)).where(Item.needs_llm_processing == True)  # noqa: E712
    ) or 0

    retry_result = await db.execute(
        select(retry_priority.label("rp"), func.count(Item.id))
        .where(Item.needs_llm_processing == True)  # noqa: E712
        .group_by("rp")
    )
    by_retry_priority = {row[0] or "unknown": row[1] for row in retry_result.fetchall()}

    # Awaiting counts — only scan recent items (older items are fully processed)
    recent_cutoff = datetime.utcnow() - timedelta(days=2)
    queue_row = (await db.execute(
        select(
            func.count(Item.id).filter(
                json_extract_path(Item.metadata_, "pre_filter").is_(None)
            ).label("awaiting_classifier"),
            func.count(Item.id).filter(
                Item.similar_to_id.is_(None),
                json_extract_path(Item.metadata_, "dedup_phase2").is_(None),
            ).label("awaiting_dedup"),
            func.count(Item.id).filter(
                json_extract_path(Item.metadata_, "vectordb_indexed").is_(None)
            ).label("awaiting_vectordb"),
        ).where(Item.fetched_at >= recent_cutoff)
    )).one()

    processing_queue = ProcessingQueueStats(
        total=queue_total,
        by_retry_priority=by_retry_priority,
        awaiting_classifier=queue_row.awaiting_classifier,
        awaiting_dedup=queue_row.awaiting_dedup,
        awaiting_vectordb=queue_row.awaiting_vectordb,
    )

    # Item stats — single query for all counts
    item_row = (await db.execute(
        select(
            func.count(Item.id).label("total"),
            func.count(Item.id).filter(Item.is_read == False).label("unread"),  # noqa: E712
            func.count(Item.id).filter(Item.is_starred == True).label("starred"),  # noqa: E712
        )
    )).one()

    priority_result = await db.execute(
        select(Item.priority, func.count(Item.id))
        .group_by(Item.priority)
    )
    by_priority = {}
    for row in priority_result.fetchall():
        priority_val = row[0]
        if priority_val is None:
            key = "none"
        elif hasattr(priority_val, 'value'):
            key = priority_val.value
        else:
            key = str(priority_val)
        by_priority[key] = row[1]

    item_stats = ItemStats(
        total=item_row.total,
        by_priority=by_priority,
        unread=item_row.unread,
        starred=item_row.starred,
    )

    return SystemStatsResponse(
        scheduler=scheduler_status,
        llm_worker=llm_worker_status,
        cerebras=cerebras_stats,
        classifier_worker=classifier_worker_status,
        dedup_worker=dedup_worker_status,
        processing_queue=processing_queue,
        items=item_stats,
        timestamp=datetime.utcnow().isoformat(),
    )
