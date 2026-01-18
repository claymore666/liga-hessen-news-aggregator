"""API endpoints for dashboard statistics."""

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db, json_extract_path
from models import Channel, Item, ItemEvent, Priority, Rule, Source
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


class ClassifierAgreementStats(BaseModel):
    """Response model for classifier agreement statistics."""
    period_days: int
    total_items: int
    items_with_agreement: int
    relevance: dict[str, Any]
    priority: dict[str, Any]
    ak: dict[str, Any]
    by_classifier_version: dict[str, dict[str, Any]] | None = None


@router.get("/stats/classifier-agreement", response_model=ClassifierAgreementStats)
async def get_classifier_agreement(
    db: AsyncSession = Depends(get_db),
    days: int = Query(7, ge=1, le=90, description="Number of days to analyze"),
) -> ClassifierAgreementStats:
    """Get classifier vs LLM agreement statistics.

    Analyzes items processed by both classifier and LLM to track:
    - Relevance agreement (both say relevant or both say irrelevant)
    - Priority agreement (exact match and within-one-level)
    - AK agreement (exact match and partial overlap)

    Used to monitor classifier quality and detect drift over time.
    """
    since = datetime.utcnow() - timedelta(days=days)

    # Query LLM events with agreement data
    query = (
        select(ItemEvent)
        .where(ItemEvent.event_type == "llm_processed")
        .where(ItemEvent.timestamp >= since)
    )

    result = await db.execute(query)
    events = result.scalars().all()

    # Filter events that have agreement data
    events_with_agreement = [
        e for e in events
        if e.data and e.data.get("agreement")
    ]

    if not events_with_agreement:
        return ClassifierAgreementStats(
            period_days=days,
            total_items=len(events),
            items_with_agreement=0,
            relevance={"agreement_rate": None, "matches": 0, "mismatches": 0},
            priority={"exact_match": None, "within_one": None},
            ak={"exact_match": None, "partial_match": None},
        )

    # Aggregate metrics
    relevance_matches = 0
    relevance_mismatches = 0
    priority_exact = 0
    priority_within_one = 0
    ak_exact = 0
    ak_partial = 0

    # Track by classifier version
    by_version: dict[str, dict[str, int]] = {}

    for event in events_with_agreement:
        agreement = event.data["agreement"]

        # Relevance
        if agreement.get("relevance_match"):
            relevance_matches += 1
        else:
            relevance_mismatches += 1

        # Priority
        if agreement.get("priority_match"):
            priority_exact += 1
        if agreement.get("priority_within_one"):
            priority_within_one += 1

        # AK
        if agreement.get("ak_exact_match"):
            ak_exact += 1
        if agreement.get("ak_partial_match"):
            ak_partial += 1

        # Track by version
        version = agreement.get("classifier_version") or "unknown"
        if version not in by_version:
            by_version[version] = {
                "total": 0,
                "relevance_matches": 0,
                "priority_exact": 0,
                "ak_exact": 0,
            }
        by_version[version]["total"] += 1
        if agreement.get("relevance_match"):
            by_version[version]["relevance_matches"] += 1
        if agreement.get("priority_match"):
            by_version[version]["priority_exact"] += 1
        if agreement.get("ak_exact_match"):
            by_version[version]["ak_exact"] += 1

    total = len(events_with_agreement)

    # Compute rates
    relevance_rate = relevance_matches / total if total > 0 else None
    priority_exact_rate = priority_exact / total if total > 0 else None
    priority_within_one_rate = priority_within_one / total if total > 0 else None
    ak_exact_rate = ak_exact / total if total > 0 else None
    ak_partial_rate = ak_partial / total if total > 0 else None

    # Compute rates by version
    by_version_stats = {}
    for version, counts in by_version.items():
        v_total = counts["total"]
        by_version_stats[version] = {
            "total": v_total,
            "relevance_rate": counts["relevance_matches"] / v_total if v_total > 0 else None,
            "priority_exact_rate": counts["priority_exact"] / v_total if v_total > 0 else None,
            "ak_exact_rate": counts["ak_exact"] / v_total if v_total > 0 else None,
        }

    return ClassifierAgreementStats(
        period_days=days,
        total_items=len(events),
        items_with_agreement=total,
        relevance={
            "agreement_rate": round(relevance_rate, 3) if relevance_rate else None,
            "matches": relevance_matches,
            "mismatches": relevance_mismatches,
        },
        priority={
            "exact_match": round(priority_exact_rate, 3) if priority_exact_rate else None,
            "within_one": round(priority_within_one_rate, 3) if priority_within_one_rate else None,
            "exact_count": priority_exact,
            "within_one_count": priority_within_one,
        },
        ak={
            "exact_match": round(ak_exact_rate, 3) if ak_exact_rate else None,
            "partial_match": round(ak_partial_rate, 3) if ak_partial_rate else None,
            "exact_count": ak_exact,
            "partial_count": ak_partial,
        },
        by_classifier_version=by_version_stats if len(by_version_stats) > 1 else None,
    )
