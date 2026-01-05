"""API endpoints for news items."""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db
from models import Item, Priority
from schemas import ItemListResponse, ItemResponse, ItemUpdate

router = APIRouter()


@router.get("/items", response_model=ItemListResponse)
async def list_items(
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    source_id: int | None = None,
    priority: Priority | None = None,
    is_read: bool | None = None,
    is_starred: bool | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    search: str | None = None,
    relevant_only: bool = Query(True, description="Exclude LOW priority items (not Liga-relevant)"),
) -> ItemListResponse:
    """List items with filtering and pagination.

    By default, only shows relevant items (critical, high, medium priority).
    Set relevant_only=false to include all items including LOW priority.
    """
    query = select(Item).options(selectinload(Item.source))

    # Apply filters
    if source_id is not None:
        query = query.where(Item.source_id == source_id)
    if priority is not None:
        query = query.where(Item.priority == priority)
    elif relevant_only:
        # Exclude LOW priority items (not Liga-relevant)
        query = query.where(Item.priority != Priority.LOW)
    if is_read is not None:
        query = query.where(Item.is_read == is_read)
    if is_starred is not None:
        query = query.where(Item.is_starred == is_starred)
    if since is not None:
        query = query.where(Item.published_at >= since)
    if until is not None:
        query = query.where(Item.published_at <= until)
    if search:
        search_pattern = f"%{search}%"
        query = query.where(
            (Item.title.ilike(search_pattern)) | (Item.content.ilike(search_pattern))
        )

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total = await db.scalar(count_query) or 0

    # Apply pagination and ordering (secondary sort by id for stable pagination)
    query = query.order_by(Item.published_at.desc(), Item.id.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    items = result.scalars().all()

    total_pages = (total + page_size - 1) // page_size

    return ItemListResponse(
        items=[ItemResponse.model_validate(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get("/items/{item_id}", response_model=ItemResponse)
async def get_item(
    item_id: int,
    db: AsyncSession = Depends(get_db),
) -> ItemResponse:
    """Get a single item by ID."""
    query = select(Item).where(Item.id == item_id).options(selectinload(Item.source))
    result = await db.execute(query)
    item = result.scalar_one_or_none()

    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")

    return ItemResponse.model_validate(item)


@router.patch("/items/{item_id}", response_model=ItemResponse)
async def update_item(
    item_id: int,
    update: ItemUpdate,
    db: AsyncSession = Depends(get_db),
) -> ItemResponse:
    """Update an item (read status, starred, notes)."""
    query = select(Item).where(Item.id == item_id).options(selectinload(Item.source))
    result = await db.execute(query)
    item = result.scalar_one_or_none()

    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")

    update_data = update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(item, key, value)

    await db.flush()
    await db.refresh(item)

    return ItemResponse.model_validate(item)


@router.post("/items/{item_id}/read")
async def mark_as_read(
    item_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Mark an item as read."""
    query = select(Item).where(Item.id == item_id)
    result = await db.execute(query)
    item = result.scalar_one_or_none()

    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")

    item.is_read = True
    return {"status": "ok"}


@router.post("/items/mark-all-read")
async def mark_all_as_read(
    source_id: int | None = None,
    before: datetime | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    """Mark multiple items as read."""
    query = select(Item).where(Item.is_read == False)  # noqa: E712

    if source_id is not None:
        query = query.where(Item.source_id == source_id)
    if before is not None:
        query = query.where(Item.published_at <= before)

    result = await db.execute(query)
    items = result.scalars().all()

    for item in items:
        item.is_read = True

    return {"marked": len(items)}
