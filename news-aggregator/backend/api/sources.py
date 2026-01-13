"""API endpoints for sources (organizations) and their channels."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db
from models import Channel, Source
from schemas import (
    ChannelCreate,
    ChannelResponse,
    ChannelUpdate,
    SourceCreate,
    SourceResponse,
    SourceUpdate,
)

router = APIRouter()


def _build_source_response(source: Source) -> SourceResponse:
    """Build a SourceResponse with computed properties."""
    response = SourceResponse.model_validate(source)
    response.channel_count = len(source.channels)
    response.enabled_channel_count = len([c for c in source.channels if c.enabled])
    return response


# === Source (Organization) Endpoints ===


@router.get("/sources", response_model=list[SourceResponse])
async def list_sources(
    db: AsyncSession = Depends(get_db),
    enabled: bool | None = None,
    has_errors: bool | None = None,
    is_stakeholder: bool | None = None,
) -> list[SourceResponse]:
    """List all sources (organizations) with their channels."""
    query = select(Source).options(selectinload(Source.channels)).order_by(Source.name)

    if enabled is not None:
        query = query.where(Source.enabled == enabled)

    if is_stakeholder is not None:
        query = query.where(Source.is_stakeholder == is_stakeholder)

    result = await db.execute(query)
    sources = result.scalars().all()

    # Filter by error status if requested
    if has_errors is not None:
        sources = [
            s for s in sources
            if any(c.last_error for c in s.channels) == has_errors
        ]

    return [_build_source_response(source) for source in sources]


@router.get("/sources/errors", response_model=list[SourceResponse])
async def list_sources_with_errors(
    db: AsyncSession = Depends(get_db),
) -> list[SourceResponse]:
    """List all sources that have channels with errors."""
    query = (
        select(Source)
        .options(selectinload(Source.channels))
        .order_by(Source.name)
    )
    result = await db.execute(query)
    sources = result.scalars().all()

    # Filter to sources with at least one channel error
    sources_with_errors = [s for s in sources if any(c.last_error for c in s.channels)]

    return [_build_source_response(source) for source in sources_with_errors]


@router.get("/sources/{source_id}", response_model=SourceResponse)
async def get_source(
    source_id: int,
    db: AsyncSession = Depends(get_db),
) -> SourceResponse:
    """Get a single source (organization) by ID with its channels."""
    query = (
        select(Source)
        .options(selectinload(Source.channels))
        .where(Source.id == source_id)
    )
    result = await db.execute(query)
    source = result.scalar_one_or_none()

    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    return _build_source_response(source)


@router.post("/sources", response_model=SourceResponse, status_code=201)
async def create_source(
    source_data: SourceCreate,
    db: AsyncSession = Depends(get_db),
) -> SourceResponse:
    """Create a new source (organization), optionally with initial channels."""
    # Check for existing source with same name
    existing_query = select(Source).where(Source.name == source_data.name)
    existing = await db.execute(existing_query)
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail=f"Source with name '{source_data.name}' already exists",
        )

    source = Source(
        name=source_data.name,
        description=source_data.description,
        is_stakeholder=source_data.is_stakeholder,
        enabled=source_data.enabled,
    )
    db.add(source)
    await db.flush()

    # Create initial channels if provided
    for channel_data in source_data.channels:
        identifier = Channel.extract_identifier(
            channel_data.connector_type.value, channel_data.config
        )
        channel = Channel(
            source_id=source.id,
            name=channel_data.name,
            connector_type=channel_data.connector_type,
            config=channel_data.config,
            source_identifier=identifier,
            enabled=channel_data.enabled,
            fetch_interval_minutes=channel_data.fetch_interval_minutes,
        )
        db.add(channel)

    await db.commit()

    # Reload with channels to avoid lazy loading issues
    query = (
        select(Source)
        .options(selectinload(Source.channels))
        .where(Source.id == source.id)
    )
    result = await db.execute(query)
    source = result.scalar_one()

    return _build_source_response(source)


@router.patch("/sources/{source_id}", response_model=SourceResponse)
async def update_source(
    source_id: int,
    update: SourceUpdate,
    db: AsyncSession = Depends(get_db),
) -> SourceResponse:
    """Update a source (organization-level fields only)."""
    query = (
        select(Source)
        .options(selectinload(Source.channels))
        .where(Source.id == source_id)
    )
    result = await db.execute(query)
    source = result.scalar_one_or_none()

    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    update_data = update.model_dump(exclude_unset=True)

    # Check for name conflict if name is being changed
    if "name" in update_data and update_data["name"] != source.name:
        existing_query = select(Source).where(
            Source.name == update_data["name"],
            Source.id != source_id,
        )
        existing = await db.execute(existing_query)
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=409,
                detail=f"Source with name '{update_data['name']}' already exists",
            )

    for key, value in update_data.items():
        setattr(source, key, value)

    await db.commit()

    # Re-fetch with relationships to avoid lazy loading issues
    query = (
        select(Source)
        .options(selectinload(Source.channels))
        .where(Source.id == source_id)
    )
    result = await db.execute(query)
    source = result.scalar_one()

    return _build_source_response(source)


@router.delete("/sources/{source_id}", status_code=204)
async def delete_source(
    source_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a source and all its channels and items."""
    query = select(Source).where(Source.id == source_id)
    result = await db.execute(query)
    source = result.scalar_one_or_none()

    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    await db.delete(source)


@router.post("/sources/{source_id}/enable", response_model=SourceResponse)
async def enable_source(
    source_id: int,
    db: AsyncSession = Depends(get_db),
) -> SourceResponse:
    """Enable a source (master toggle)."""
    query = (
        select(Source)
        .options(selectinload(Source.channels))
        .where(Source.id == source_id)
    )
    result = await db.execute(query)
    source = result.scalar_one_or_none()

    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    source.enabled = True
    await db.commit()

    # Re-fetch with relationships to avoid lazy loading issues
    query = (
        select(Source)
        .options(selectinload(Source.channels))
        .where(Source.id == source_id)
    )
    result = await db.execute(query)
    source = result.scalar_one()

    return _build_source_response(source)


@router.post("/sources/{source_id}/disable", response_model=SourceResponse)
async def disable_source(
    source_id: int,
    db: AsyncSession = Depends(get_db),
) -> SourceResponse:
    """Disable a source (master toggle - disables all channel fetching)."""
    query = (
        select(Source)
        .options(selectinload(Source.channels))
        .where(Source.id == source_id)
    )
    result = await db.execute(query)
    source = result.scalar_one_or_none()

    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    source.enabled = False
    await db.commit()

    # Re-fetch with relationships to avoid lazy loading issues
    query = (
        select(Source)
        .options(selectinload(Source.channels))
        .where(Source.id == source_id)
    )
    result = await db.execute(query)
    source = result.scalar_one()

    return _build_source_response(source)


@router.post("/sources/{source_id}/fetch-all")
async def trigger_fetch_all_channels(
    source_id: int,
    training_mode: bool = False,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str | int | bool]:
    """Trigger fetch for all enabled channels of a source.

    Args:
        source_id: ID of source whose channels to fetch
        training_mode: If True, disables all filters for training data collection
    """
    from services.scheduler import fetch_channel

    query = (
        select(Source)
        .options(selectinload(Source.channels))
        .where(Source.id == source_id)
    )
    result = await db.execute(query)
    source = result.scalar_one_or_none()

    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    if not source.enabled:
        raise HTTPException(status_code=400, detail="Source is disabled")

    total_new = 0
    channels_fetched = 0
    errors = 0

    for channel in source.channels:
        if channel.enabled:
            try:
                new_count = await fetch_channel(channel.id, training_mode=training_mode)
                total_new += new_count
                channels_fetched += 1
            except Exception:
                errors += 1

    return {
        "status": "completed",
        "channels_fetched": channels_fetched,
        "new_items": total_new,
        "errors": errors,
        "training_mode": training_mode,
    }


# === Channel Endpoints ===


@router.post("/sources/{source_id}/channels", response_model=ChannelResponse, status_code=201)
async def add_channel(
    source_id: int,
    channel_data: ChannelCreate,
    db: AsyncSession = Depends(get_db),
) -> ChannelResponse:
    """Add a new channel to a source."""
    # Check source exists
    source_query = select(Source).where(Source.id == source_id)
    result = await db.execute(source_query)
    source = result.scalar_one_or_none()

    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    # Extract identifier for uniqueness check
    identifier = Channel.extract_identifier(
        channel_data.connector_type.value, channel_data.config
    )

    # Check for duplicate channel (same connector_type + identifier on same source)
    if identifier:
        existing_query = select(Channel).where(
            Channel.source_id == source_id,
            Channel.connector_type == channel_data.connector_type,
            Channel.source_identifier == identifier,
        )
        existing = await db.execute(existing_query)
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=409,
                detail=f"Channel with identifier '{identifier}' already exists for this source",
            )

    channel = Channel(
        source_id=source_id,
        name=channel_data.name,
        connector_type=channel_data.connector_type,
        config=channel_data.config,
        source_identifier=identifier,
        enabled=channel_data.enabled,
        fetch_interval_minutes=channel_data.fetch_interval_minutes,
    )
    db.add(channel)
    await db.flush()
    await db.refresh(channel)

    return ChannelResponse.model_validate(channel)


@router.get("/channels/{channel_id}", response_model=ChannelResponse)
async def get_channel(
    channel_id: int,
    db: AsyncSession = Depends(get_db),
) -> ChannelResponse:
    """Get a single channel by ID."""
    query = select(Channel).where(Channel.id == channel_id)
    result = await db.execute(query)
    channel = result.scalar_one_or_none()

    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")

    return ChannelResponse.model_validate(channel)


@router.patch("/channels/{channel_id}", response_model=ChannelResponse)
async def update_channel(
    channel_id: int,
    update: ChannelUpdate,
    db: AsyncSession = Depends(get_db),
) -> ChannelResponse:
    """Update a channel."""
    query = select(Channel).where(Channel.id == channel_id)
    result = await db.execute(query)
    channel = result.scalar_one_or_none()

    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")

    update_data = update.model_dump(exclude_unset=True)

    # If config changes, recalculate identifier
    if "config" in update_data:
        new_config = update_data["config"]
        connector_type_str = channel.connector_type.value if hasattr(channel.connector_type, 'value') else channel.connector_type
        new_identifier = Channel.extract_identifier(connector_type_str, new_config)

        # Check for duplicates (excluding current channel)
        if new_identifier:
            existing_query = select(Channel).where(
                Channel.source_id == channel.source_id,
                Channel.connector_type == channel.connector_type,
                Channel.source_identifier == new_identifier,
                Channel.id != channel_id,
            )
            existing = await db.execute(existing_query)
            if existing.scalar_one_or_none():
                raise HTTPException(
                    status_code=409,
                    detail=f"Channel with identifier '{new_identifier}' already exists",
                )

        update_data["source_identifier"] = new_identifier

    for key, value in update_data.items():
        setattr(channel, key, value)

    await db.flush()
    await db.refresh(channel)

    return ChannelResponse.model_validate(channel)


@router.delete("/channels/{channel_id}", status_code=204)
async def delete_channel(
    channel_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a channel and all its items."""
    query = select(Channel).where(Channel.id == channel_id)
    result = await db.execute(query)
    channel = result.scalar_one_or_none()

    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")

    await db.delete(channel)


@router.post("/channels/{channel_id}/fetch")
async def trigger_channel_fetch(
    channel_id: int,
    training_mode: bool = False,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str | int | bool]:
    """Manually trigger a fetch for a channel.

    Args:
        channel_id: ID of channel to fetch
        training_mode: If True, disables all filters for training data collection
    """
    from services.scheduler import fetch_channel

    query = (
        select(Channel)
        .options(selectinload(Channel.source))
        .where(Channel.id == channel_id)
    )
    result = await db.execute(query)
    channel = result.scalar_one_or_none()

    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")

    if not channel.source.enabled:
        raise HTTPException(status_code=400, detail="Parent source is disabled")

    try:
        new_count = await fetch_channel(channel_id, training_mode=training_mode)
        return {
            "status": "completed",
            "new_items": new_count,
            "training_mode": training_mode,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/channels/{channel_id}/enable", response_model=ChannelResponse)
async def enable_channel(
    channel_id: int,
    db: AsyncSession = Depends(get_db),
) -> ChannelResponse:
    """Enable a channel."""
    query = select(Channel).where(Channel.id == channel_id)
    result = await db.execute(query)
    channel = result.scalar_one_or_none()

    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")

    channel.enabled = True
    await db.flush()
    await db.refresh(channel)

    return ChannelResponse.model_validate(channel)


@router.post("/channels/{channel_id}/disable", response_model=ChannelResponse)
async def disable_channel(
    channel_id: int,
    db: AsyncSession = Depends(get_db),
) -> ChannelResponse:
    """Disable a channel."""
    query = select(Channel).where(Channel.id == channel_id)
    result = await db.execute(query)
    channel = result.scalar_one_or_none()

    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")

    channel.enabled = False
    await db.flush()
    await db.refresh(channel)

    return ChannelResponse.model_validate(channel)


# === Legacy Endpoints (for backward compatibility during transition) ===


@router.post("/sources/fetch-all")
async def trigger_fetch_all_sources(training_mode: bool = False) -> dict:
    """Manually trigger a fetch for all enabled channels across all sources.

    Args:
        training_mode: If True, disables all filters for training data collection
    """
    from services.scheduler import fetch_all_channels

    try:
        result = await fetch_all_channels(training_mode=training_mode)
        return {"status": "completed", **result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
