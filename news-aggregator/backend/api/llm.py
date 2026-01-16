"""API endpoints for LLM configuration."""

import asyncio
import logging
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from models import Item, Priority, Setting
from services.llm.ollama import OllamaProvider

logger = logging.getLogger(__name__)
router = APIRouter()

# Constants
LLM_ENABLED_KEY = "llm_enabled"
LLM_ENABLED_AT_KEY = "llm_enabled_at"


class PromptRequest(BaseModel):
    """Request to send a prompt to the LLM."""

    prompt: str = Field(..., min_length=1, description="The prompt to send")
    system: str | None = Field(None, description="Optional system prompt")
    temperature: float = Field(0.7, ge=0.0, le=2.0, description="Sampling temperature")
    max_tokens: int | None = Field(None, ge=1, le=4096, description="Max tokens to generate")


class PromptResponse(BaseModel):
    """Response from LLM prompt."""

    text: str
    model: str
    tokens_used: int | None = None
    provider: str


class OllamaModel(BaseModel):
    """Information about an Ollama model."""

    name: str
    is_current: bool = False


class OllamaModelsResponse(BaseModel):
    """Response for available Ollama models."""

    available: bool
    models: list[OllamaModel]
    current_model: str
    base_url: str


class LLMSettingsResponse(BaseModel):
    """Current LLM settings."""

    ollama_available: bool
    ollama_base_url: str
    ollama_model: str
    openrouter_configured: bool
    openrouter_model: str


@router.get("/llm/models", response_model=OllamaModelsResponse)
async def list_ollama_models() -> OllamaModelsResponse:
    """List available Ollama models.

    Returns list of models installed in Ollama along with current selection.
    """
    provider = OllamaProvider(
        base_url=settings.ollama_base_url,
        model=settings.ollama_model,
    )

    try:
        available = await provider.is_available()
        if not available:
            return OllamaModelsResponse(
                available=False,
                models=[],
                current_model=settings.ollama_model,
                base_url=settings.ollama_base_url,
            )

        model_names = await provider.list_models()
        models = [
            OllamaModel(
                name=name,
                is_current=(name == settings.ollama_model),
            )
            for name in sorted(model_names)
        ]

        return OllamaModelsResponse(
            available=True,
            models=models,
            current_model=settings.ollama_model,
            base_url=settings.ollama_base_url,
        )

    except Exception as e:
        logger.error(f"Error listing Ollama models: {e}")
        raise HTTPException(
            status_code=503,
            detail=f"Could not connect to Ollama: {e}",
        )


@router.get("/llm/settings", response_model=LLMSettingsResponse)
async def get_llm_settings() -> LLMSettingsResponse:
    """Get current LLM configuration settings."""
    provider = OllamaProvider(
        base_url=settings.ollama_base_url,
        model=settings.ollama_model,
    )

    ollama_available = await provider.is_available()

    return LLMSettingsResponse(
        ollama_available=ollama_available,
        ollama_base_url=settings.ollama_base_url,
        ollama_model=settings.ollama_model,
        openrouter_configured=bool(settings.openrouter_api_key),
        openrouter_model=settings.openrouter_model,
    )


@router.post("/llm/prompt", response_model=PromptResponse)
async def send_prompt(request: PromptRequest) -> PromptResponse:
    """Send a prompt to the LLM and get a response.

    Uses the configured LLM provider (Ollama primary, OpenRouter fallback).
    """
    from services.processor import create_processor_from_settings

    try:
        processor = await create_processor_from_settings()
        response = await processor.llm.complete(
            prompt=request.prompt,
            system=request.system,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
        )

        return PromptResponse(
            text=response.text,
            model=response.model,
            tokens_used=response.tokens_used,
            provider=response.metadata.get("provider", "unknown"),
        )

    except Exception as e:
        logger.error(f"LLM prompt failed: {e}")
        raise HTTPException(
            status_code=503,
            detail=f"LLM request failed: {e}",
        )


class ModelSelectRequest(BaseModel):
    """Request to select an Ollama model."""

    model: str = Field(..., min_length=1, description="Model name to select")


class ModelSelectResponse(BaseModel):
    """Response after selecting a model."""

    success: bool
    selected_model: str
    message: str


@router.put("/llm/model", response_model=ModelSelectResponse)
async def select_ollama_model(
    request: ModelSelectRequest,
    db: AsyncSession = Depends(get_db),
) -> ModelSelectResponse:
    """Change the active Ollama model.

    The selection is persisted in the database and used for future requests.
    """
    # Verify the model exists in Ollama
    provider = OllamaProvider(
        base_url=settings.ollama_base_url,
        model=settings.ollama_model,
    )

    try:
        available = await provider.is_available()
        if not available:
            raise HTTPException(
                status_code=503,
                detail="Ollama is not available",
            )

        model_names = await provider.list_models()
        if request.model not in model_names:
            raise HTTPException(
                status_code=400,
                detail=f"Model '{request.model}' not found. Available: {', '.join(sorted(model_names))}",
            )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"Could not verify model: {e}",
        )

    # Persist the selection in database
    setting = await db.scalar(
        select(Setting).where(Setting.key == "ollama_model")
    )

    if setting:
        setting.value = request.model
    else:
        setting = Setting(
            key="ollama_model",
            value=request.model,
            description="Selected Ollama model for LLM operations",
        )
        db.add(setting)

    logger.info(f"Changed Ollama model to: {request.model}")

    return ModelSelectResponse(
        success=True,
        selected_model=request.model,
        message=f"Model changed to '{request.model}'",
    )


@router.get("/llm/model")
async def get_selected_model(
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Get the currently selected Ollama model."""
    # Check database for persisted selection
    setting = await db.scalar(
        select(Setting).where(Setting.key == "ollama_model")
    )

    if setting:
        return {
            "model": setting.value,
            "source": "database",
        }

    # Fall back to config
    return {
        "model": settings.ollama_model,
        "source": "config",
    }


# ============================================================================
# LLM Enable/Disable API
# ============================================================================


class LLMStatusResponse(BaseModel):
    """Current LLM processing status."""

    enabled: bool
    env_enabled: bool  # From environment variable
    runtime_enabled: bool | None  # From database (None = not set, use env)
    provider: str
    model: str
    ollama_available: bool
    unprocessed_count: int
    last_enabled_at: str | None


class LLMToggleResponse(BaseModel):
    """Response after toggling LLM."""

    success: bool
    enabled: bool
    message: str
    reprocess_triggered: bool = False
    unprocessed_count: int = 0


async def get_llm_enabled(db: AsyncSession) -> bool:
    """Check if LLM is enabled (runtime setting overrides env)."""
    # Check database for runtime override
    setting = await db.scalar(
        select(Setting).where(Setting.key == LLM_ENABLED_KEY)
    )

    if setting is not None:
        return setting.value.lower() == "true"

    # Fall back to environment variable
    return settings.llm_enabled


async def get_unprocessed_count(db: AsyncSession) -> int:
    """Count items queued for LLM processing."""
    count = await db.scalar(
        select(func.count(Item.id)).where(Item.needs_llm_processing == True)
    )
    return count or 0


async def reprocess_unprocessed_items(limit: int = 100) -> int:
    """Background task to reprocess items without LLM analysis."""
    from database import async_session_maker
    from services.processor import create_processor_from_settings
    from services.pipeline import Pipeline

    logger.info(f"Starting background reprocessing of up to {limit} unprocessed items")

    async with async_session_maker() as db:
        # Get processor
        processor = await create_processor_from_settings()
        if not processor:
            logger.warning("LLM processor not available, skipping reprocess")
            return 0

        # Get unprocessed items
        result = await db.execute(
            select(Item)
            .where((Item.summary.is_(None)) | (Item.summary == ""))
            .order_by(Item.published_at.desc())
            .limit(limit)
        )
        items = result.scalars().all()

        if not items:
            logger.info("No unprocessed items found")
            return 0

        logger.info(f"Found {len(items)} unprocessed items to process")

        processed = 0
        for item in items:
            try:
                # Get source name for context
                from models import Channel, Source
                channel = await db.get(Channel, item.channel_id)
                source_name = "Unknown"
                if channel:
                    source = await db.get(Source, channel.source_id)
                    if source:
                        source_name = source.name

                # Run LLM analysis
                analysis = await processor.analyze(item, source_name=source_name)

                # Update item
                item.summary = analysis.get("summary")
                item.detailed_analysis = analysis.get("detailed_analysis")

                # Map priority string to enum (critical→high, high→medium, medium→low, low→none)
                llm_priority = analysis.get("priority") or analysis.get("priority_suggestion")
                if llm_priority == "critical":
                    item.priority = Priority.HIGH
                elif llm_priority == "high":
                    item.priority = Priority.MEDIUM
                elif llm_priority == "medium":
                    item.priority = Priority.LOW
                else:
                    item.priority = Priority.NONE

                item.priority_score = int(analysis.get("relevance_score", 0) * 100)

                # Store in metadata (use metadata_ to avoid SQLAlchemy naming conflict)
                if item.metadata_ is None:
                    item.metadata_ = {}
                item.metadata_["llm_analysis"] = analysis

                await db.commit()
                processed += 1
                logger.debug(f"Processed item {item.id}: {item.title[:50]}...")

            except Exception as e:
                logger.error(f"Error processing item {item.id}: {e}")
                await db.rollback()

        logger.info(f"Background reprocessing completed: {processed}/{len(items)} items")
        return processed


@router.get("/llm/status", response_model=LLMStatusResponse)
async def get_llm_status(db: AsyncSession = Depends(get_db)) -> LLMStatusResponse:
    """Get current LLM processing status."""
    # Check runtime setting
    runtime_setting = await db.scalar(
        select(Setting).where(Setting.key == LLM_ENABLED_KEY)
    )
    runtime_enabled = None
    if runtime_setting is not None:
        runtime_enabled = runtime_setting.value.lower() == "true"

    # Get last enabled timestamp
    enabled_at_setting = await db.scalar(
        select(Setting).where(Setting.key == LLM_ENABLED_AT_KEY)
    )
    last_enabled_at = enabled_at_setting.value if enabled_at_setting else None

    # Check if effectively enabled
    enabled = await get_llm_enabled(db)

    # Check Ollama availability
    provider = OllamaProvider(
        base_url=settings.ollama_base_url,
        model=settings.ollama_model,
    )
    ollama_available = await provider.is_available()

    # Count unprocessed items
    unprocessed_count = await get_unprocessed_count(db)

    return LLMStatusResponse(
        enabled=enabled,
        env_enabled=settings.llm_enabled,
        runtime_enabled=runtime_enabled,
        provider="ollama",
        model=settings.ollama_model,
        ollama_available=ollama_available,
        unprocessed_count=unprocessed_count,
        last_enabled_at=last_enabled_at,
    )


@router.post("/llm/enable", response_model=LLMToggleResponse)
async def enable_llm(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    auto_reprocess: bool = True,
    reprocess_limit: int = 100,
) -> LLMToggleResponse:
    """Enable LLM processing at runtime.

    Args:
        auto_reprocess: If True, automatically process items without LLM analysis
        reprocess_limit: Maximum number of items to reprocess in background
    """
    # Update or create setting
    setting = await db.scalar(
        select(Setting).where(Setting.key == LLM_ENABLED_KEY)
    )

    if setting:
        setting.value = "true"
    else:
        setting = Setting(
            key=LLM_ENABLED_KEY,
            value="true",
            description="Runtime toggle for LLM processing",
        )
        db.add(setting)

    # Update enabled timestamp
    timestamp_setting = await db.scalar(
        select(Setting).where(Setting.key == LLM_ENABLED_AT_KEY)
    )
    now = datetime.utcnow().isoformat()

    if timestamp_setting:
        timestamp_setting.value = now
    else:
        timestamp_setting = Setting(
            key=LLM_ENABLED_AT_KEY,
            value=now,
            description="Timestamp when LLM was last enabled",
        )
        db.add(timestamp_setting)

    await db.commit()
    logger.info("LLM processing enabled via API")

    # Count unprocessed items
    unprocessed_count = await get_unprocessed_count(db)

    # Trigger background reprocessing if requested
    reprocess_triggered = False
    if auto_reprocess and unprocessed_count > 0:
        background_tasks.add_task(reprocess_unprocessed_items, reprocess_limit)
        reprocess_triggered = True
        logger.info(f"Triggered background reprocessing of up to {reprocess_limit} items")

    return LLMToggleResponse(
        success=True,
        enabled=True,
        message="LLM processing enabled",
        reprocess_triggered=reprocess_triggered,
        unprocessed_count=unprocessed_count,
    )


@router.post("/llm/disable", response_model=LLMToggleResponse)
async def disable_llm(db: AsyncSession = Depends(get_db)) -> LLMToggleResponse:
    """Disable LLM processing at runtime."""
    # Update or create setting
    setting = await db.scalar(
        select(Setting).where(Setting.key == LLM_ENABLED_KEY)
    )

    if setting:
        setting.value = "false"
    else:
        setting = Setting(
            key=LLM_ENABLED_KEY,
            value="false",
            description="Runtime toggle for LLM processing",
        )
        db.add(setting)

    await db.commit()
    logger.info("LLM processing disabled via API")

    # Count unprocessed items
    unprocessed_count = await get_unprocessed_count(db)

    return LLMToggleResponse(
        success=True,
        enabled=False,
        message="LLM processing disabled",
        unprocessed_count=unprocessed_count,
    )


# ============================================================================
# LLM Worker Status API
# ============================================================================


class WorkerStatusResponse(BaseModel):
    """LLM Worker status and statistics."""

    running: bool
    paused: bool
    fresh_queue_size: int
    stats: dict


@router.get("/llm/worker/status", response_model=WorkerStatusResponse)
async def get_worker_status() -> WorkerStatusResponse:
    """Get LLM worker status and statistics.

    The worker processes items continuously with priority:
    1. Fresh items (from fetch) - immediate processing
    2. Backlog items (needs_llm_processing=True) - when idle
    """
    from services.llm_worker import get_worker

    worker = get_worker()
    if worker is None:
        return WorkerStatusResponse(
            running=False,
            paused=False,
            fresh_queue_size=0,
            stats={
                "fresh_processed": 0,
                "backlog_processed": 0,
                "errors": 0,
                "started_at": None,
                "last_processed_at": None,
            },
        )

    status = worker.get_status()
    return WorkerStatusResponse(
        running=status["running"],
        paused=status["paused"],
        fresh_queue_size=status["fresh_queue_size"],
        stats=status["stats"],
    )


@router.post("/llm/worker/pause")
async def pause_worker() -> dict:
    """Pause LLM worker processing.

    Items will still be queued but not processed until resumed.
    """
    from services.llm_worker import get_worker

    worker = get_worker()
    if worker is None:
        raise HTTPException(status_code=503, detail="LLM worker not running")

    worker.pause()
    return {"status": "paused", "message": "LLM worker paused"}


@router.post("/llm/worker/resume")
async def resume_worker() -> dict:
    """Resume LLM worker processing."""
    from services.llm_worker import get_worker

    worker = get_worker()
    if worker is None:
        raise HTTPException(status_code=503, detail="LLM worker not running")

    worker.resume()
    return {"status": "resumed", "message": "LLM worker resumed"}


# ============================================================================
# Classifier Worker Status API
# ============================================================================


class ClassifierWorkerStatusResponse(BaseModel):
    """Classifier Worker status and statistics."""

    running: bool
    paused: bool
    stats: dict


@router.get("/classifier/worker/status", response_model=ClassifierWorkerStatusResponse)
async def get_classifier_worker_status() -> ClassifierWorkerStatusResponse:
    """Get classifier worker status and statistics.

    The classifier worker processes items that have never been classified
    (no pre_filter metadata) and updates their priority based on classifier confidence.
    """
    from services.classifier_worker import get_classifier_worker

    worker = get_classifier_worker()
    if worker is None:
        return ClassifierWorkerStatusResponse(
            running=False,
            paused=False,
            stats={
                "processed": 0,
                "priority_changed": 0,
                "errors": 0,
                "started_at": None,
                "last_processed_at": None,
            },
        )

    status = worker.get_status()
    return ClassifierWorkerStatusResponse(
        running=status["running"],
        paused=status["paused"],
        stats=status["stats"],
    )


@router.post("/classifier/worker/pause")
async def pause_classifier_worker() -> dict:
    """Pause classifier worker processing.

    Items will still be queued but not processed until resumed.
    """
    from services.classifier_worker import get_classifier_worker

    worker = get_classifier_worker()
    if worker is None:
        raise HTTPException(status_code=503, detail="Classifier worker not running")

    worker.pause()
    return {"status": "paused", "message": "Classifier worker paused"}


@router.post("/classifier/worker/resume")
async def resume_classifier_worker() -> dict:
    """Resume classifier worker processing."""
    from services.classifier_worker import get_classifier_worker

    worker = get_classifier_worker()
    if worker is None:
        raise HTTPException(status_code=503, detail="Classifier worker not running")

    worker.resume()
    return {"status": "resumed", "message": "Classifier worker resumed"}


@router.get("/classifier/unclassified/count")
async def get_classifier_unclassified_count() -> dict:
    """Get count of items that have not been classified yet.

    These items have no pre_filter metadata and will be processed
    by the classifier worker.
    """
    from services.classifier_worker import get_unclassified_count

    count = await get_unclassified_count()
    return {"unclassified_count": count}
