"""Admin endpoints for statistics."""

import logging
from datetime import datetime

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


class SystemStatsResponse(BaseModel):
    """Comprehensive system statistics for dashboard."""
    scheduler: SchedulerStatus
    llm_worker: WorkerStatus
    classifier_worker: WorkerStatus
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
    from services.llm_worker import get_worker as get_llm_worker
    from services.classifier_worker import get_classifier_worker

    # Scheduler status
    scheduler_status = SchedulerStatus(
        running=scheduler.running,
        jobs=get_job_status() if scheduler.running else [],
    )

    # LLM Worker status
    llm_worker = get_llm_worker()
    if llm_worker:
        llm_status = await llm_worker.get_status()
        llm_worker_status = WorkerStatus(
            running=llm_status["running"],
            paused=llm_status["paused"],
            stats=llm_status["stats"],
        )
    else:
        llm_worker_status = WorkerStatus(
            running=False,
            paused=False,
            stats={"fresh_processed": 0, "backlog_processed": 0, "errors": 0},
        )

    # Classifier Worker status
    classifier_worker = get_classifier_worker()
    if classifier_worker:
        clf_status = await classifier_worker.get_status()
        classifier_worker_status = WorkerStatus(
            running=clf_status["running"],
            paused=clf_status["paused"],
            stats=clf_status["stats"],
        )
    else:
        classifier_worker_status = WorkerStatus(
            running=False,
            paused=False,
            stats={"processed": 0, "errors": 0},
        )

    # Run all independent DB queries concurrently
    import asyncio

    retry_priority = json_extract_path(Item.metadata_, "retry_priority")

    (
        queue_total,
        retry_result,
        awaiting_classifier,
        awaiting_dedup,
        awaiting_vectordb,
        items_total,
        priority_result,
        unread_count,
        starred_count,
    ) = await asyncio.gather(
        db.scalar(
            select(func.count(Item.id)).where(Item.needs_llm_processing == True)  # noqa: E712
        ),
        db.execute(
            select(retry_priority.label("rp"), func.count(Item.id))
            .where(Item.needs_llm_processing == True)  # noqa: E712
            .group_by("rp")
        ),
        db.scalar(
            select(func.count(Item.id)).where(
                json_extract_path(Item.metadata_, "pre_filter").is_(None)
            )
        ),
        db.scalar(
            select(func.count(Item.id)).where(
                Item.similar_to_id.is_(None),
                json_extract_path(Item.metadata_, "duplicate_checked").is_(None),
            )
        ),
        db.scalar(
            select(func.count(Item.id)).where(
                json_extract_path(Item.metadata_, "vectordb_indexed").is_(None)
            )
        ),
        db.scalar(select(func.count(Item.id))),
        db.execute(
            select(Item.priority, func.count(Item.id))
            .group_by(Item.priority)
        ),
        db.scalar(
            select(func.count(Item.id)).where(Item.is_read == False)  # noqa: E712
        ),
        db.scalar(
            select(func.count(Item.id)).where(Item.is_starred == True)  # noqa: E712
        ),
    )

    by_retry_priority = {row[0] or "unknown": row[1] for row in retry_result.fetchall()}

    processing_queue = ProcessingQueueStats(
        total=queue_total or 0,
        by_retry_priority=by_retry_priority,
        awaiting_classifier=awaiting_classifier or 0,
        awaiting_dedup=awaiting_dedup or 0,
        awaiting_vectordb=awaiting_vectordb or 0,
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
        total=items_total or 0,
        by_priority=by_priority,
        unread=unread_count or 0,
        starred=starred_count or 0,
    )

    return SystemStatsResponse(
        scheduler=scheduler_status,
        llm_worker=llm_worker_status,
        classifier_worker=classifier_worker_status,
        processing_queue=processing_queue,
        items=item_stats,
        timestamp=datetime.utcnow().isoformat(),
    )
