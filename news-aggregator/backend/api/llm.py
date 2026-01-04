"""API endpoints for LLM configuration."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from models import Setting
from services.llm.ollama import OllamaProvider

logger = logging.getLogger(__name__)
router = APIRouter()


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
