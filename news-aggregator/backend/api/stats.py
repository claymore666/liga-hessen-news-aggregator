"""API endpoints for dashboard statistics."""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import Item, Priority, Rule, Source
from schemas import StatsResponse

router = APIRouter()


@router.get("/stats", response_model=StatsResponse)
async def get_stats(
    db: AsyncSession = Depends(get_db),
) -> StatsResponse:
    """Get dashboard statistics."""
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=today_start.weekday())

    # Item counts
    total_items = await db.scalar(select(func.count(Item.id))) or 0
    # Relevant items = everything except LOW priority
    relevant_items = await db.scalar(
        select(func.count(Item.id)).where(Item.priority != Priority.LOW)
    ) or 0
    unread_items = await db.scalar(
        select(func.count(Item.id)).where(
            Item.is_read == False,  # noqa: E712
            Item.priority != Priority.LOW  # Only count unread relevant items
        )
    ) or 0
    starred_items = await db.scalar(
        select(func.count(Item.id)).where(Item.is_starred == True)  # noqa: E712
    ) or 0
    critical_items = await db.scalar(
        select(func.count(Item.id)).where(Item.priority == Priority.CRITICAL)
    ) or 0
    high_priority_items = await db.scalar(
        select(func.count(Item.id)).where(Item.priority == Priority.HIGH)
    ) or 0

    # Source counts
    sources_count = await db.scalar(select(func.count(Source.id))) or 0
    enabled_sources = await db.scalar(
        select(func.count(Source.id)).where(Source.enabled == True)  # noqa: E712
    ) or 0

    # Rule count
    rules_count = await db.scalar(select(func.count(Rule.id))) or 0

    # Time-based counts
    items_today = await db.scalar(
        select(func.count(Item.id)).where(Item.fetched_at >= today_start)
    ) or 0
    items_this_week = await db.scalar(
        select(func.count(Item.id)).where(Item.fetched_at >= week_start)
    ) or 0

    # Medium priority count for frontend
    medium_items = await db.scalar(
        select(func.count(Item.id)).where(Item.priority == Priority.MEDIUM)
    ) or 0
    low_items = await db.scalar(
        select(func.count(Item.id)).where(Item.priority == Priority.LOW)
    ) or 0

    # Last fetch time
    last_fetch = await db.scalar(
        select(func.max(Source.last_fetch_at)).where(Source.last_fetch_at.isnot(None))
    )

    return StatsResponse(
        total_items=total_items,
        relevant_items=relevant_items,
        unread_items=unread_items,
        starred_items=starred_items,
        critical_items=critical_items,
        high_priority_items=high_priority_items,
        sources_count=sources_count,
        enabled_sources=enabled_sources,
        rules_count=rules_count,
        items_today=items_today,
        items_this_week=items_this_week,
        items_by_priority={
            "critical": critical_items,
            "high": high_priority_items,
            "medium": medium_items,
            "low": low_items,
        },
        last_fetch_at=last_fetch.isoformat() if last_fetch else None,
    )


@router.get("/stats/by-source")
async def get_stats_by_source(
    db: AsyncSession = Depends(get_db),
    connector_type: str | None = None,
    source_id: int | None = None,
) -> list[dict]:
    """Get item counts grouped by source.

    Args:
        connector_type: Filter by connector type (e.g., 'rss', 'x_scraper', 'telegram', 'instagram_scraper')
        source_id: Filter by specific source ID
    """
    query = (
        select(
            Source.id,
            Source.name,
            Source.connector_type,
            Source.enabled,
            Source.last_fetch_at,
            Source.last_error,
            func.count(Item.id).label("item_count"),
            func.sum(case((Item.is_read == False, 1), else_=0)).label("unread_count"),  # noqa: E712
        )
        .outerjoin(Item, Source.id == Item.source_id)
        .group_by(Source.id)
    )

    # Apply filters
    if connector_type:
        query = query.where(Source.connector_type == connector_type)
    if source_id:
        query = query.where(Source.id == source_id)

    query = query.order_by(Source.name)

    result = await db.execute(query)
    rows = result.all()

    return [
        {
            "source_id": row.id,
            "name": row.name,
            "connector_type": row.connector_type.value if hasattr(row.connector_type, 'value') else row.connector_type,
            "enabled": row.enabled,
            "item_count": row.item_count or 0,
            "unread_count": row.unread_count or 0,
            "last_fetch_at": row.last_fetch_at.isoformat() if row.last_fetch_at else None,
            "last_error": row.last_error,
        }
        for row in rows
    ]


@router.get("/stats/by-connector")
async def get_stats_by_connector(
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Get aggregated stats grouped by connector type."""
    query = (
        select(
            Source.connector_type,
            func.count(Source.id.distinct()).label("source_count"),
            func.count(Item.id).label("item_count"),
            func.sum(case((Item.is_read == False, 1), else_=0)).label("unread_count"),  # noqa: E712
        )
        .outerjoin(Item, Source.id == Item.source_id)
        .group_by(Source.connector_type)
        .order_by(Source.connector_type)
    )

    result = await db.execute(query)
    rows = result.all()

    return [
        {
            "connector_type": row.connector_type.value if hasattr(row.connector_type, 'value') else row.connector_type,
            "source_count": row.source_count or 0,
            "item_count": row.item_count or 0,
            "unread_count": row.unread_count or 0,
        }
        for row in rows
    ]


@router.get("/stats/by-priority")
async def get_stats_by_priority(
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    """Get item counts grouped by priority."""
    result = {}

    for priority in Priority:
        count = await db.scalar(
            select(func.count(Item.id)).where(Item.priority == priority)
        ) or 0
        result[priority.value] = count

    return result
