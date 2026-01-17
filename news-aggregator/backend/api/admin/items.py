"""Admin endpoints for item management."""

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import delete, select, func, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db
from models import Channel, Item, Source, Priority

logger = logging.getLogger(__name__)
router = APIRouter()


class DeleteItemsResponse(BaseModel):
    """Response for delete items operation."""
    deleted_count: int
    message: str


class MigrationResponse(BaseModel):
    """Response for migration operations."""
    updated_count: int
    message: str


class ClassifyResponse(BaseModel):
    """Response for classify items operation."""
    processed: int
    updated: int
    errors: int
    message: str


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

    # Get items without summaries (eager load channel/source for processor)
    result = await db.execute(
        select(Item)
        .options(selectinload(Item.channel).selectinload(Channel.source))
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
        .values(needs_llm_processing=True)
    )
    result = await db.execute(stmt)
    updated = result.rowcount

    logger.info(f"Migration: marked {updated} items for LLM retry")
    return MigrationResponse(
        updated_count=updated,
        message=f"Marked {updated} items without summary for LLM retry processing",
    )


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
            if update_retry_priority:
                confidence = classification.get("relevance_confidence", 0.5)
                if confidence >= 0.5:
                    new_metadata["retry_priority"] = "high"
                elif confidence >= 0.25:
                    new_metadata["retry_priority"] = "edge_case"
                else:
                    new_metadata["retry_priority"] = "low"
                    item.needs_llm_processing = False

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
