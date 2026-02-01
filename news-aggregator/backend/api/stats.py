"""API endpoints for dashboard statistics."""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, case, func, select, text
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

    # Combine all item counts into a single query
    item_stats_row = (await db.execute(
        select(
            func.count(Item.id).label("total"),
            func.count(Item.id).filter(Item.priority != Priority.NONE).label("relevant"),
            func.count(Item.id).filter(Item.is_read == False, Item.priority != Priority.NONE).label("unread"),  # noqa: E712
            func.count(Item.id).filter(Item.is_starred == True).label("starred"),  # noqa: E712
            func.count(Item.id).filter(Item.priority == Priority.HIGH).label("high"),
            func.count(Item.id).filter(Item.priority == Priority.MEDIUM).label("medium"),
            func.count(Item.id).filter(Item.priority == Priority.LOW).label("low"),
            func.count(Item.id).filter(Item.priority == Priority.NONE).label("none_p"),
            func.count(Item.id).filter(Item.fetched_at >= today_start).label("today"),
            func.count(Item.id).filter(Item.fetched_at >= week_start).label("week"),
        )
    )).one()

    # Source/channel/rule counts in a single query
    sources_count = await db.scalar(select(func.count(Source.id))) or 0
    enabled_sources = await db.scalar(
        select(func.count(Source.id)).where(Source.enabled == True)  # noqa: E712
    ) or 0
    channels_count = await db.scalar(select(func.count(Channel.id))) or 0
    enabled_channels = await db.scalar(
        select(func.count(Channel.id)).join(Source).where(
            Channel.enabled == True,  # noqa: E712
            Source.enabled == True,  # noqa: E712
        )
    ) or 0
    rules_count = await db.scalar(select(func.count(Rule.id))) or 0

    last_fetch = await db.scalar(
        select(func.max(Channel.last_fetch_at)).where(Channel.last_fetch_at.isnot(None))
    )

    return StatsResponse(
        total_items=item_stats_row.total,
        relevant_items=item_stats_row.relevant,
        unread_items=item_stats_row.unread,
        starred_items=item_stats_row.starred,
        high_items=item_stats_row.high,
        medium_items=item_stats_row.medium,
        sources_count=sources_count,
        channels_count=channels_count,
        enabled_sources=enabled_sources,
        enabled_channels=enabled_channels,
        rules_count=rules_count,
        items_today=item_stats_row.today,
        items_this_week=item_stats_row.week,
        items_by_priority={
            "high": item_stats_row.high,
            "medium": item_stats_row.medium,
            "low": item_stats_row.low,
            "none": item_stats_row.none_p,
        },
        last_fetch_at=last_fetch.isoformat() if last_fetch else None,
    )


@router.get("/stats/topic-counts")
async def get_topic_counts(
    db: AsyncSession = Depends(get_db),
    days: int | None = None,
    limit: int = 0,
    priority: str | None = None,
) -> dict:
    """Get topic counts for word cloud.

    Args:
        days: Number of days to look back. Default: 1 (3 on Monday).
        limit: Max topics to return (0 = all).
        priority: Comma-separated priority filter (e.g. 'high,medium').
    """
    now = datetime.utcnow()
    if days is None:
        # Monday = 0, so use 3 days on Monday to cover the weekend
        days = 3 if now.weekday() == 0 else 1

    cutoff = now - timedelta(days=days)

    topic_expr = Item.metadata_["llm_analysis"]["topic"].as_string()

    query = (
        select(
            topic_expr.label("topic"),
            func.count().label("count"),
        )
        .where(
            Item.similar_to_id.is_(None),
            Item.priority != Priority.NONE,
            Item.fetched_at >= cutoff,
            topic_expr.isnot(None),
            topic_expr != "Sonstiges",
        )
        .group_by(topic_expr)
        .order_by(func.count().desc())
    )

    if priority:
        priorities = [p.strip() for p in priority.split(",")]
        query = query.where(Item.priority.in_(priorities))

    if limit > 0:
        query = query.limit(limit)

    result = await db.execute(query)
    rows = result.all()

    # Get total items count for the period
    total_query = (
        select(func.count())
        .select_from(Item)
        .where(
            Item.similar_to_id.is_(None),
            Item.priority != Priority.NONE,
            Item.fetched_at >= cutoff,
        )
    )
    if priority:
        total_query = total_query.where(Item.priority.in_(priorities))

    total = await db.scalar(total_query) or 0

    return {
        "topics": [{"topic": row.topic, "count": row.count} for row in rows],
        "period_days": days,
        "total_items": total,
    }


@router.get("/stats/by-source", response_model=list[SourceStats])
async def get_stats_by_source(
    db: AsyncSession = Depends(get_db),
    source_id: int | None = None,
    days: int | None = None,
    priority: str | None = None,
) -> list[SourceStats]:
    """Get item counts grouped by source (organization).

    Aggregates items across all channels for each source.

    Args:
        source_id: Filter by specific source ID
        days: Filter items to last N days
        priority: Comma-separated priority filter (e.g. 'high,medium')
    """
    # Build item filter conditions
    item_conditions = [Channel.id == Item.channel_id]
    if days is not None:
        cutoff = datetime.utcnow() - timedelta(days=days)
        item_conditions.append(Item.fetched_at >= cutoff)
    if priority:
        priorities = [p.strip() for p in priority.split(",")]
        item_conditions.append(Item.priority.in_(priorities))

    # Subquery to count items per source through channels
    item_counts = (
        select(
            Channel.source_id,
            func.count(Item.id).label("item_count"),
            func.sum(case((Item.is_read == False, 1), else_=0)).label("unread_count"),  # noqa: E712
        )
        .outerjoin(Item, and_(*item_conditions))
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


@router.get("/stats/source-donut")
async def get_source_donut(
    db: AsyncSession = Depends(get_db),
    days: int | None = None,
    priority: str | None = None,
    resolve_ga: str | None = None,
) -> list[dict]:
    """Get source counts for donut chart, with optional Google Alerts resolution.

    Returns a flat list of {name, count} suitable for a donut chart.
    When resolve_ga is set, Google Alerts items are broken out by keyword or
    source domain instead of being lumped under their parent source.

    Args:
        days: Filter items to last N days.
        priority: Comma-separated priority filter.
        resolve_ga: None (default), 'keyword' (GA channel name), or 'source' (source_domain).
    """
    base_conditions = []
    if days is not None:
        cutoff = datetime.utcnow() - timedelta(days=days)
        base_conditions.append(Item.fetched_at >= cutoff)
    if priority:
        priorities = [p.strip() for p in priority.split(",")]
        base_conditions.append(Item.priority.in_(priorities))

    results: dict[str, int] = {}

    if resolve_ga:
        # Non-GA items: group by source name
        non_ga_query = (
            select(
                Source.name.label("name"),
                func.count(Item.id).label("count"),
            )
            .select_from(Item)
            .join(Channel, Channel.id == Item.channel_id)
            .join(Source, Source.id == Channel.source_id)
            .where(Channel.connector_type != "google_alerts", *base_conditions)
            .group_by(Source.name)
        )
        for row in (await db.execute(non_ga_query)).all():
            results[row.name] = results.get(row.name, 0) + row.count

        # GA items: resolve by keyword or source domain
        if resolve_ga == "source":
            source_domain = Item.metadata_["source_domain"].as_string()
            ga_query = (
                select(
                    source_domain.label("name"),
                    func.count().label("count"),
                )
                .select_from(Item)
                .join(Channel, Channel.id == Item.channel_id)
                .where(
                    Channel.connector_type == "google_alerts",
                    source_domain.isnot(None),
                    source_domain != "",
                    *base_conditions,
                )
                .group_by(source_domain)
            )
        else:
            # keyword = channel name
            ga_query = (
                select(
                    Channel.name.label("name"),
                    func.count().label("count"),
                )
                .select_from(Item)
                .join(Channel, Channel.id == Item.channel_id)
                .where(Channel.connector_type == "google_alerts", *base_conditions)
                .group_by(Channel.name)
            )
        for row in (await db.execute(ga_query)).all():
            name = row.name or "Unbekannt"
            results[name] = results.get(name, 0) + row.count
    else:
        # Default: group everything by source name
        query = (
            select(
                Source.name.label("name"),
                func.count(Item.id).label("count"),
            )
            .select_from(Item)
            .join(Channel, Channel.id == Item.channel_id)
            .join(Source, Source.id == Channel.source_id)
        )
        if base_conditions:
            query = query.where(*base_conditions)
        query = query.group_by(Source.name)
        for row in (await db.execute(query)).all():
            results[row.name] = row.count

    sorted_results = sorted(results.items(), key=lambda x: x[1], reverse=True)
    return [{"name": name, "count": count} for name, count in sorted_results]


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
