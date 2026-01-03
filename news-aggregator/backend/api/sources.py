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
    source = Source(
        name=source_data.name,
        connector_type=source_data.connector_type,
        config=source_data.config,
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
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Manually trigger a fetch for a source."""
    query = select(Source).where(Source.id == source_id)
    result = await db.execute(query)
    source = result.scalar_one_or_none()

    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    # TODO: Trigger async fetch job
    # await scheduler.trigger_source_fetch(source_id)

    return {"status": "fetch_queued"}
