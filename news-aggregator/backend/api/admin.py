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
    except Exception:
        pass

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
    except Exception:
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
    db: AsyncSession = Depends(get_db),
) -> ClassifyResponse:
    """Run classifier on items without relevance confidence.

    This is useful to populate confidence scores for items that were
    fetched before the classifier was available, allowing better
    prioritization of the LLM retry queue.

    The classifier is fast (embedding-based) compared to LLM processing.
    """
    from sqlalchemy.orm import selectinload
    from services.relevance_filter import create_relevance_filter

    # Create classifier
    relevance_filter = await create_relevance_filter()
    if not relevance_filter:
        raise HTTPException(status_code=503, detail="Classifier service not available")

    # Query items without relevance_confidence
    # Prioritize items that need LLM processing (retry queue)
    query = (
        select(Item)
        .options(selectinload(Item.channel).selectinload(Channel.source))
        .where(
            func.json_extract(Item.metadata_, "$.pre_filter.relevance_confidence").is_(None)
        )
        .order_by(
            Item.needs_llm_processing.desc(),  # Items needing retry first
            Item.fetched_at.desc()
        )
        .limit(limit)
    )
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

            # Update retry_priority if item needs LLM processing
            if update_retry_priority and item.needs_llm_processing:
                confidence = classification.get("relevance_confidence", 0.5)
                if confidence >= 0.5:
                    new_metadata["retry_priority"] = "high"
                else:
                    new_metadata["retry_priority"] = "edge_case"

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
