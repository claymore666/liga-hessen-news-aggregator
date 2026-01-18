"""Processing analytics API endpoints.

Provides endpoints for:
- Finding low-confidence items
- Detecting classifier vs LLM disagreements
- Viewing item processing history
- Model performance metrics
"""

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db
from models import Item, ItemProcessingLog, ProcessingStepType

router = APIRouter(prefix="/analytics")


class ProcessingLogResponse(BaseModel):
    """Response model for a single processing log entry."""

    id: int
    item_id: int | None
    processing_run_id: str
    step_type: str
    step_order: int
    started_at: datetime
    completed_at: datetime | None
    duration_ms: int | None
    model_name: str | None
    model_provider: str | None
    confidence_score: float | None
    priority_input: str | None
    priority_output: str | None
    priority_changed: bool
    ak_suggestions: list[str] | None
    ak_primary: str | None
    relevant: bool | None
    relevance_score: float | None
    success: bool
    skipped: bool
    skip_reason: str | None
    error_message: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ItemWithLogs(BaseModel):
    """Item with processing history."""

    id: int
    title: str
    priority: str
    assigned_aks: list[str]
    fetched_at: datetime
    source_name: str | None
    logs: list[ProcessingLogResponse]

    model_config = {"from_attributes": True}


class LowConfidenceItem(BaseModel):
    """Response model for low-confidence items."""

    id: int
    title: str
    confidence_score: float
    priority: str
    step_type: str
    ak_primary: str | None
    fetched_at: datetime
    source_name: str | None

    model_config = {"from_attributes": True}


class DisagreementItem(BaseModel):
    """Response model for classifier vs LLM disagreements."""

    id: int
    title: str
    classifier_priority: str | None
    llm_priority: str | None
    classifier_ak: str | None
    llm_ak: str | None
    classifier_confidence: float | None
    llm_relevance_score: float | None
    fetched_at: datetime
    source_name: str | None

    model_config = {"from_attributes": True}


class ModelPerformanceStats(BaseModel):
    """Model performance statistics."""

    model_name: str
    model_provider: str | None
    total_processed: int
    avg_duration_ms: float | None
    priority_changed_count: int
    error_count: int
    avg_confidence: float | None


class AnalyticsSummary(BaseModel):
    """Summary statistics for analytics dashboard."""

    total_logs: int
    logs_by_step: dict[str, int]
    low_confidence_count: int
    priority_changed_count: int
    error_count: int
    avg_processing_time_ms: float | None


@router.get("/summary", response_model=AnalyticsSummary)
async def get_analytics_summary(
    days: int = Query(default=7, ge=1, le=90, description="Days to look back"),
    db: AsyncSession = Depends(get_db),
) -> AnalyticsSummary:
    """Get summary analytics for the processing logs."""
    cutoff = datetime.utcnow() - timedelta(days=days)

    # Total logs
    total_result = await db.execute(
        select(func.count(ItemProcessingLog.id)).where(
            ItemProcessingLog.created_at >= cutoff
        )
    )
    total_logs = total_result.scalar() or 0

    # Logs by step type
    step_counts_result = await db.execute(
        select(
            ItemProcessingLog.step_type,
            func.count(ItemProcessingLog.id),
        )
        .where(ItemProcessingLog.created_at >= cutoff)
        .group_by(ItemProcessingLog.step_type)
    )
    logs_by_step = {row[0]: row[1] for row in step_counts_result.fetchall()}

    # Low confidence count (confidence < 0.5 for pre_filter and classifier steps)
    low_conf_result = await db.execute(
        select(func.count(ItemProcessingLog.id)).where(
            and_(
                ItemProcessingLog.created_at >= cutoff,
                ItemProcessingLog.step_type.in_([
                    ProcessingStepType.PRE_FILTER.value,
                    ProcessingStepType.CLASSIFIER_OVERRIDE.value,
                ]),
                ItemProcessingLog.confidence_score.is_not(None),
                ItemProcessingLog.confidence_score < 0.5,
            )
        )
    )
    low_confidence_count = low_conf_result.scalar() or 0

    # Priority changed count
    priority_changed_result = await db.execute(
        select(func.count(ItemProcessingLog.id)).where(
            and_(
                ItemProcessingLog.created_at >= cutoff,
                ItemProcessingLog.priority_changed == True,  # noqa: E712
            )
        )
    )
    priority_changed_count = priority_changed_result.scalar() or 0

    # Error count
    error_result = await db.execute(
        select(func.count(ItemProcessingLog.id)).where(
            and_(
                ItemProcessingLog.created_at >= cutoff,
                ItemProcessingLog.success == False,  # noqa: E712
            )
        )
    )
    error_count = error_result.scalar() or 0

    # Average processing time
    avg_time_result = await db.execute(
        select(func.avg(ItemProcessingLog.duration_ms)).where(
            and_(
                ItemProcessingLog.created_at >= cutoff,
                ItemProcessingLog.duration_ms.is_not(None),
            )
        )
    )
    avg_processing_time_ms = avg_time_result.scalar()

    return AnalyticsSummary(
        total_logs=total_logs,
        logs_by_step=logs_by_step,
        low_confidence_count=low_confidence_count,
        priority_changed_count=priority_changed_count,
        error_count=error_count,
        avg_processing_time_ms=float(avg_processing_time_ms) if avg_processing_time_ms else None,
    )


@router.get("/low-confidence", response_model=list[LowConfidenceItem])
async def get_low_confidence_items(
    min_confidence: float = Query(default=0.25, ge=0, le=1, description="Minimum confidence"),
    max_confidence: float = Query(default=0.5, ge=0, le=1, description="Maximum confidence"),
    step_type: str | None = Query(default=None, description="Filter by step type"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[LowConfidenceItem]:
    """Get items with low confidence scores (edge cases).

    These are items where the classifier was uncertain, making them
    good candidates for manual review or training data.
    """
    query = (
        select(
            ItemProcessingLog.item_id,
            ItemProcessingLog.confidence_score,
            ItemProcessingLog.step_type,
            ItemProcessingLog.ak_primary,
            ItemProcessingLog.priority_output,
            Item.title,
            Item.fetched_at,
        )
        .join(Item, Item.id == ItemProcessingLog.item_id)
        .where(
            and_(
                ItemProcessingLog.confidence_score >= min_confidence,
                ItemProcessingLog.confidence_score <= max_confidence,
                ItemProcessingLog.confidence_score.is_not(None),
            )
        )
        .order_by(ItemProcessingLog.confidence_score.asc())
        .limit(limit)
        .offset(offset)
    )

    if step_type:
        query = query.where(ItemProcessingLog.step_type == step_type)

    result = await db.execute(query)
    rows = result.fetchall()

    # Get source names for items
    items = []
    for row in rows:
        # Fetch source name
        item_result = await db.execute(
            select(Item)
            .where(Item.id == row.item_id)
            .options(selectinload(Item.channel))
        )
        item = item_result.scalar_one_or_none()
        source_name = None
        if item and item.channel and hasattr(item.channel, "source"):
            source_name = item.channel.source.name if item.channel.source else None

        items.append(
            LowConfidenceItem(
                id=row.item_id,
                title=row.title,
                confidence_score=row.confidence_score,
                priority=row.priority_output or "unknown",
                step_type=row.step_type,
                ak_primary=row.ak_primary,
                fetched_at=row.fetched_at,
                source_name=source_name,
            )
        )

    return items


@router.get("/disagreements", response_model=list[DisagreementItem])
async def get_classifier_llm_disagreements(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[DisagreementItem]:
    """Find items where classifier and LLM disagree on priority or AK.

    Useful for identifying systematic differences between models
    and potential training data for model improvement.
    """
    # Subquery for classifier results
    classifier_subq = (
        select(
            ItemProcessingLog.item_id,
            ItemProcessingLog.priority_output.label("clf_priority"),
            ItemProcessingLog.ak_primary.label("clf_ak"),
            ItemProcessingLog.confidence_score.label("clf_confidence"),
        )
        .where(
            ItemProcessingLog.step_type.in_([
                ProcessingStepType.PRE_FILTER.value,
                ProcessingStepType.CLASSIFIER_OVERRIDE.value,
            ])
        )
        .distinct(ItemProcessingLog.item_id)
        .order_by(ItemProcessingLog.item_id, ItemProcessingLog.created_at.desc())
        .subquery()
    )

    # Subquery for LLM results
    llm_subq = (
        select(
            ItemProcessingLog.item_id,
            ItemProcessingLog.priority_output.label("llm_priority"),
            ItemProcessingLog.ak_primary.label("llm_ak"),
            ItemProcessingLog.relevance_score.label("llm_relevance"),
        )
        .where(ItemProcessingLog.step_type == ProcessingStepType.LLM_ANALYSIS.value)
        .distinct(ItemProcessingLog.item_id)
        .order_by(ItemProcessingLog.item_id, ItemProcessingLog.created_at.desc())
        .subquery()
    )

    # Join and find disagreements
    query = (
        select(
            Item.id,
            Item.title,
            Item.fetched_at,
            classifier_subq.c.clf_priority,
            classifier_subq.c.clf_ak,
            classifier_subq.c.clf_confidence,
            llm_subq.c.llm_priority,
            llm_subq.c.llm_ak,
            llm_subq.c.llm_relevance,
        )
        .join(classifier_subq, Item.id == classifier_subq.c.item_id)
        .join(llm_subq, Item.id == llm_subq.c.item_id)
        .where(
            (classifier_subq.c.clf_priority != llm_subq.c.llm_priority)
            | (classifier_subq.c.clf_ak != llm_subq.c.llm_ak)
        )
        .order_by(Item.fetched_at.desc())
        .limit(limit)
        .offset(offset)
    )

    result = await db.execute(query)
    rows = result.fetchall()

    # Get source names
    items = []
    for row in rows:
        item_result = await db.execute(
            select(Item)
            .where(Item.id == row.id)
            .options(selectinload(Item.channel))
        )
        item = item_result.scalar_one_or_none()
        source_name = None
        if item and item.channel and hasattr(item.channel, "source"):
            source_name = item.channel.source.name if item.channel.source else None

        items.append(
            DisagreementItem(
                id=row.id,
                title=row.title,
                classifier_priority=row.clf_priority,
                llm_priority=row.llm_priority,
                classifier_ak=row.clf_ak,
                llm_ak=row.llm_ak,
                classifier_confidence=row.clf_confidence,
                llm_relevance_score=row.llm_relevance,
                fetched_at=row.fetched_at,
                source_name=source_name,
            )
        )

    return items


@router.get("/item/{item_id}/history", response_model=ItemWithLogs)
async def get_item_processing_history(
    item_id: int,
    db: AsyncSession = Depends(get_db),
) -> ItemWithLogs:
    """Get full processing history for a specific item.

    Returns all processing steps in order, allowing reconstruction
    of how the item ended up with its current priority/classification.
    """
    # Get item
    result = await db.execute(
        select(Item)
        .where(Item.id == item_id)
        .options(selectinload(Item.channel))
    )
    item = result.scalar_one_or_none()

    if not item:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Item not found")

    # Get all logs for this item
    logs_result = await db.execute(
        select(ItemProcessingLog)
        .where(ItemProcessingLog.item_id == item_id)
        .order_by(ItemProcessingLog.step_order)
    )
    logs = logs_result.scalars().all()

    source_name = None
    if item.channel and hasattr(item.channel, "source"):
        source_name = item.channel.source.name if item.channel.source else None

    priority_value = item.priority.value if hasattr(item.priority, "value") else str(item.priority)

    return ItemWithLogs(
        id=item.id,
        title=item.title,
        priority=priority_value,
        assigned_aks=item.assigned_aks or [],
        fetched_at=item.fetched_at,
        source_name=source_name,
        logs=[ProcessingLogResponse.model_validate(log) for log in logs],
    )


@router.get("/model-performance", response_model=list[ModelPerformanceStats])
async def get_model_performance(
    days: int = Query(default=7, ge=1, le=90, description="Days to look back"),
    db: AsyncSession = Depends(get_db),
) -> list[ModelPerformanceStats]:
    """Get performance statistics by model.

    Shows processing time, error rates, and confidence distribution
    for each model used in the processing pipeline.
    """
    cutoff = datetime.utcnow() - timedelta(days=days)

    # Use case expressions for counting boolean conditions (works on PostgreSQL)
    from sqlalchemy import case

    query = (
        select(
            ItemProcessingLog.model_name,
            ItemProcessingLog.model_provider,
            func.count(ItemProcessingLog.id).label("total"),
            func.avg(ItemProcessingLog.duration_ms).label("avg_duration"),
            func.sum(
                case((ItemProcessingLog.priority_changed == True, 1), else_=0)  # noqa: E712
            ).label("priority_changed"),
            func.sum(
                case((ItemProcessingLog.success == False, 1), else_=0)  # noqa: E712
            ).label("errors"),
            func.avg(ItemProcessingLog.confidence_score).label("avg_confidence"),
        )
        .where(
            and_(
                ItemProcessingLog.created_at >= cutoff,
                ItemProcessingLog.model_name.is_not(None),
            )
        )
        .group_by(ItemProcessingLog.model_name, ItemProcessingLog.model_provider)
        .order_by(func.count(ItemProcessingLog.id).desc())
    )

    result = await db.execute(query)
    rows = result.fetchall()

    return [
        ModelPerformanceStats(
            model_name=row.model_name,
            model_provider=row.model_provider,
            total_processed=row.total,
            avg_duration_ms=float(row.avg_duration) if row.avg_duration else None,
            priority_changed_count=int(row.priority_changed or 0),
            error_count=int(row.errors or 0),
            avg_confidence=float(row.avg_confidence) if row.avg_confidence else None,
        )
        for row in rows
    ]


@router.get("/recent-errors", response_model=list[ProcessingLogResponse])
async def get_recent_errors(
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> list[ProcessingLogResponse]:
    """Get recent processing errors for debugging."""
    result = await db.execute(
        select(ItemProcessingLog)
        .where(ItemProcessingLog.success == False)  # noqa: E712
        .order_by(ItemProcessingLog.created_at.desc())
        .limit(limit)
    )
    logs = result.scalars().all()

    return [ProcessingLogResponse.model_validate(log) for log in logs]
