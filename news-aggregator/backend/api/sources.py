"""API endpoints for news sources."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import Source
from schemas import SourceCreate, SourceResponse, SourceUpdate, ValidationResult

router = APIRouter()


@router.get("/sources", response_model=list[SourceResponse])
async def list_sources(
    db: AsyncSession = Depends(get_db),
    enabled: bool | None = None,
) -> list[SourceResponse]:
    """List all configured sources."""
    query = select(Source).order_by(Source.name)

    if enabled is not None:
        query = query.where(Source.enabled == enabled)

    result = await db.execute(query)
    sources = result.scalars().all()

    return [SourceResponse.model_validate(source) for source in sources]


@router.get("/sources/errors", response_model=list[SourceResponse])
async def list_sources_with_errors(
    db: AsyncSession = Depends(get_db),
) -> list[SourceResponse]:
    """List all sources that have errors."""
    query = (
        select(Source)
        .where(Source.last_error.isnot(None))
        .order_by(Source.updated_at.desc())
    )
    result = await db.execute(query)
    sources = result.scalars().all()

    return [SourceResponse.model_validate(source) for source in sources]


@router.get("/sources/{source_id}", response_model=SourceResponse)
async def get_source(
    source_id: int,
    db: AsyncSession = Depends(get_db),
) -> SourceResponse:
    """Get a single source by ID."""
    query = select(Source).where(Source.id == source_id)
    result = await db.execute(query)
    source = result.scalar_one_or_none()

    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    return SourceResponse.model_validate(source)


@router.post("/sources", response_model=SourceResponse, status_code=201)
async def create_source(
    source_data: SourceCreate,
    db: AsyncSession = Depends(get_db),
) -> SourceResponse:
    """Create a new source."""
    # Extract identifier for uniqueness check
    identifier = Source.extract_identifier(
        source_data.connector_type, source_data.config
    )

    # Check for existing source with same connector_type and identifier
    if identifier:
        existing_query = select(Source).where(
            Source.connector_type == source_data.connector_type,
            Source.source_identifier == identifier,
        )
        existing = await db.execute(existing_query)
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=409,
                detail=f"Source with identifier '{identifier}' already exists for connector type '{source_data.connector_type}'",
            )

    source = Source(
        name=source_data.name,
        connector_type=source_data.connector_type,
        config=source_data.config,
        source_identifier=identifier,
        enabled=source_data.enabled,
        fetch_interval_minutes=source_data.fetch_interval_minutes,
    )
    db.add(source)
    await db.flush()
    await db.refresh(source)

    return SourceResponse.model_validate(source)


@router.patch("/sources/{source_id}", response_model=SourceResponse)
async def update_source(
    source_id: int,
    update: SourceUpdate,
    db: AsyncSession = Depends(get_db),
) -> SourceResponse:
    """Update a source."""
    query = select(Source).where(Source.id == source_id)
    result = await db.execute(query)
    source = result.scalar_one_or_none()

    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    update_data = update.model_dump(exclude_unset=True)

    # If config or connector_type changes, recalculate identifier
    new_config = update_data.get("config", source.config)
    new_connector_type = update_data.get("connector_type", source.connector_type)

    if "config" in update_data or "connector_type" in update_data:
        new_identifier = Source.extract_identifier(new_connector_type, new_config)

        # Check for duplicates (excluding current source)
        if new_identifier:
            existing_query = select(Source).where(
                Source.connector_type == new_connector_type,
                Source.source_identifier == new_identifier,
                Source.id != source_id,
            )
            existing = await db.execute(existing_query)
            if existing.scalar_one_or_none():
                raise HTTPException(
                    status_code=409,
                    detail=f"Source with identifier '{new_identifier}' already exists",
                )

        update_data["source_identifier"] = new_identifier

    for key, value in update_data.items():
        setattr(source, key, value)

    await db.flush()
    await db.refresh(source)

    return SourceResponse.model_validate(source)


@router.delete("/sources/{source_id}", status_code=204)
async def delete_source(
    source_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a source and all its items."""
    query = select(Source).where(Source.id == source_id)
    result = await db.execute(query)
    source = result.scalar_one_or_none()

    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    await db.delete(source)


@router.post("/sources/{source_id}/validate", response_model=ValidationResult)
async def validate_source(
    source_id: int,
    db: AsyncSession = Depends(get_db),
) -> ValidationResult:
    """Validate a source configuration."""
    query = select(Source).where(Source.id == source_id)
    result = await db.execute(query)
    source = result.scalar_one_or_none()

    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    # TODO: Get connector and run validation
    # connector = ConnectorRegistry.get(source.connector_type)
    # valid, message = await connector.validate(source.config)

    # Placeholder until connector system is implemented
    return ValidationResult(valid=True, message="Validation not yet implemented")


@router.post("/sources/{source_id}/fetch")
async def trigger_fetch(
    source_id: int,
    training_mode: bool = False,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str | int | bool]:
    """Manually trigger a fetch for a source.

    Args:
        source_id: ID of source to fetch
        training_mode: If True, disables all filters (age, keyword, LLM relevance)
                      for training data collection. Only deduplication remains.
    """
    from services.scheduler import fetch_source

    query = select(Source).where(Source.id == source_id)
    result = await db.execute(query)
    source = result.scalar_one_or_none()

    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    try:
        new_count = await fetch_source(source_id, training_mode=training_mode)
        return {
            "status": "completed",
            "new_items": new_count,
            "training_mode": training_mode,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sources/fetch-all")
async def trigger_fetch_all(training_mode: bool = False) -> dict:
    """Manually trigger a fetch for all enabled sources.

    Args:
        training_mode: If True, disables all filters (age, keyword, LLM relevance)
                      for training data collection. Only deduplication remains.
                      Also skips LLM analysis for speed.
    """
    from services.scheduler import fetch_all_sources

    try:
        result = await fetch_all_sources(training_mode=training_mode)
        return {"status": "completed", **result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sources/{source_id}/enable", response_model=SourceResponse)
async def enable_source(
    source_id: int,
    db: AsyncSession = Depends(get_db),
) -> SourceResponse:
    """Enable a source."""
    query = select(Source).where(Source.id == source_id)
    result = await db.execute(query)
    source = result.scalar_one_or_none()

    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    source.enabled = True
    await db.flush()
    await db.refresh(source)

    return SourceResponse.model_validate(source)


@router.post("/sources/{source_id}/disable", response_model=SourceResponse)
async def disable_source(
    source_id: int,
    db: AsyncSession = Depends(get_db),
) -> SourceResponse:
    """Disable a source."""
    query = select(Source).where(Source.id == source_id)
    result = await db.execute(query)
    source = result.scalar_one_or_none()

    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    source.enabled = False
    await db.flush()
    await db.refresh(source)

    return SourceResponse.model_validate(source)
