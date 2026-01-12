"""API endpoints for dashboard statistics."""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import Channel, Item, Priority, Rule, Source
from schemas import ChannelStats, SourceStats, StatsResponse

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
    # Relevant items = everything except NONE priority
    relevant_items = await db.scalar(
        select(func.count(Item.id)).where(Item.priority != Priority.NONE)
    ) or 0
    unread_items = await db.scalar(
        select(func.count(Item.id)).where(
            Item.is_read == False,  # noqa: E712
            Item.priority != Priority.NONE  # Only count unread relevant items
        )
    ) or 0
    starred_items = await db.scalar(
        select(func.count(Item.id)).where(Item.is_starred == True)  # noqa: E712
    ) or 0
    # High priority items
    high_items = await db.scalar(
        select(func.count(Item.id)).where(Item.priority == Priority.HIGH)
    ) or 0
    # Medium priority items
    medium_items_count = await db.scalar(
        select(func.count(Item.id)).where(Item.priority == Priority.MEDIUM)
    ) or 0

    # Source (organization) counts
    sources_count = await db.scalar(select(func.count(Source.id))) or 0
    enabled_sources = await db.scalar(
        select(func.count(Source.id)).where(Source.enabled == True)  # noqa: E712
    ) or 0

    # Channel counts
    channels_count = await db.scalar(select(func.count(Channel.id))) or 0
    enabled_channels = await db.scalar(
        select(func.count(Channel.id))
        .join(Source)
        .where(
            Channel.enabled == True,  # noqa: E712
            Source.enabled == True,  # noqa: E712
        )
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

    # Low priority count for frontend
    low_items = await db.scalar(
        select(func.count(Item.id)).where(Item.priority == Priority.LOW)
    ) or 0
    # None priority count (not relevant)
    none_items = await db.scalar(
        select(func.count(Item.id)).where(Item.priority == Priority.NONE)
    ) or 0

    # Last fetch time (from channels now)
    last_fetch = await db.scalar(
        select(func.max(Channel.last_fetch_at)).where(Channel.last_fetch_at.isnot(None))
    )

    return StatsResponse(
        total_items=total_items,
        relevant_items=relevant_items,
        unread_items=unread_items,
        starred_items=starred_items,
        high_items=high_items,
        medium_items=medium_items_count,
        sources_count=sources_count,
        channels_count=channels_count,
        enabled_sources=enabled_sources,
        enabled_channels=enabled_channels,
        rules_count=rules_count,
        items_today=items_today,
        items_this_week=items_this_week,
        items_by_priority={
            "high": high_items,
            "medium": medium_items_count,
            "low": low_items,
            "none": none_items,
        },
        last_fetch_at=last_fetch.isoformat() if last_fetch else None,
    )


@router.get("/stats/by-source", response_model=list[SourceStats])
async def get_stats_by_source(
    db: AsyncSession = Depends(get_db),
    source_id: int | None = None,
) -> list[SourceStats]:
    """Get item counts grouped by source (organization).

    Aggregates items across all channels for each source.

    Args:
        source_id: Filter by specific source ID
    """
    # Subquery to count items per source through channels
    item_counts = (
        select(
            Channel.source_id,
            func.count(Item.id).label("item_count"),
            func.sum(case((Item.is_read == False, 1), else_=0)).label("unread_count"),  # noqa: E712
        )
        .outerjoin(Item, Channel.id == Item.channel_id)
        .group_by(Channel.source_id)
        .subquery()
    )

    # Main query joining sources with item counts
    query = (
        select(
            Source.id,
            Source.name,
            Source.is_stakeholder,
            Source.enabled,
            func.count(Channel.id).label("channel_count"),
            func.coalesce(item_counts.c.item_count, 0).label("item_count"),
            func.coalesce(item_counts.c.unread_count, 0).label("unread_count"),
        )
        .outerjoin(Channel, Source.id == Channel.source_id)
        .outerjoin(item_counts, Source.id == item_counts.c.source_id)
        .group_by(Source.id, item_counts.c.item_count, item_counts.c.unread_count)
    )

    if source_id:
        query = query.where(Source.id == source_id)

    query = query.order_by(Source.name)

    result = await db.execute(query)
    rows = result.all()

    return [
        SourceStats(
            source_id=row.id,
            name=row.name,
            is_stakeholder=row.is_stakeholder,
            enabled=row.enabled,
            channel_count=row.channel_count or 0,
            item_count=row.item_count or 0,
            unread_count=row.unread_count or 0,
        )
        for row in rows
    ]


@router.get("/stats/by-channel", response_model=list[ChannelStats])
async def get_stats_by_channel(
    db: AsyncSession = Depends(get_db),
    source_id: int | None = None,
    connector_type: str | None = None,
) -> list[ChannelStats]:
    """Get item counts grouped by channel.

    Args:
        source_id: Filter by specific source ID
        connector_type: Filter by connector type (e.g., 'rss', 'x_scraper')
    """
    query = (
        select(
            Channel.id,
            Channel.source_id,
            Source.name.label("source_name"),
            Channel.connector_type,
            Channel.name,
            Channel.enabled,
            Channel.last_fetch_at,
            Channel.last_error,
            func.count(Item.id).label("item_count"),
            func.sum(case((Item.is_read == False, 1), else_=0)).label("unread_count"),  # noqa: E712
        )
        .join(Source, Channel.source_id == Source.id)
        .outerjoin(Item, Channel.id == Item.channel_id)
        .group_by(Channel.id, Source.name)
    )

    if source_id:
        query = query.where(Channel.source_id == source_id)
    if connector_type:
        query = query.where(Channel.connector_type == connector_type)

    query = query.order_by(Source.name, Channel.connector_type)

    result = await db.execute(query)
    rows = result.all()

    return [
        ChannelStats(
            channel_id=row.id,
            source_id=row.source_id,
            source_name=row.source_name,
            connector_type=row.connector_type,
            name=row.name,
            enabled=row.enabled,
            item_count=row.item_count or 0,
            unread_count=row.unread_count or 0,
            last_fetch_at=row.last_fetch_at,
            last_error=row.last_error,
        )
        for row in rows
    ]


@router.get("/stats/by-connector")
async def get_stats_by_connector(
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Get aggregated stats grouped by connector type."""
    query = (
        select(
            Channel.connector_type,
            func.count(Channel.id.distinct()).label("channel_count"),
            func.count(Item.id).label("item_count"),
            func.sum(case((Item.is_read == False, 1), else_=0)).label("unread_count"),  # noqa: E712
        )
        .outerjoin(Item, Channel.id == Item.channel_id)
        .group_by(Channel.connector_type)
        .order_by(Channel.connector_type)
    )

    result = await db.execute(query)
    rows = result.all()

    return [
        {
            "connector_type": row.connector_type.value if hasattr(row.connector_type, 'value') else row.connector_type,
            "channel_count": row.channel_count or 0,
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
