"""API endpoints for LLM model configuration."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, func

from database import async_session_maker
from models import LLMModelConfig, LLMPrompt
from .auth import require_admin_key

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/llm/model-configs")


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class LLMModelConfigResponse(BaseModel):
    id: int
    model_name: str
    display_name: str | None
    priority: int
    enabled: bool
    ollama_base_url: str | None
    timeout: int
    has_prompt: bool = False
    prompt_version: int | None = None
    created_at: str

    class Config:
        from_attributes = True


class LLMModelConfigCreate(BaseModel):
    model_name: str = Field(..., max_length=100)
    display_name: Optional[str] = Field(None, max_length=200)
    priority: int = Field(default=1, ge=1)
    enabled: bool = True
    ollama_base_url: Optional[str] = Field(None, max_length=500)
    timeout: int = Field(default=120, ge=10, le=600)


class LLMModelConfigUpdate(BaseModel):
    display_name: Optional[str] = Field(None, max_length=200)
    priority: Optional[int] = Field(None, ge=1)
    enabled: Optional[bool] = None
    ollama_base_url: Optional[str] = Field(None, max_length=500)
    timeout: Optional[int] = Field(None, ge=10, le=600)


class ReorderItem(BaseModel):
    id: int
    priority: int = Field(ge=1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _enrich_with_prompt_info(
    configs: list[LLMModelConfig],
) -> list[LLMModelConfigResponse]:
    """Add has_prompt and prompt_version from LLMPrompt table."""
    async with async_session_maker() as db:
        # Fetch active prompts for all model names in one query
        model_names = [c.model_name for c in configs]
        result = await db.execute(
            select(LLMPrompt.model, LLMPrompt.version)
            .where(LLMPrompt.model.in_(model_names), LLMPrompt.active.is_(True))
        )
        active_prompts = {row.model: row.version for row in result}

    responses = []
    for c in configs:
        responses.append(LLMModelConfigResponse(
            id=c.id,
            model_name=c.model_name,
            display_name=c.display_name,
            priority=c.priority,
            enabled=c.enabled,
            ollama_base_url=c.ollama_base_url,
            timeout=c.timeout,
            has_prompt=c.model_name in active_prompts,
            prompt_version=active_prompts.get(c.model_name),
            created_at=c.created_at.isoformat() if c.created_at else "",
        ))
    return responses


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("", response_model=list[LLMModelConfigResponse])
async def list_model_configs():
    """List all LLM model configurations ordered by priority."""
    async with async_session_maker() as db:
        result = await db.execute(
            select(LLMModelConfig).order_by(LLMModelConfig.priority, LLMModelConfig.id)
        )
        configs = result.scalars().all()
    return await _enrich_with_prompt_info(configs)


@router.post("", response_model=LLMModelConfigResponse, dependencies=[Depends(require_admin_key)])
async def create_model_config(data: LLMModelConfigCreate):
    """Create a new LLM model configuration."""
    async with async_session_maker() as db:
        # Check uniqueness
        existing = await db.execute(
            select(LLMModelConfig).where(LLMModelConfig.model_name == data.model_name)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail=f"Model '{data.model_name}' already configured")

        config = LLMModelConfig(
            model_name=data.model_name,
            display_name=data.display_name,
            priority=data.priority,
            enabled=data.enabled,
            ollama_base_url=data.ollama_base_url,
            timeout=data.timeout,
        )
        db.add(config)
        await db.commit()
        await db.refresh(config)

    enriched = await _enrich_with_prompt_info([config])
    return enriched[0]


@router.patch("/{config_id}", response_model=LLMModelConfigResponse, dependencies=[Depends(require_admin_key)])
async def update_model_config(config_id: int, data: LLMModelConfigUpdate):
    """Update an LLM model configuration."""
    async with async_session_maker() as db:
        config = await db.get(LLMModelConfig, config_id)
        if not config:
            raise HTTPException(status_code=404, detail="Model config not found")

        update_data = data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(config, key, value)

        await db.commit()
        await db.refresh(config)

    enriched = await _enrich_with_prompt_info([config])
    return enriched[0]


@router.delete("/{config_id}", dependencies=[Depends(require_admin_key)])
async def delete_model_config(config_id: int):
    """Delete an LLM model configuration."""
    async with async_session_maker() as db:
        config = await db.get(LLMModelConfig, config_id)
        if not config:
            raise HTTPException(status_code=404, detail="Model config not found")

        await db.delete(config)
        await db.commit()

    return {"status": "deleted", "model_name": config.model_name}


@router.post("/reorder", dependencies=[Depends(require_admin_key)])
async def reorder_model_configs(items: list[ReorderItem]):
    """Bulk update priorities for model configurations."""
    async with async_session_maker() as db:
        for item in items:
            config = await db.get(LLMModelConfig, item.id)
            if config:
                config.priority = item.priority
        await db.commit()

    return {"status": "reordered", "count": len(items)}
