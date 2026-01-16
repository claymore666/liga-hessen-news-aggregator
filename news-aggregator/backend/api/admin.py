"""Admin API endpoints for backend management."""

import logging
import os
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import async_session_maker, get_db
from models import Channel, Item, Source, Rule, Priority

logger = logging.getLogger(__name__)
router = APIRouter()


class DeleteItemsResponse(BaseModel):
    """Response for delete items operation."""
    deleted_count: int
    message: str


class DatabaseStatsResponse(BaseModel):
    """Database statistics."""
    items_count: int
    sources_count: int
    rules_count: int
    items_with_summary: int
    items_without_summary: int


@router.delete("/admin/items", response_model=DeleteItemsResponse)
async def delete_all_items(
    db: AsyncSession = Depends(get_db),
) -> DeleteItemsResponse:
    """Delete all items from the database.

    Use this to clear items before a fresh refetch with LLM analysis.
    """
    count = await db.scalar(select(func.count(Item.id)))
    await db.execute(delete(Item))

    logger.info(f"Deleted {count} items via admin API")
    return DeleteItemsResponse(
        deleted_count=count or 0,
        message=f"Deleted {count} items"
    )


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


@router.post("/admin/reanalyze-items")
async def reanalyze_items(
    limit: int = 10,
    db: AsyncSession = Depends(get_db),
):
    """Re-analyze items without summaries using LLM.

    Args:
        limit: Maximum number of items to re-analyze (default 10)
    """
    from services.processor import create_processor_from_settings

    try:
        processor = await create_processor_from_settings()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"LLM not available: {e}")

    # Get items without summaries
    result = await db.execute(
        select(Item)
        .where(Item.summary.is_(None))
        .limit(limit)
    )
    items = result.scalars().all()

    if not items:
        return {"message": "No items need re-analysis", "analyzed": 0}

    analyzed = 0
    for item in items:
        try:
            analysis = await processor.analyze(item)
            if analysis.get("summary"):
                item.summary = analysis["summary"]
                analyzed += 1
                logger.info(f"Re-analyzed item {item.id}: {item.title[:40]}")
        except Exception as e:
            logger.warning(f"Failed to re-analyze item {item.id}: {e}")

    return {
        "message": f"Re-analyzed {analyzed}/{len(items)} items",
        "analyzed": analyzed,
        "total_checked": len(items),
    }


@router.delete("/admin/items/old", response_model=DeleteItemsResponse)
async def delete_old_items(
    days: int = Query(30, ge=1, le=365, description="Delete items older than X days"),
    db: AsyncSession = Depends(get_db),
) -> DeleteItemsResponse:
    """Delete items older than the specified number of days.

    Starred items are preserved.
    """
    cutoff = datetime.utcnow() - timedelta(days=days)

    stmt = delete(Item).where(
        Item.fetched_at < cutoff,
        Item.is_starred == False,  # noqa: E712
    )
    result = await db.execute(stmt)

    deleted = result.rowcount
    logger.info(f"Deleted {deleted} items older than {days} days")
    return DeleteItemsResponse(
        deleted_count=deleted,
        message=f"Deleted {deleted} items older than {days} days",
    )


@router.delete("/admin/items/source/{source_id}", response_model=DeleteItemsResponse)
async def delete_items_by_source(
    source_id: int,
    db: AsyncSession = Depends(get_db),
) -> DeleteItemsResponse:
    """Delete all items from a specific source.

    Starred items are preserved.
    """
    # Verify source exists
    source = await db.scalar(select(Source).where(Source.id == source_id))
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    # Get channel IDs for this source
    channel_ids_query = select(Channel.id).where(Channel.source_id == source_id)
    channel_ids_result = await db.execute(channel_ids_query)
    channel_ids = [row[0] for row in channel_ids_result.fetchall()]

    if not channel_ids:
        return DeleteItemsResponse(
            deleted_count=0,
            message=f"No channels found for source '{source.name}'",
        )

    # Delete items belonging to those channels
    stmt = delete(Item).where(
        Item.channel_id.in_(channel_ids),
        Item.is_starred == False,  # noqa: E712
    )
    result = await db.execute(stmt)

    deleted = result.rowcount
    logger.info(f"Deleted {deleted} items from source {source_id}")
    return DeleteItemsResponse(
        deleted_count=deleted,
        message=f"Deleted {deleted} items from source '{source.name}'",
    )


@router.delete("/admin/items/low-priority", response_model=DeleteItemsResponse)
async def delete_low_priority_items(
    db: AsyncSession = Depends(get_db),
) -> DeleteItemsResponse:
    """Delete all LOW priority items.

    Starred items are preserved.
    """
    stmt = delete(Item).where(
        Item.priority == Priority.NONE,
        Item.is_starred == False,  # noqa: E712
    )
    result = await db.execute(stmt)

    deleted = result.rowcount
    logger.info(f"Deleted {deleted} low-priority items")
    return DeleteItemsResponse(
        deleted_count=deleted,
        message=f"Deleted {deleted} low-priority items",
    )


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
    proxy_working = proxy_count  # All proxies in working_proxies are considered working

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


class LogEntry(BaseModel):
    """A single log entry."""

    line: str


class LogsResponse(BaseModel):
    """Response for logs endpoint."""

    lines: list[str]
    source: str
    total_lines: int


@router.get("/admin/logs", response_model=LogsResponse)
async def get_application_logs(
    lines: int = Query(100, ge=1, le=1000, description="Number of lines to return"),
) -> LogsResponse:
    """View recent application logs.

    Returns logs from Docker container or log file.
    """
    # In Docker, we read from stdout which is captured by Docker
    # For now, we provide a basic in-memory log capture
    # A more robust solution would read from a log file or Docker API

    log_lines: list[str] = []
    source = "memory"

    # Try reading from a log file first
    log_file = Path("/app/data/app.log")
    if log_file.exists():
        try:
            with open(log_file) as f:
                # Use deque to efficiently get last N lines
                log_lines = list(deque(f, maxlen=lines))
                source = str(log_file)
        except Exception as e:
            logger.warning(f"Failed to read log file: {e}")

    # If no log file, return a message
    if not log_lines:
        log_lines = [
            "Log viewing requires file-based logging configuration.",
            "Currently logs are sent to stdout (view with 'docker logs').",
            "To enable: configure LOG_FILE=/app/data/app.log in environment.",
        ]
        source = "info"

    return LogsResponse(
        lines=log_lines,
        source=source,
        total_lines=len(log_lines),
    )


class MigrationResponse(BaseModel):
    """Response for migration operations."""

    updated_count: int
    message: str


@router.post("/admin/migrate-llm-retry", response_model=MigrationResponse)
async def migrate_items_for_llm_retry(
    db: AsyncSession = Depends(get_db),
) -> MigrationResponse:
    """Mark existing items without summary as needing LLM processing.

    This is a one-time migration to populate the needs_llm_processing flag
    for items that were fetched when the LLM was unavailable.

    Items WITH a summary are considered already processed and skipped.
    """
    # Count items without summary that aren't already marked
    count_query = select(func.count(Item.id)).where(
        Item.summary.is_(None),
        Item.needs_llm_processing == False,  # noqa: E712
    )
    count = await db.scalar(count_query) or 0

    if count == 0:
        return MigrationResponse(
            updated_count=0,
            message="No items need migration - all items either have summaries or are already marked for retry",
        )

    # Update items without summary to need LLM processing
    stmt = (
        update(Item)
        .where(
            Item.summary.is_(None),
            Item.needs_llm_processing == False,  # noqa: E712
        )
        .values(
            needs_llm_processing=True,
            # Set retry priority based on current priority
            # High/Medium priority items get processed first
        )
    )
    result = await db.execute(stmt)
    updated = result.rowcount

    logger.info(f"Migration: marked {updated} items for LLM retry")
    return MigrationResponse(
        updated_count=updated,
        message=f"Marked {updated} items without summary for LLM retry processing",
    )


class ClassifyResponse(BaseModel):
    """Response for classify items operation."""

    processed: int
    updated: int
    errors: int
    message: str


@router.post("/admin/classify-items", response_model=ClassifyResponse)
async def classify_items_for_confidence(
    limit: int = Query(100, ge=1, le=1000, description="Max items to classify"),
    update_retry_priority: bool = Query(True, description="Update retry_priority based on confidence"),
    force: bool = Query(False, description="Re-classify items that already have confidence"),
    retry_queue_only: bool = Query(False, description="Only classify items in the retry queue"),
    db: AsyncSession = Depends(get_db),
) -> ClassifyResponse:
    """Run classifier on items without relevance confidence.

    This is useful to populate confidence scores for items that were
    fetched before the classifier was available, allowing better
    prioritization of the LLM retry queue.

    Priority thresholds:
    - high: >= 0.5 (likely relevant, process first)
    - edge_case: 0.25-0.5 (uncertain)
    - low: < 0.25 (certainly irrelevant, skipped by default)

    The classifier is fast (embedding-based) compared to LLM processing.
    """
    from sqlalchemy.orm import selectinload
    from services.relevance_filter import create_relevance_filter

    # Create classifier
    relevance_filter = await create_relevance_filter()
    if not relevance_filter:
        raise HTTPException(status_code=503, detail="Classifier service not available")

    # Build query
    query = select(Item).options(selectinload(Item.channel).selectinload(Channel.source))

    # Filter by confidence (unless force)
    if not force:
        from database import json_extract_path
        query = query.where(
            json_extract_path(Item.metadata_, "pre_filter", "relevance_confidence").is_(None)
        )

    # Filter to retry queue only
    if retry_queue_only:
        query = query.where(Item.needs_llm_processing == True)  # noqa: E712

    # Prioritize items that need LLM processing
    query = query.order_by(
        Item.needs_llm_processing.desc(),
        Item.fetched_at.desc()
    ).limit(limit)
    result = await db.execute(query)
    items = result.scalars().all()

    if not items:
        return ClassifyResponse(
            processed=0,
            updated=0,
            errors=0,
            message="No items without confidence scores found",
        )

    processed = 0
    updated = 0
    errors = 0

    for item in items:
        try:
            source_name = item.channel.source.name if item.channel and item.channel.source else ""
            classification = await relevance_filter.classify(
                title=item.title,
                content=item.content,
                source=source_name,
            )

            # Store classifier results in metadata
            # Create new dict to ensure SQLAlchemy detects the change
            new_metadata = dict(item.metadata_) if item.metadata_ else {}
            new_metadata["pre_filter"] = {
                "relevance_confidence": classification.get("relevance_confidence"),
                "ak_suggestion": classification.get("ak"),
                "ak_confidence": classification.get("ak_confidence"),
                "priority_suggestion": classification.get("priority"),
                "priority_confidence": classification.get("priority_confidence"),
                "classified_at": datetime.utcnow().isoformat(),
            }

            # Update retry_priority based on confidence
            # Priority levels:
            # - high: >= 0.5 (likely relevant, process first)
            # - edge_case: 0.25-0.5 (uncertain, process after high)
            # - low: < 0.25 (certainly irrelevant, clear needs_llm_processing)
            if update_retry_priority:
                confidence = classification.get("relevance_confidence", 0.5)
                if confidence >= 0.5:
                    new_metadata["retry_priority"] = "high"
                elif confidence >= 0.25:
                    new_metadata["retry_priority"] = "edge_case"
                else:
                    # Certainly irrelevant - don't process with LLM
                    new_metadata["retry_priority"] = "low"
                    item.needs_llm_processing = False

            # Assign new dict to trigger SQLAlchemy change detection
            item.metadata_ = new_metadata
            processed += 1
            updated += 1

        except Exception as e:
            logger.warning(f"Failed to classify item {item.id}: {e}")
            errors += 1

    await db.commit()

    logger.info(f"Classified {processed} items: {updated} updated, {errors} errors")
    return ClassifyResponse(
        processed=processed,
        updated=updated,
        errors=errors,
        message=f"Classified {processed} items, updated {updated} with confidence scores",
    )


# =============================================================================
# System Stats Dashboard
# =============================================================================


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
    from database import json_extract_path

    # Scheduler status
    scheduler_status = SchedulerStatus(
        running=scheduler.running,
        jobs=get_job_status() if scheduler.running else [],
    )

    # LLM Worker status
    llm_worker = get_llm_worker()
    if llm_worker:
        llm_status = llm_worker.get_status()
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
        clf_status = classifier_worker.get_status()
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

    # Processing queue stats
    retry_priority = json_extract_path(Item.metadata_, "retry_priority")
    queue_total = await db.scalar(
        select(func.count(Item.id)).where(Item.needs_llm_processing == True)  # noqa: E712
    ) or 0

    # Count by retry_priority
    retry_counts_query = (
        select(retry_priority.label("rp"), func.count(Item.id))
        .where(Item.needs_llm_processing == True)  # noqa: E712
        .group_by("rp")
    )
    retry_result = await db.execute(retry_counts_query)
    by_retry_priority = {row[0] or "unknown": row[1] for row in retry_result.fetchall()}

    # Items awaiting classifier
    awaiting_classifier = await db.scalar(
        select(func.count(Item.id)).where(
            json_extract_path(Item.metadata_, "pre_filter").is_(None)
        )
    ) or 0

    processing_queue = ProcessingQueueStats(
        total=queue_total,
        by_retry_priority=by_retry_priority,
        awaiting_classifier=awaiting_classifier,
    )

    # Item stats
    items_total = await db.scalar(select(func.count(Item.id))) or 0

    priority_counts_query = (
        select(Item.priority, func.count(Item.id))
        .group_by(Item.priority)
    )
    priority_result = await db.execute(priority_counts_query)
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

    unread_count = await db.scalar(
        select(func.count(Item.id)).where(Item.is_read == False)  # noqa: E712
    ) or 0

    starred_count = await db.scalar(
        select(func.count(Item.id)).where(Item.is_starred == True)  # noqa: E712
    ) or 0

    item_stats = ItemStats(
        total=items_total,
        by_priority=by_priority,
        unread=unread_count,
        starred=starred_count,
    )

    return SystemStatsResponse(
        scheduler=scheduler_status,
        llm_worker=llm_worker_status,
        classifier_worker=classifier_worker_status,
        processing_queue=processing_queue,
        items=item_stats,
        timestamp=datetime.utcnow().isoformat(),
    )


# =============================================================================
# Worker Control Endpoints
# =============================================================================


@router.post("/admin/scheduler/start")
async def start_scheduler_endpoint():
    """Start the background scheduler."""
    from services.scheduler import scheduler, start_scheduler

    if scheduler.running:
        return {"status": "already_running", "message": "Scheduler is already running"}

    start_scheduler()
    return {"status": "started", "message": "Scheduler started"}


@router.post("/admin/scheduler/stop")
async def stop_scheduler_endpoint():
    """Stop the background scheduler."""
    from services.scheduler import scheduler, stop_scheduler

    if not scheduler.running:
        return {"status": "already_stopped", "message": "Scheduler is not running"}

    stop_scheduler()
    return {"status": "stopped", "message": "Scheduler stopped"}


@router.post("/admin/llm-worker/start")
async def start_llm_worker_endpoint():
    """Start the LLM worker."""
    from services.llm_worker import get_worker, start_worker

    worker = get_worker()
    if worker and worker._running:
        return {"status": "already_running", "message": "LLM worker is already running"}

    await start_worker()
    return {"status": "started", "message": "LLM worker started"}


@router.post("/admin/llm-worker/stop")
async def stop_llm_worker_endpoint():
    """Stop the LLM worker."""
    from services.llm_worker import get_worker, stop_worker

    worker = get_worker()
    if not worker or not worker._running:
        return {"status": "already_stopped", "message": "LLM worker is not running"}

    await stop_worker()
    return {"status": "stopped", "message": "LLM worker stopped"}


@router.post("/admin/llm-worker/pause")
async def pause_llm_worker_endpoint():
    """Pause the LLM worker."""
    from services.llm_worker import get_worker

    worker = get_worker()
    if not worker:
        raise HTTPException(status_code=503, detail="LLM worker not running")

    if worker._paused:
        return {"status": "already_paused", "message": "LLM worker is already paused"}

    worker.pause()
    return {"status": "paused", "message": "LLM worker paused"}


@router.post("/admin/llm-worker/resume")
async def resume_llm_worker_endpoint():
    """Resume the LLM worker."""
    from services.llm_worker import get_worker

    worker = get_worker()
    if not worker:
        raise HTTPException(status_code=503, detail="LLM worker not running")

    if not worker._paused:
        return {"status": "already_running", "message": "LLM worker is not paused"}

    worker.resume()
    return {"status": "resumed", "message": "LLM worker resumed"}


@router.post("/admin/classifier-worker/start")
async def start_classifier_worker_endpoint():
    """Start the classifier worker."""
    from services.classifier_worker import get_classifier_worker, start_classifier_worker

    worker = get_classifier_worker()
    if worker and worker._running:
        return {"status": "already_running", "message": "Classifier worker is already running"}

    await start_classifier_worker()
    return {"status": "started", "message": "Classifier worker started"}


@router.post("/admin/classifier-worker/stop")
async def stop_classifier_worker_endpoint():
    """Stop the classifier worker."""
    from services.classifier_worker import get_classifier_worker, stop_classifier_worker

    worker = get_classifier_worker()
    if not worker or not worker._running:
        return {"status": "already_stopped", "message": "Classifier worker is not running"}

    await stop_classifier_worker()
    return {"status": "stopped", "message": "Classifier worker stopped"}


@router.post("/admin/classifier-worker/pause")
async def pause_classifier_worker_endpoint():
    """Pause the classifier worker."""
    from services.classifier_worker import get_classifier_worker

    worker = get_classifier_worker()
    if not worker:
        raise HTTPException(status_code=503, detail="Classifier worker not running")

    if worker._paused:
        return {"status": "already_paused", "message": "Classifier worker is already paused"}

    worker.pause()
    return {"status": "paused", "message": "Classifier worker paused"}


@router.post("/admin/classifier-worker/resume")
async def resume_classifier_worker_endpoint():
    """Resume the classifier worker."""
    from services.classifier_worker import get_classifier_worker

    worker = get_classifier_worker()
    if not worker:
        raise HTTPException(status_code=503, detail="Classifier worker not running")

    if not worker._paused:
        return {"status": "already_running", "message": "Classifier worker is not paused"}

    worker.resume()
    return {"status": "resumed", "message": "Classifier worker resumed"}


# =============================================================================
# Housekeeping / Data Management
# =============================================================================


# Default retention periods in days
DEFAULT_HOUSEKEEPING_CONFIG = {
    "retention_days_high": 365,
    "retention_days_medium": 180,
    "retention_days_low": 90,
    "retention_days_none": 30,
    "autopurge_enabled": False,
    "exclude_starred": True,
}


class HousekeepingConfig(BaseModel):
    """Housekeeping configuration."""
    retention_days_high: int = 365
    retention_days_medium: int = 180
    retention_days_low: int = 90
    retention_days_none: int = 30
    autopurge_enabled: bool = False
    exclude_starred: bool = True


class CleanupPreview(BaseModel):
    """Preview of items to be deleted."""
    total: int
    by_priority: dict[str, int]
    oldest_item_date: str | None


class CleanupResult(BaseModel):
    """Result of cleanup operation."""
    deleted: int
    by_priority: dict[str, int]


class StorageStats(BaseModel):
    """Storage usage statistics."""
    postgresql_size_bytes: int
    postgresql_size_human: str
    postgresql_items: int = Field(description="Total items in PostgreSQL database")
    postgresql_duplicates: int = Field(description="Items marked as duplicates (similar_to_id set)")
    search_index_size_bytes: int = Field(description="Disk size of semantic search index (nomic embeddings)")
    search_index_size_human: str
    search_index_items: int = Field(description="Items indexed for semantic search")
    duplicate_index_size_bytes: int = Field(description="Disk size of duplicate detection index (paraphrase embeddings)")
    duplicate_index_size_human: str
    duplicate_index_items: int = Field(description="Items indexed for duplicate detection")
    total_size_bytes: int
    total_size_human: str


def _format_bytes(size_bytes: int) -> str:
    """Format bytes as human-readable string."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"


async def get_housekeeping_config(db: AsyncSession) -> dict:
    """Load housekeeping config from database."""
    from models import Setting

    result = await db.execute(
        select(Setting).where(Setting.key == "housekeeping")
    )
    setting = result.scalar_one_or_none()

    if setting and setting.value:
        # Merge with defaults to handle new fields
        config = dict(DEFAULT_HOUSEKEEPING_CONFIG)
        config.update(setting.value)
        return config
    return dict(DEFAULT_HOUSEKEEPING_CONFIG)


async def save_housekeeping_config(db: AsyncSession, config: HousekeepingConfig) -> dict:
    """Save housekeeping config to database."""
    from models import Setting

    result = await db.execute(
        select(Setting).where(Setting.key == "housekeeping")
    )
    setting = result.scalar_one_or_none()

    config_dict = config.model_dump()

    if setting:
        setting.value = config_dict
    else:
        new_setting = Setting(
            key="housekeeping",
            value=config_dict,
            description="Housekeeping and retention configuration",
        )
        db.add(new_setting)

    await db.commit()
    return config_dict


async def get_items_to_delete(
    db: AsyncSession,
    config: dict,
    execute: bool = False,
) -> tuple[int, dict[str, int], str | None]:
    """Get or delete items based on retention config.

    Args:
        db: Database session
        config: Housekeeping configuration
        execute: If True, delete the items; if False, just count them

    Returns:
        Tuple of (total_count, by_priority_counts, oldest_date)
    """
    from sqlalchemy import and_, or_

    now = datetime.utcnow()
    by_priority: dict[str, int] = {}
    total = 0
    oldest_date: datetime | None = None

    exclude_starred = config.get("exclude_starred", True)

    # Process each priority level
    for priority, days_key in [
        (Priority.HIGH, "retention_days_high"),
        (Priority.MEDIUM, "retention_days_medium"),
        (Priority.LOW, "retention_days_low"),
        (Priority.NONE, "retention_days_none"),
    ]:
        retention_days = config.get(days_key, 30)
        cutoff = now - timedelta(days=retention_days)

        # Build the where clause
        conditions = [
            Item.priority == priority,
            Item.fetched_at < cutoff,
        ]
        if exclude_starred:
            conditions.append(Item.is_starred == False)  # noqa: E712

        if execute:
            # Delete items
            stmt = delete(Item).where(and_(*conditions))
            result = await db.execute(stmt)
            count = result.rowcount
        else:
            # Count items
            count_stmt = select(func.count(Item.id)).where(and_(*conditions))
            count = await db.scalar(count_stmt) or 0

            # Get oldest date for this priority
            if count > 0:
                oldest_stmt = (
                    select(func.min(Item.fetched_at))
                    .where(and_(*conditions))
                )
                priority_oldest = await db.scalar(oldest_stmt)
                if priority_oldest and (oldest_date is None or priority_oldest < oldest_date):
                    oldest_date = priority_oldest

        if count > 0:
            by_priority[priority.value] = count
            total += count

    return total, by_priority, oldest_date.isoformat() if oldest_date else None


@router.get("/admin/housekeeping", response_model=HousekeepingConfig)
async def get_housekeeping(
    db: AsyncSession = Depends(get_db),
) -> HousekeepingConfig:
    """Get current housekeeping configuration."""
    config = await get_housekeeping_config(db)
    return HousekeepingConfig(**config)


@router.put("/admin/housekeeping", response_model=HousekeepingConfig)
async def update_housekeeping(
    config: HousekeepingConfig,
    db: AsyncSession = Depends(get_db),
) -> HousekeepingConfig:
    """Update housekeeping configuration."""
    saved = await save_housekeeping_config(db, config)
    logger.info(f"Housekeeping config updated: {saved}")
    return HousekeepingConfig(**saved)


@router.post("/admin/housekeeping/preview", response_model=CleanupPreview)
async def preview_cleanup(
    db: AsyncSession = Depends(get_db),
) -> CleanupPreview:
    """Preview items that would be deleted based on current retention settings."""
    config = await get_housekeeping_config(db)
    total, by_priority, oldest_date = await get_items_to_delete(db, config, execute=False)

    return CleanupPreview(
        total=total,
        by_priority=by_priority,
        oldest_item_date=oldest_date,
    )


@router.post("/admin/housekeeping/cleanup", response_model=CleanupResult)
async def execute_cleanup(
    db: AsyncSession = Depends(get_db),
) -> CleanupResult:
    """Execute cleanup based on current retention settings."""
    config = await get_housekeeping_config(db)
    total, by_priority, _ = await get_items_to_delete(db, config, execute=True)
    await db.commit()

    logger.info(f"Housekeeping cleanup completed: deleted {total} items ({by_priority})")
    return CleanupResult(
        deleted=total,
        by_priority=by_priority,
    )


@router.get("/admin/storage", response_model=StorageStats)
async def get_storage_stats(
    db: AsyncSession = Depends(get_db),
) -> StorageStats:
    """Get storage usage statistics."""
    from services.relevance_filter import create_relevance_filter

    # Get PostgreSQL database size
    pg_size_result = await db.execute(
        select(func.pg_database_size(func.current_database()))
    )
    pg_size_bytes = pg_size_result.scalar() or 0

    # Get item count
    items_count = await db.scalar(select(func.count(Item.id))) or 0

    # Get duplicate count (items with similar_to_id set)
    duplicates_count = await db.scalar(
        select(func.count(Item.id)).where(Item.similar_to_id.isnot(None))
    ) or 0

    # Get classifier storage stats
    search_size_bytes = 0
    search_items = 0
    duplicate_size_bytes = 0
    duplicate_items = 0

    try:
        relevance_filter = await create_relevance_filter()
        if relevance_filter:
            storage_stats = await relevance_filter.get_storage_stats()
            if storage_stats:
                search_size_bytes = storage_stats.get("search_index_size_bytes", 0)
                search_items = storage_stats.get("search_index_items", 0)
                duplicate_size_bytes = storage_stats.get("duplicate_index_size_bytes", 0)
                duplicate_items = storage_stats.get("duplicate_index_items", 0)
    except Exception as e:
        logger.warning(f"Failed to get classifier storage stats: {e}")

    total_size = pg_size_bytes + search_size_bytes + duplicate_size_bytes

    return StorageStats(
        postgresql_size_bytes=pg_size_bytes,
        postgresql_size_human=_format_bytes(pg_size_bytes),
        postgresql_items=items_count,
        postgresql_duplicates=duplicates_count,
        search_index_size_bytes=search_size_bytes,
        search_index_size_human=_format_bytes(search_size_bytes),
        search_index_items=search_items,
        duplicate_index_size_bytes=duplicate_size_bytes,
        duplicate_index_size_human=_format_bytes(duplicate_size_bytes),
        duplicate_index_items=duplicate_items,
        total_size_bytes=total_size,
        total_size_human=_format_bytes(total_size),
    )
