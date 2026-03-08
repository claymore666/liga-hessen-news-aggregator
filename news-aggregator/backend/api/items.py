"""API endpoints for news items."""

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.auth import require_admin_key
from database import get_db, async_session_maker, json_extract_path, json_array_overlaps
from models import Channel, Item, ItemEvent, Priority, Source
from pydantic import BaseModel
from schemas import BulkArchiveRequest, BulkArchiveResponse, DuplicateBrief, ItemListResponse, ItemResponse, ItemUpdate, SourceBrief, TopicGroupsResponse, TopicGroup, TopicItemBrief


class BulkUpdateRequest(BaseModel):
    """Request body for bulk item updates."""
    ids: list[int]
    is_read: bool | None = None


def _build_item_response(item: Item) -> ItemResponse:
    """Build ItemResponse with proper duplicate formatting."""
    # Build duplicates list from relationship
    duplicates = []
    if hasattr(item, 'duplicates') and item.duplicates:
        for dup in item.duplicates:
            source_brief = None
            if dup.channel and dup.channel.source:
                source_brief = SourceBrief(id=dup.channel.source.id, name=dup.channel.source.name)
            duplicates.append(DuplicateBrief(
                id=dup.id,
                title=dup.title,
                url=dup.url,
                priority=dup.priority,
                source=source_brief,
                published_at=dup.published_at,
            ))

    # Create base response
    response = ItemResponse.model_validate(item)
    response.duplicates = duplicates

    # Set dedup_pending: true if not yet fully dedup-checked (Phase 2 not done)
    meta = item.metadata_ or {}
    response.dedup_pending = (
        item.similar_to_id is None
        and not meta.get("dedup_phase2")
    )

    return response

router = APIRouter()
logger = logging.getLogger(__name__)


def _safe_priority(value: str) -> Priority:
    """Coerce a raw priority string to a Priority enum, defaulting to NONE."""
    try:
        return Priority(value)
    except (ValueError, KeyError):
        return Priority.NONE


def get_client_ip(request: Request) -> str | None:
    """Extract client IP from request, handling proxies."""
    # Check for forwarded header (behind proxy)
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    # Direct connection
    return request.client.host if request.client else None


@router.get("/items", response_model=ItemListResponse)
async def list_items(
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    source_id: int | None = None,
    channel_id: int | None = None,
    priority: str | None = Query(None, description="Filter by priority (single value or comma-separated: high,medium)"),
    is_read: bool | None = None,
    is_starred: bool | None = None,
    is_archived: bool | None = Query(None, description="Filter by archive status (default: exclude archived)"),
    days: int | None = Query(None, description="Filter to last N days by fetched_at"),
    since: datetime | None = None,
    until: datetime | None = None,
    search: str | None = None,
    relevant_only: bool = Query(True, description="Exclude LOW priority items (not Liga-relevant)"),
    connector_type: str | None = Query(None, description="Filter by connector type (rss, x_scraper, etc.)"),
    assigned_ak: str | None = Query(None, description="Filter by Arbeitskreis (comma-separated: AK1,AK2,AK3)"),
    sort_by: str = Query("date", description="Sort by: date, priority, source"),
    sort_order: str = Query("desc", description="Sort order: asc, desc"),
    group_duplicates: bool = Query(True, description="Group duplicate articles under primary item"),
) -> ItemListResponse:
    """List items with filtering and pagination.

    By default, only shows relevant items (high, medium, low priority).
    Set relevant_only=false to include all items including NONE priority.
    By default, archived items are excluded. Set is_archived=true to show only archived,
    or is_archived=false to explicitly exclude them.
    When group_duplicates=true (default), duplicate articles are nested under their primary item.
    """
    query = select(Item).options(
        selectinload(Item.channel).selectinload(Channel.source),
        selectinload(Item.duplicates).selectinload(Item.channel).selectinload(Channel.source),
    )

    # Exclude duplicates from main list (they appear nested under primary)
    if group_duplicates:
        query = query.where(Item.similar_to_id.is_(None))

    # Apply filters
    if channel_id is not None:
        query = query.where(Item.channel_id == channel_id)
    elif source_id is not None:
        # Filter by source through channel
        query = query.join(Channel).where(Channel.source_id == source_id)
    if priority is not None:
        # Support comma-separated priority values
        priority_values = [p.strip() for p in priority.split(",") if p.strip()]
        if len(priority_values) == 1:
            query = query.where(Item.priority == priority_values[0])
        elif len(priority_values) > 1:
            query = query.where(Item.priority.in_(priority_values))
    elif relevant_only:
        # Exclude LOW priority items (not Liga-relevant)
        query = query.where(Item.priority != Priority.NONE)
    if is_read is not None:
        query = query.where(Item.is_read == is_read)
    if is_starred is not None:
        query = query.where(Item.is_starred == is_starred)
    if is_archived is not None:
        query = query.where(Item.is_archived == is_archived)
    else:
        # By default, exclude archived items
        query = query.where(Item.is_archived == False)  # noqa: E712
    if days is not None:
        cutoff = datetime.utcnow() - timedelta(days=days)
        query = query.where(Item.fetched_at >= cutoff)
    if since is not None:
        # Strip timezone info for PostgreSQL TIMESTAMP WITHOUT TIME ZONE compatibility
        since_naive = since.replace(tzinfo=None) if since.tzinfo else since
        query = query.where(Item.published_at >= since_naive)
    if until is not None:
        # Strip timezone info for PostgreSQL TIMESTAMP WITHOUT TIME ZONE compatibility
        until_naive = until.replace(tzinfo=None) if until.tzinfo else until
        query = query.where(Item.published_at <= until_naive)
    if search:
        search_pattern = f"%{search}%"
        query = query.where(
            (Item.title.ilike(search_pattern)) | (Item.content.ilike(search_pattern))
        )
    if connector_type is not None:
        # Filter by connector type through channel
        if channel_id is None and source_id is None:
            query = query.join(Channel)
        query = query.where(Channel.connector_type == connector_type)
    if assigned_ak is not None:
        # Support comma-separated AK values
        ak_values = [ak.strip() for ak in assigned_ak.split(",") if ak.strip()]
        from sqlalchemy import or_
        # Filter by assigned_aks (JSON array) or fall back to legacy assigned_ak/metadata
        query = query.where(
            or_(
                json_array_overlaps(Item.assigned_aks, ak_values),
                Item.assigned_ak.in_(ak_values),
                json_extract_path(Item.metadata_, "llm_analysis", "assigned_ak").in_(ak_values)
            )
        )

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total = await db.scalar(count_query) or 0

    # Apply ordering based on sort_by parameter
    if sort_by == "priority":
        # Priority order: high > medium > low > none
        priority_order = case(
            (Item.priority == "high", 1),
            (Item.priority == "medium", 2),
            (Item.priority == "low", 3),
            (Item.priority == "none", 4),
            else_=5,
        )
        if sort_order == "asc":
            query = query.order_by(priority_order.desc(), Item.id.desc())
        else:
            query = query.order_by(priority_order.asc(), Item.id.desc())
    elif sort_by == "source":
        # Need to join Channel and Source if not already joined
        if channel_id is None and source_id is None and connector_type is None:
            query = query.join(Channel, isouter=True).join(Source, isouter=True)
        elif connector_type is not None:
            query = query.join(Source, Channel.source_id == Source.id, isouter=True)
        else:
            query = query.join(Source, Channel.source_id == Source.id, isouter=True)
        if sort_order == "asc":
            query = query.order_by(Source.name.asc(), Item.id.desc())
        else:
            query = query.order_by(Source.name.desc(), Item.id.desc())
    else:
        # Default: sort by date
        if sort_order == "asc":
            query = query.order_by(Item.published_at.asc(), Item.id.asc())
        else:
            query = query.order_by(Item.published_at.desc(), Item.id.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    items = result.scalars().all()

    total_pages = (total + page_size - 1) // page_size

    return ItemListResponse(
        items=[_build_item_response(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get("/items/retry-queue")
async def get_retry_queue_stats(
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get statistics about items waiting for LLM retry processing.

    Returns counts by retry priority (high, unknown, edge_case).
    Items without retry_priority metadata are counted as "unknown".
    """
    from sqlalchemy import literal_column

    # Count by retry priority, using COALESCE to treat NULL as 'unknown'
    priority_expr = func.coalesce(
        json_extract_path(Item.metadata_, "retry_priority"),
        literal_column("'unknown'")
    ).label("priority")

    query = select(
        priority_expr,
        func.count().label("count"),
    ).where(
        Item.needs_llm_processing == True  # noqa: E712
    ).group_by(priority_expr)

    result = await db.execute(query)
    rows = result.fetchall()

    by_priority = {str(row[0]).strip('"'): row[1] for row in rows}
    total = sum(by_priority.values())

    return {
        "total": total,
        "by_priority": by_priority,
        "order": ["high", "unknown", "edge_case", "low"],
    }


@router.get("/items/by-topic", response_model=TopicGroupsResponse)
async def get_items_by_topic(
    db: AsyncSession = Depends(get_db),
    since: str | None = Query(None, description="ISO datetime cutoff (e.g. 2026-01-28T00:00:00)"),
    days: int = Query(7, ge=1, le=90, description="Fallback: look back N days (used if since is not set)"),
    min_group_size: int = Query(2, ge=2, le=10, description="Minimum items per topic group"),
) -> TopicGroupsResponse:
    """Get items grouped by LLM-extracted topic keywords.

    Returns topic groups with at least min_group_size items, plus all
    ungrouped items under a separate list.
    """
    from datetime import timedelta
    from collections import defaultdict
    from sqlalchemy import text as sql_text

    if since:
        parsed = datetime.fromisoformat(since.replace("Z", "+00:00"))
        # Strip timezone for naive timestamp columns
        since_dt = parsed.replace(tzinfo=None)
    else:
        since_dt = datetime.utcnow() - timedelta(days=days)

    # Base filter for all relevant items in the period
    base_where = """
        i.published_at >= :since
        AND i.similar_to_id IS NULL
        AND i.is_archived = false
        AND i.priority != 'none'
    """

    # Common SELECT fields for item brief
    item_fields = """
            i.id,
            i.title,
            i.url,
            i.priority,
            i.published_at,
            i.summary,
            s.name as source_name,
            (i.metadata::jsonb)->>'source_domain' as source_domain,
            i.assigned_aks,
            i.is_read
    """

    # Query items with topic from llm_analysis.topic field
    raw_query = sql_text(f"""
        SELECT
            (i.metadata::jsonb)->'llm_analysis'->>'topic' AS topic,
            {item_fields}
        FROM items i
        LEFT JOIN channels c ON i.channel_id = c.id
        LEFT JOIN sources s ON c.source_id = s.id
        WHERE {base_where}
          AND (i.metadata::jsonb)->'llm_analysis'->>'topic' IS NOT NULL
          AND (i.metadata::jsonb)->'llm_analysis'->>'topic' != 'Sonstiges'
        ORDER BY topic, i.published_at DESC
    """)

    result = await db.execute(raw_query, {"since": since_dt})
    rows = result.fetchall()

    def _make_brief(row, offset=0) -> TopicItemBrief:
        # Raw SQL returns priority as string; coerce unknown values to "none"
        priority = _safe_priority(row[3 + offset])
        return TopicItemBrief(
            id=row[0 + offset],
            title=row[1 + offset],
            url=row[2 + offset],
            priority=priority,
            published_at=row[4 + offset],
            summary=row[5 + offset],
            source_name=row[6 + offset],
            source_domain=row[7 + offset],
            assigned_aks=row[8 + offset] or [],
            is_read=row[9 + offset],
        )

    # Group by topic
    topic_items: dict[str, list[TopicItemBrief]] = defaultdict(list)
    seen_per_topic: dict[str, set[int]] = defaultdict(set)

    for row in rows:
        topic = row[0]
        item_id = row[1]
        if item_id not in seen_per_topic[topic]:
            seen_per_topic[topic].add(item_id)
            topic_items[topic].append(_make_brief(row, offset=1))

    # Filter to groups with enough items
    groups = []
    grouped_item_ids: set[int] = set()
    for topic, items in sorted(topic_items.items(), key=lambda x: -len(x[1])):
        if len(items) >= min_group_size:
            groups.append(TopicGroup(topic=topic, items=items))
            for item in items:
                grouped_item_ids.add(item.id)

    # Query ALL items in period to find ungrouped ones
    all_items_query = sql_text(f"""
        SELECT {item_fields}
        FROM items i
        LEFT JOIN channels c ON i.channel_id = c.id
        LEFT JOIN sources s ON c.source_id = s.id
        WHERE {base_where}
        ORDER BY i.published_at DESC
    """)
    all_result = await db.execute(all_items_query, {"since": since_dt})
    all_rows = all_result.fetchall()

    ungrouped_items = []
    for row in all_rows:
        if row[0] not in grouped_item_ids:
            ungrouped_items.append(_make_brief(row, offset=0))

    # Collect all item IDs to fetch their duplicates
    all_item_ids = grouped_item_ids.copy()
    for item in ungrouped_items:
        all_item_ids.add(item.id)

    # Fetch duplicates for all items in one query
    if all_item_ids:
        dup_query = sql_text("""
            SELECT
                i.similar_to_id,
                i.id,
                i.title,
                i.url,
                i.priority,
                s.name as source_name,
                i.published_at
            FROM items i
            LEFT JOIN channels c ON i.channel_id = c.id
            LEFT JOIN sources s ON c.source_id = s.id
            WHERE i.similar_to_id = ANY(:ids)
              AND i.is_archived = false
            ORDER BY i.similar_to_id, i.published_at DESC
        """)
        dup_result = await db.execute(dup_query, {"ids": list(all_item_ids)})
        dup_rows = dup_result.fetchall()

        # Build lookup: parent_id -> list of DuplicateBrief
        dup_map: dict[int, list[DuplicateBrief]] = defaultdict(list)
        for row in dup_rows:
            parent_id = row[0]
            source_brief = SourceBrief(id=0, name=row[5]) if row[5] else None
            dup_map[parent_id].append(DuplicateBrief(
                id=row[1],
                title=row[2],
                url=row[3],
                priority=_safe_priority(row[4]),
                source=source_brief,
                published_at=row[6],
            ))

        # Attach duplicates to items
        for group in groups:
            for item in group.items:
                item.duplicates = dup_map.get(item.id, [])
        for item in ungrouped_items:
            item.duplicates = dup_map.get(item.id, [])

    return TopicGroupsResponse(
        topics=groups,
        ungrouped_count=len(ungrouped_items),
        ungrouped_items=ungrouped_items,
    )


@router.post("/items/retry-queue/process", dependencies=[Depends(require_admin_key)])
async def trigger_retry_processing(
    batch_size: int = Query(10, ge=1, le=50, description="Number of items to process"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Queue retry items to the LLM worker for processing.

    Finds items with needs_llm_processing=True and adds them to the
    LLM worker's fresh queue for standard processing (with topic extraction
    and prompt tracking).
    """
    from services.llm_worker import get_worker

    worker = get_worker()
    if not worker or not worker._running:
        raise HTTPException(status_code=503, detail="LLM worker is not running")

    # Find items needing processing
    retry_priority = json_extract_path(Item.metadata_, "retry_priority")
    priority_order = case(
        (retry_priority == "high", 1),
        (retry_priority == "unknown", 2),
        (retry_priority == "edge_case", 3),
        else_=4,
    )
    query = (
        select(Item.id)
        .where(
            Item.needs_llm_processing == True,  # noqa: E712
            retry_priority != "low",
        )
        .order_by(priority_order, Item.fetched_at.desc())
        .limit(batch_size)
    )
    result = await db.execute(query)
    item_ids = [row[0] for row in result.all()]

    if not item_ids:
        return {"status": "no_items", "queued": 0, "message": "No items in retry queue"}

    # Queue to LLM worker
    queued = 0
    for item_id in item_ids:
        try:
            worker._fresh_queue.put_nowait(item_id)
            queued += 1
        except Exception:
            break  # Queue full

    return {
        "status": "queued",
        "queued": queued,
        "message": f"Queued {queued} items to LLM worker for processing.",
    }


@router.get("/items/{item_id}", response_model=ItemResponse)
async def get_item(
    item_id: int,
    db: AsyncSession = Depends(get_db),
) -> ItemResponse:
    """Get a single item by ID."""
    query = (
        select(Item)
        .where(Item.id == item_id)
        .options(
            selectinload(Item.channel).selectinload(Channel.source),
            selectinload(Item.duplicates).selectinload(Item.channel).selectinload(Channel.source),
        )
    )
    result = await db.execute(query)
    item = result.scalar_one_or_none()

    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")

    return _build_item_response(item)


@router.patch("/items/{item_id}", response_model=ItemResponse)
async def update_item(
    item_id: int,
    update: ItemUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ItemResponse:
    """Update an item (read status, starred, notes, content, summary, priority).

    When priority or assigned_ak is changed, the item is marked as manually reviewed.
    This creates verified training data for classification.
    """
    query = (
        select(Item)
        .where(Item.id == item_id)
        .options(selectinload(Item.channel).selectinload(Channel.source))
    )
    result = await db.execute(query)
    item = result.scalar_one_or_none()

    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")

    update_data = update.model_dump(exclude_unset=True)
    manually_reviewed = False

    # Handle priority conversion from string to enum
    if "priority" in update_data:
        priority_str = update_data.pop("priority")
        if priority_str:
            priority_map = {
                "high": Priority.HIGH,
                "medium": Priority.MEDIUM,
                "low": Priority.LOW,
                "none": Priority.NONE,
            }
            if priority_str.lower() in priority_map:
                new_priority = priority_map[priority_str.lower()]
                if item.priority != new_priority:
                    item.priority = new_priority
                    manually_reviewed = True
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid priority: {priority_str}. Must be high, medium, low, or none."
                )

    # Check if assigned_aks (array) is being changed
    if "assigned_aks" in update_data:
        new_aks = update_data.get("assigned_aks") or []
        if set(item.assigned_aks or []) != set(new_aks):
            manually_reviewed = True
            item.assigned_aks = new_aks
            # Also sync deprecated assigned_ak field for backward compatibility
            item.assigned_ak = new_aks[0] if new_aks else None
        # Remove from update_data since we already handled it
        del update_data["assigned_aks"]
    # Backward compatibility: handle single assigned_ak
    elif "assigned_ak" in update_data:
        new_ak = update_data.get("assigned_ak")
        new_aks = [new_ak] if new_ak else []
        if set(item.assigned_aks or []) != set(new_aks):
            manually_reviewed = True
            item.assigned_aks = new_aks
            item.assigned_ak = new_ak
        # Remove from update_data since we already handled it
        del update_data["assigned_ak"]

    for key, value in update_data.items():
        setattr(item, key, value)

    # Mark as manually reviewed if priority or AK was changed
    if manually_reviewed:
        item.is_manually_reviewed = True
        item.reviewed_at = datetime.utcnow()

    await db.flush()

    # Record modification event
    from services.item_events import record_event, EVENT_USER_MODIFIED

    changes = {k: v for k, v in update.model_dump(exclude_unset=True).items()}
    if changes:
        await record_event(
            db,
            item_id,
            EVENT_USER_MODIFIED,
            data={"changes": changes},
            ip_address=get_client_ip(request),
        )

    # Re-fetch with duplicates loaded
    query = (
        select(Item)
        .where(Item.id == item_id)
        .options(
            selectinload(Item.channel).selectinload(Channel.source),
            selectinload(Item.duplicates).selectinload(Item.channel).selectinload(Channel.source),
        )
    )
    result = await db.execute(query)
    item = result.scalar_one()

    return _build_item_response(item)


@router.post("/items/{item_id}/read")
async def mark_as_read(
    item_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Mark an item as read."""
    query = select(Item).where(Item.id == item_id)
    result = await db.execute(query)
    item = result.scalar_one_or_none()

    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")

    item.is_read = True

    # Record read event
    from services.item_events import record_event, EVENT_READ

    await record_event(
        db,
        item_id,
        EVENT_READ,
        ip_address=get_client_ip(request),
    )

    return {"status": "ok"}


@router.post("/items/{item_id}/archive")
async def toggle_archive(
    item_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str | bool]:
    """Toggle archive status of an item."""
    query = select(Item).where(Item.id == item_id)
    result = await db.execute(query)
    item = result.scalar_one_or_none()

    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")

    item.is_archived = not item.is_archived

    # Record archive event
    from services.item_events import record_event, EVENT_ARCHIVED

    await record_event(
        db,
        item_id,
        EVENT_ARCHIVED,
        data={"is_archived": item.is_archived},
        ip_address=get_client_ip(request),
    )

    return {"status": "ok", "is_archived": item.is_archived}


@router.post("/items/bulk-archive", response_model=BulkArchiveResponse)
async def bulk_archive(
    request_body: BulkArchiveRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> BulkArchiveResponse:
    """Bulk archive or restore items.

    When archiving/restoring an item that has duplicates or is part of a duplicate group,
    all related items (primary + duplicates) are archived/restored together.
    """
    if not request_body.ids:
        return BulkArchiveResponse(archived=0, item_ids=[])

    # Collect all item IDs to archive (including duplicates)
    all_item_ids: set[int] = set()

    # First pass: get all requested items and their related items
    for item_id in request_body.ids:
        all_item_ids.add(item_id)

        # Get the item to check if it's a duplicate or has duplicates
        query = select(Item).where(Item.id == item_id).options(
            selectinload(Item.duplicates)
        )
        result = await db.execute(query)
        item = result.scalar_one_or_none()

        if item:
            # If this item is a duplicate, also include its primary
            if item.similar_to_id:
                all_item_ids.add(item.similar_to_id)
                # Get siblings (other duplicates of the same primary)
                sibling_query = select(Item.id).where(Item.similar_to_id == item.similar_to_id)
                sibling_result = await db.execute(sibling_query)
                for (sibling_id,) in sibling_result.fetchall():
                    all_item_ids.add(sibling_id)

            # If this item has duplicates, include them all
            if item.duplicates:
                for dup in item.duplicates:
                    all_item_ids.add(dup.id)

    # Now fetch and update all items
    query = select(Item).where(Item.id.in_(list(all_item_ids)))
    result = await db.execute(query)
    items = result.scalars().all()

    archived_count = 0
    for item in items:
        if item.is_archived != request_body.is_archived:
            item.is_archived = request_body.is_archived
            archived_count += 1

    # Record events for all archived items
    if archived_count > 0:
        from services.item_events import record_events_batch, EVENT_ARCHIVED
        ip_address = get_client_ip(request)

        events_data = [
            {
                "item_id": item.id,
                "event_type": EVENT_ARCHIVED,
                "data": {"is_archived": request_body.is_archived, "bulk_archive": True},
                "ip_address": ip_address,
            }
            for item in items
            if item.is_archived == request_body.is_archived  # Only items that changed
        ]
        record_events_batch(db, events_data)

    return BulkArchiveResponse(
        archived=archived_count,
        item_ids=list(all_item_ids)
    )


@router.post("/items/{item_id}/reprocess", dependencies=[Depends(require_admin_key)])
async def reprocess_single_item(
    item_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Reprocess a single item through the LLM.

    Forces reprocessing even if item already has LLM analysis.
    Runs in background and returns immediately.
    """
    # Verify item exists
    query = select(Item).where(Item.id == item_id)
    result = await db.execute(query)
    item = result.scalar_one_or_none()

    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")

    background_tasks.add_task(_reprocess_items_task, [item_id], force=True)

    return {
        "status": "started",
        "item_id": item_id,
        "message": "Reprocessing in background. Check logs for progress.",
    }


async def _refetch_rss_item_task(item_id: int):
    """Background task to re-fetch an RSS/Google Alerts item and extract article content."""
    from services.article_extractor import ArticleExtractor
    from sqlalchemy.orm.attributes import flag_modified

    async with async_session_maker() as db:
        try:
            result = await db.execute(
                select(Item)
                .where(Item.id == item_id)
                .options(selectinload(Item.channel).selectinload(Channel.source))
            )
            item = result.scalar_one_or_none()
            if not item:
                logger.error(f"Item {item_id} not found for re-fetch")
                return

            extractor = ArticleExtractor()
            article = await extractor.fetch_article(item.url)

            if article and article.content and len(article.content) > 100:
                # Combine RSS summary with full article
                rss_summary = item.content
                item.content = f"RSS-Zusammenfassung: {rss_summary}\n\n--- Vollständiger Artikel von {article.source_domain} ---\n\n{article.content}"
                item.metadata_["article_extracted"] = True
                item.metadata_["refetched_at"] = datetime.utcnow().isoformat()
                item.metadata_["linked_articles"] = [{
                    "url": article.url,
                    "title": article.title,
                    "domain": article.source_domain,
                    "content_length": len(article.content),
                }]
                flag_modified(item, "metadata_")

                await db.commit()
                logger.info(f"Re-fetched RSS item {item_id}: {len(article.content)} chars from {article.source_domain}")

                # Reprocess through LLM for better analysis
                await _reprocess_items_task([item_id], force=True)
            else:
                logger.warning(f"No article content extracted for RSS item {item_id}")

        except Exception as e:
            logger.error(f"Error re-fetching RSS item {item_id}: {e}")


async def _refetch_social_item_task(item_id: int):
    """Background task to re-fetch a social media item (Mastodon/Bluesky/Telegram) and extract linked articles."""
    from services.article_extractor import ArticleExtractor
    from sqlalchemy.orm.attributes import flag_modified

    async with async_session_maker() as db:
        try:
            result = await db.execute(
                select(Item)
                .where(Item.id == item_id)
                .options(selectinload(Item.channel).selectinload(Channel.source))
            )
            item = result.scalar_one_or_none()
            if not item:
                logger.error(f"Item {item_id} not found for re-fetch")
                return

            connector_type = item.channel.connector_type
            extractor = ArticleExtractor()

            # Extract URLs from content
            content_text = item.content
            links = extractor.extract_urls_from_text(content_text)

            if not links:
                logger.info(f"No links found in {connector_type} item {item_id}")
                return

            logger.info(f"Found {len(links)} links in {connector_type} item {item_id}: {links}")

            # Try to fetch first valid article
            for link_url in links[:3]:
                # Skip links to the same social media platform
                if any(domain in link_url for domain in ["mastodon.", "social.", "bsky.app", "t.me", "telegram."]):
                    continue

                try:
                    article = await extractor.fetch_article(link_url)
                    if article and article.is_article and len(article.content) > 100:
                        # Keep original post, append article
                        original_content = item.content
                        # Remove the "Toot: " or similar prefix if present for cleaner output
                        if original_content.startswith("Toot: "):
                            original_content = original_content[6:]

                        item.content = f"""{original_content}

---

Verlinkter Artikel von {article.source_domain}:
{article.title or 'Unbekannter Titel'}

{article.content[:4000]}"""

                        item.metadata_["article_extracted"] = True
                        item.metadata_["extracted_links"] = links
                        item.metadata_["linked_articles"] = [{
                            "url": article.url,
                            "title": article.title,
                            "domain": article.source_domain,
                            "content_length": len(article.content),
                        }]
                        item.metadata_["refetched_at"] = datetime.utcnow().isoformat()
                        flag_modified(item, "metadata_")

                        await db.commit()
                        logger.info(f"Re-fetched {connector_type} item {item_id} with article from {article.source_domain} ({len(article.content)} chars)")

                        # Reprocess through LLM for better analysis
                        await _reprocess_items_task([item_id], force=True)
                        return

                except Exception as e:
                    logger.debug(f"Failed to fetch article from {link_url}: {e}")

            logger.warning(f"No valid articles found for {connector_type} item {item_id}")

        except Exception as e:
            logger.error(f"Error re-fetching {connector_type} item {item_id}: {e}")


async def _refetch_item_task(item_id: int):
    """Background task to re-fetch an x_scraper/linkedin item and extract linked articles."""
    from connectors import ConnectorRegistry
    from services.article_extractor import ArticleExtractor

    async with async_session_maker() as db:
        try:
            result = await db.execute(
                select(Item)
                .where(Item.id == item_id)
                .options(selectinload(Item.channel).selectinload(Channel.source))
            )
            item = result.scalar_one_or_none()
            if not item:
                logger.error(f"Item {item_id} not found for re-fetch")
                return

            connector_type = item.channel.connector_type
            if connector_type not in ("x_scraper", "linkedin"):
                logger.warning(f"_refetch_item_task only for x_scraper/linkedin, got {connector_type}")
                return

            # Get the tweet/post URL
            post_url = item.url
            if not post_url:
                logger.warning(f"Item {item_id} has no URL")
                return

            extractor = ArticleExtractor()
            original_text = item.metadata_.get("original_tweet_text") or item.metadata_.get("original_post_text") or item.content
            links = []

            # Method 1: Extract from stored text
            links = extractor.extract_urls_from_text(original_text)

            # Method 2: Check for t.co links in text
            import re
            tco_links = re.findall(r'https?://t\.co/\w+', original_text)
            for tco in tco_links:
                resolved = await extractor.resolve_redirect(tco)
                if resolved and resolved != tco and "t.co" not in resolved:
                    if resolved not in links:
                        links.append(resolved)

            # Method 3: If no links found, scrape the tweet/post page to extract card links
            if not links and post_url and ("x.com" in post_url or "twitter.com" in post_url):
                logger.info(f"No links in text, scraping tweet page for card links: {post_url}")
                card_links = await _scrape_tweet_for_links(post_url)
                links.extend(card_links)

            # Try to fetch first valid article
            for link_url in links[:3]:
                try:
                    article = await extractor.fetch_article(link_url)
                    if article and article.is_article:
                        # Update item content with article
                        author = item.author or "Unknown"
                        combined_content = f"""Tweet von @{author}:
{original_text}

---

Verlinkter Artikel von {article.source_domain}:
{article.title or 'Unbekannter Titel'}

{article.content[:4000]}"""

                        item.content = combined_content
                        item.metadata_ = {
                            **item.metadata_,
                            "extracted_links": links,
                            "linked_articles": [{
                                "url": article.url,
                                "title": article.title,
                                "domain": article.source_domain,
                                "content_length": len(article.content),
                            }],
                            "refetched_at": datetime.utcnow().isoformat(),
                        }

                        await db.flush()
                        await db.commit()
                        logger.info(f"Re-fetched item {item_id} with article from {article.source_domain}")

                        # Now reprocess through LLM
                        await _reprocess_items_task([item_id], force=True)
                        return

                except Exception as e:
                    logger.debug(f"Failed to fetch article from {link_url}: {e}")

            logger.warning(f"No valid articles found for item {item_id}")

        except Exception as e:
            logger.error(f"Error re-fetching item {item_id}: {e}")


async def _scrape_tweet_for_links(tweet_url: str) -> list[str]:
    """Scrape a tweet page to extract article links from cards.

    Uses the shared browser pool to respect concurrency limits.

    Args:
        tweet_url: Full URL to the tweet

    Returns:
        List of article URLs found in the tweet's cards
    """
    import json
    from pathlib import Path
    from playwright_stealth import Stealth
    from services.browser_pool import browser_pool

    links = []
    cookie_file = Path("/app/data/x_cookies.json")

    try:
        async with browser_pool.get_browser() as browser:
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0",
                viewport={"width": 1920, "height": 1080},
                locale="de-DE",
            )

            # Load cookies if available
            if cookie_file.exists():
                with open(cookie_file) as f:
                    cookies = json.load(f)
                await context.add_cookies(cookies)
                logger.debug("Loaded X.com cookies for refetch")

            page = await context.new_page()
            stealth = Stealth()
            await stealth.apply_stealth_async(page)

            await page.goto(tweet_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)  # Wait for JS to render

            # Extract links from tweet cards
            skip_domains = ("x.com", "twitter.com", "t.co", "pic.twitter.com", "pbs.twimg.com")

            # Find all links in the tweet
            all_links = await page.query_selector_all('article a[href*="://"]')
            for link_el in all_links:
                href = await link_el.get_attribute("href")
                if href:
                    # Skip internal X links
                    is_internal = any(d in href for d in skip_domains)
                    if not is_internal and href.startswith("http"):
                        if href not in links:
                            links.append(href)

            # Also check for t.co links and resolve them
            tco_links = await page.query_selector_all('a[href*="t.co"]')
            for link_el in tco_links:
                href = await link_el.get_attribute("href")
                if href and "t.co" in href:
                    from services.article_extractor import ArticleExtractor
                    extractor = ArticleExtractor()
                    resolved = await extractor.resolve_redirect(href)
                    if resolved and "t.co" not in resolved:
                        if resolved not in links:
                            links.append(resolved)

            await context.close()

    except Exception as e:
        logger.warning(f"Error scraping tweet for links: {e}")

    logger.info(f"Scraped {len(links)} links from tweet: {links}")
    return links


@router.post("/items/{item_id}/refetch")
async def refetch_item(
    item_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Re-fetch an item to extract linked article content.

    Supports all connector types:
    - x_scraper/linkedin: Extract links from tweet/post, resolve t.co, fetch articles
    - rss/google_alerts: Fetch full article from item URL (resolves Google redirects)
    - Other connectors: Extract URLs from content and fetch articles

    Runs in background and returns immediately.
    """
    # Verify item exists
    query = (
        select(Item)
        .where(Item.id == item_id)
        .options(selectinload(Item.channel))
    )
    result = await db.execute(query)
    item = result.scalar_one_or_none()

    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")

    if not item.channel:
        raise HTTPException(status_code=400, detail="Item has no channel, cannot re-fetch")

    connector_type = item.channel.connector_type

    # Route to appropriate task based on connector type
    if connector_type in ("x_scraper", "linkedin"):
        background_tasks.add_task(_refetch_item_task, item_id)
    elif connector_type in ("mastodon", "bluesky", "telegram"):
        # Social media posts: extract links from content and follow them
        background_tasks.add_task(_refetch_social_item_task, item_id)
    elif connector_type in ("rss", "google_alerts"):
        background_tasks.add_task(_refetch_rss_item_task, item_id)
    else:
        # For other connectors, use RSS-style refetch (direct URL fetch)
        background_tasks.add_task(_refetch_rss_item_task, item_id)

    return {
        "status": "started",
        "item_id": item_id,
        "connector_type": connector_type,
        "message": "Re-fetching article content in background. Check logs for progress.",
    }


@router.post("/items/bulk-update")
async def bulk_update_items(
    request_body: BulkUpdateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    """Bulk update items (mark read/unread).

    Args:
        request_body: Contains ids list and is_read flag.
                     is_read=True marks as read, is_read=False marks as unread.
    """
    if not request_body.ids:
        return {"updated": 0}

    query = select(Item).where(Item.id.in_(request_body.ids))
    result = await db.execute(query)
    items = result.scalars().all()

    updated = 0
    for item in items:
        if request_body.is_read is not None and item.is_read != request_body.is_read:
            item.is_read = request_body.is_read
            updated += 1

    if updated > 0:
        # Record events in batch for all updated items (more efficient than sequential)
        from services.item_events import record_events_batch, EVENT_READ, EVENT_USER_MODIFIED
        ip_address = get_client_ip(request)

        if request_body.is_read is not None:
            event_type = EVENT_READ if request_body.is_read else EVENT_USER_MODIFIED
            events_data = [
                {
                    "item_id": item.id,
                    "event_type": event_type,
                    "data": {"is_read": request_body.is_read, "bulk_update": True},
                    "ip_address": ip_address,
                }
                for item in items
            ]
            record_events_batch(db, events_data)

    return {"updated": updated}


@router.post("/items/mark-all-read")
async def mark_all_as_read(
    request_body: BulkUpdateRequest | None = None,
    source_id: int | None = None,
    channel_id: int | None = None,
    before: datetime | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    """Mark multiple items as read.

    Supports two modes:
    1. Request body with {ids: [...]} - marks specific items as read
    2. Query params (source_id, channel_id, before) - marks filtered items as read
    """
    # Mode 1: Specific IDs from request body
    if request_body and request_body.ids:
        query = select(Item).where(Item.id.in_(request_body.ids))
        result = await db.execute(query)
        items = result.scalars().all()

        for item in items:
            item.is_read = True

        return {"marked": len(items)}

    # Mode 2: Filter-based bulk mark (legacy)
    query = select(Item).where(Item.is_read == False)  # noqa: E712

    if channel_id is not None:
        query = query.where(Item.channel_id == channel_id)
    elif source_id is not None:
        query = query.join(Channel).where(Channel.source_id == source_id)
    if before is not None:
        query = query.where(Item.published_at <= before)

    result = await db.execute(query)
    items = result.scalars().all()

    for item in items:
        item.is_read = True

    return {"marked": len(items)}


# Background task for reprocessing
async def _reprocess_items_task(item_ids: list[int], force: bool):
    """Background task to reprocess items through LLM.

    Uses the 3-phase read-process-write pattern to avoid holding DB
    connections during long-running LLM calls. Includes topic extraction.
    """
    from services.processor import create_processor_from_settings

    processor = await create_processor_from_settings()
    processed = 0
    errors = 0

    for item_id in item_ids:
        try:
            # Phase 1: Read item data (release connection immediately)
            async with async_session_maker() as db:
                result = await db.execute(
                    select(Item)
                    .where(Item.id == item_id)
                    .options(selectinload(Item.channel).selectinload(Channel.source))
                )
                item = result.scalar_one_or_none()
                if not item:
                    continue

                # Skip if already processed (unless force)
                if not force and item.metadata_.get("llm_analysis"):
                    continue

                source_name = item.channel.source.name if item.channel and item.channel.source else "Unbekannt"
                item_data = {
                    "title": item.title,
                    "content": item.content,
                    "source_name": source_name,
                    "published_at": item.published_at,
                }
                old_metadata = dict(item.metadata_) if item.metadata_ else {}
                old_priority_score = item.priority_score or 0

            # Phase 2: LLM processing — NO connection held
            analysis, conversation = await processor.analyze_from_data_with_messages(item_data)

            # Override self-contradictory output (relevant=False but priority set)
            llm_priority = analysis.get("priority") or analysis.get("priority_suggestion")
            if analysis.get("relevant") is False:
                llm_priority = None

            # Topic extraction via follow-up chat turn
            topic = "Sonstiges"
            topic_suggestion = None
            if analysis.get("relevant") is not False:
                try:
                    topic, topic_suggestion = await processor.extract_topics(conversation)
                except Exception as topic_err:
                    logger.warning(f"Topic extraction failed for item {item_id}: {topic_err}")

            # Phase 3: Quick write — update item in database
            async with async_session_maker() as db:
                result = await db.execute(select(Item).where(Item.id == item_id))
                item = result.scalar_one_or_none()
                if not item:
                    continue

                if analysis.get("summary"):
                    item.summary = analysis["summary"]
                if analysis.get("detailed_analysis"):
                    item.detailed_analysis = analysis["detailed_analysis"]

                # Map LLM priority (matches llm_worker mapping)
                if llm_priority == "critical":
                    item.priority = Priority.HIGH
                    item.priority_score = max(old_priority_score, 95)
                elif llm_priority == "high":
                    item.priority = Priority.HIGH
                    item.priority_score = max(old_priority_score, 90)
                elif llm_priority == "medium":
                    item.priority = Priority.MEDIUM
                    item.priority_score = max(old_priority_score, 70)
                elif llm_priority == "low":
                    item.priority = Priority.LOW
                    item.priority_score = max(old_priority_score, 40)
                else:
                    item.priority = Priority.NONE
                    item.priority_score = min(old_priority_score, 20)

                llm_aks = analysis.get("assigned_aks", [])
                if llm_aks:
                    item.assigned_aks = llm_aks
                    item.assigned_ak = llm_aks[0] if llm_aks else None

                llm_meta = {
                    "relevance_score": analysis.get("relevance_score", 0.5),
                    "priority_suggestion": llm_priority,
                    "assigned_aks": llm_aks,
                    "assigned_ak": llm_aks[0] if llm_aks else None,
                    "tags": analysis.get("tags", []),
                    "topic": topic,
                    "topic_suggestion": topic_suggestion,
                    "reasoning": analysis.get("reasoning"),
                    "provider": analysis.get("_provider"),
                    "model": analysis.get("_model"),
                    "prompt_version": analysis.get("_prompt_version"),
                    "prompt_model": analysis.get("_prompt_model"),
                    "reprocessed_at": datetime.utcnow().isoformat(),
                }
                item.metadata_ = {**old_metadata, "llm_analysis": llm_meta}

                await db.commit()
                processed += 1

            if processed % 10 == 0:
                logger.info(f"Reprocessed {processed}/{len(item_ids)} items")

        except Exception as e:
            logger.error(f"Error reprocessing item {item_id}: {e}")
            errors += 1

    logger.info(f"Reprocessing complete: {processed} processed, {errors} errors")


@router.post("/items/reprocess", dependencies=[Depends(require_admin_key)])
async def reprocess_items(
    background_tasks: BackgroundTasks,
    source_id: int | None = Query(None, description="Only reprocess items from this source"),
    channel_id: int | None = Query(None, description="Only reprocess items from this channel"),
    connector_type: str | None = Query(None, description="Only reprocess items from this connector type (e.g., x_scraper, rss)"),
    priority: Priority | None = Query(None, description="Only reprocess items with this priority"),
    exclude_low: bool = Query(False, description="Exclude LOW priority items (reprocess only relevant items)"),
    limit: int = Query(100, ge=1, le=1000, description="Max items to reprocess"),
    force: bool = Query(False, description="Reprocess even if already has LLM analysis"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Reprocess items through the LLM for priority and summary.

    Runs in background. Check logs for progress.
    """
    query = select(Item.id).order_by(Item.published_at.desc())

    # Join with Channel if we need to filter by source or connector type
    needs_channel_join = source_id is not None or connector_type is not None

    if channel_id is not None:
        query = query.where(Item.channel_id == channel_id)
    elif needs_channel_join:
        query = query.join(Channel)
        if source_id is not None:
            query = query.where(Channel.source_id == source_id)
        if connector_type is not None:
            query = query.where(Channel.connector_type == connector_type)

    # Filter by priority
    if priority is not None:
        query = query.where(Item.priority == priority)
    elif exclude_low:
        query = query.where(Item.priority != Priority.NONE)

    # When not forcing, only select items without LLM analysis
    # Use database-agnostic JSON extract to check if key exists
    if not force:
        query = query.where(
            json_extract_path(Item.metadata_, "llm_analysis").is_(None)
        )

    query = query.limit(limit)
    result = await db.execute(query)
    item_ids = [row[0] for row in result.fetchall()]

    if not item_ids:
        return {"status": "no items to process", "count": 0}

    background_tasks.add_task(_reprocess_items_task, item_ids, force)

    return {
        "status": "started",
        "count": len(item_ids),
        "force": force,
        "message": "Reprocessing in background. Check logs for progress.",
    }


@router.get("/items/{item_id}/history")
async def get_item_history(
    item_id: int,
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Get event history for an item.

    Returns a list of events in reverse chronological order (newest first).
    """
    # Verify item exists
    item_query = select(Item.id).where(Item.id == item_id)
    result = await db.execute(item_query)
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Item not found")

    # Get events
    query = (
        select(ItemEvent)
        .where(ItemEvent.item_id == item_id)
        .order_by(ItemEvent.timestamp.desc())
        .limit(limit)
    )
    result = await db.execute(query)
    events = result.scalars().all()

    return [
        {
            "id": event.id,
            "event_type": event.event_type,
            "timestamp": event.timestamp.isoformat() if event.timestamp else None,
            "ip_address": event.ip_address,
            "data": event.data,
        }
        for event in events
    ]


# --- Title Pre-filter Endpoints ---


class PrefilterBatchRequest(BaseModel):
    """Request body for batch pre-filter."""
    item_ids: list[int] | None = None
    days: int | None = None


@router.post("/items/{item_id}/prefilter", dependencies=[Depends(require_admin_key)])
async def prefilter_item(
    item_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Run title pre-filter (qwen3:8b) on a single item.

    Returns the relevance verdict without triggering full LLM analysis.
    Does NOT unload/reload models — caller manages that.
    """
    from config import settings
    from services.title_prefilter import prefilter_single

    if not settings.title_prefilter_enabled:
        raise HTTPException(status_code=400, detail="Title pre-filter is disabled")

    query = select(Item).where(Item.id == item_id)
    result = await db.execute(query)
    item = result.scalar_one_or_none()

    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")

    # Run pre-filter
    check_result = await prefilter_single(
        base_url=settings.ollama_base_url,
        model=settings.title_prefilter_model,
        title=item.title,
    )

    relevant = check_result["relevant"]
    duration_ms = check_result.get("duration_ms", 0)

    # Update item metadata
    new_metadata = dict(item.metadata_) if item.metadata_ else {}
    new_metadata.pop("needs_title_check", None)
    new_metadata["title_check"] = {
        "relevant": relevant,
        "model": settings.title_prefilter_model,
        "duration_ms": duration_ms,
        "checked_at": datetime.utcnow().isoformat(),
    }
    item.metadata_ = new_metadata

    if not relevant:
        item.needs_llm_processing = False
        item.priority = Priority.NONE
        item.priority_score = 10

    # Record event
    from services.item_events import record_event, EVENT_TITLE_PREFILTER
    await record_event(
        db, item_id, EVENT_TITLE_PREFILTER,
        data={"relevant": relevant, "model": settings.title_prefilter_model, "duration_ms": duration_ms},
    )

    return {
        "id": item_id,
        "title": item.title,
        "relevant": relevant,
        "duration_ms": duration_ms,
    }


async def _prefilter_batch_task(item_ids: list[int]):
    """Background task to run title pre-filter on a batch of items."""
    from config import settings
    from services.title_prefilter import run_prefilter_batch
    from services.item_events import record_event, EVENT_TITLE_PREFILTER

    # Load items
    async with async_session_maker() as db:
        result = await db.execute(
            select(Item.id, Item.title, Item.metadata_)
            .where(Item.id.in_(item_ids))
        )
        rows = result.fetchall()

    items = [{"id": r[0], "title": r[1], "metadata_": dict(r[2]) if r[2] else {}} for r in rows]

    if not items:
        logger.info("Pre-filter batch: no items found")
        return

    # Run batch (handles model switching)
    batch_result = await run_prefilter_batch(
        base_url=settings.ollama_base_url,
        items=items,
        main_model=settings.ollama_model,
        prefilter_model=settings.title_prefilter_model,
    )

    # Apply results
    from sqlalchemy import update as sql_update
    checked = 0
    filtered = 0

    for res in batch_result.get("results", []):
        item_id = res["id"]
        relevant = res["relevant"]
        duration_ms = res.get("duration_ms", 0)

        orig = next((i for i in items if i["id"] == item_id), None)
        if not orig:
            continue

        try:
            async with async_session_maker() as db:
                new_metadata = dict(orig["metadata_"])
                new_metadata.pop("needs_title_check", None)
                new_metadata["title_check"] = {
                    "relevant": relevant,
                    "model": settings.title_prefilter_model,
                    "duration_ms": duration_ms,
                    "checked_at": datetime.utcnow().isoformat(),
                }

                update_values = {"metadata_": new_metadata}
                if not relevant:
                    update_values["needs_llm_processing"] = False
                    update_values["priority"] = Priority.NONE
                    update_values["priority_score"] = 10
                    filtered += 1

                await db.execute(
                    sql_update(Item).where(Item.id == item_id).values(**update_values)
                )
                await record_event(
                    db, item_id, EVENT_TITLE_PREFILTER,
                    data={"relevant": relevant, "model": settings.title_prefilter_model, "duration_ms": duration_ms},
                )
                await db.commit()
                checked += 1
        except Exception as e:
            logger.error(f"Pre-filter batch: failed to update item {item_id}: {e}")

    logger.info(f"Pre-filter batch complete: {checked} checked, {filtered} filtered out")


@router.post("/items/prefilter", dependencies=[Depends(require_admin_key)])
async def prefilter_items_batch(
    request_body: PrefilterBatchRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Run title pre-filter on a batch of items.

    Specify either item_ids or days (to process recent classifier-only items).
    Runs in background with model switching (unload 14b -> batch 8b -> unload 8b).
    """
    from config import settings

    if not settings.title_prefilter_enabled:
        raise HTTPException(status_code=400, detail="Title pre-filter is disabled")

    if request_body.item_ids:
        item_ids = request_body.item_ids
    elif request_body.days:
        # Find recent items that have classifier results but no LLM analysis
        cutoff = datetime.utcnow() - timedelta(days=request_body.days)
        query = (
            select(Item.id)
            .where(
                Item.fetched_at >= cutoff,
                Item.needs_llm_processing == True,  # noqa: E712
                json_extract_path(Item.metadata_, "pre_filter").is_not(None),
                json_extract_path(Item.metadata_, "title_check").is_(None),
            )
            .order_by(Item.fetched_at.desc())
            .limit(settings.title_prefilter_batch_limit)
        )
        result = await db.execute(query)
        item_ids = [row[0] for row in result.fetchall()]
    else:
        raise HTTPException(
            status_code=400,
            detail="Provide either item_ids or days parameter",
        )

    if not item_ids:
        return {"status": "no items to process", "count": 0}

    background_tasks.add_task(_prefilter_batch_task, item_ids)

    return {
        "status": "started",
        "count": len(item_ids),
        "message": "Pre-filter running in background. Check logs for progress.",
    }
