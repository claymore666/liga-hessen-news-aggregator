"""LLM Prompt management API endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import require_admin_key
from database import get_db
from models import LLMPrompt

router = APIRouter(prefix="/prompts", tags=["prompts"])


class PromptResponse(BaseModel):
    """Response with prompt details."""

    id: int
    model: str
    version: int
    active: bool
    notes: str | None = None
    created_at: str | None = None
    system_prompt: str | None = None  # Only included when explicitly requested


class PromptCreate(BaseModel):
    """Request to create a new prompt version."""

    model: str
    system_prompt: str
    notes: str | None = None
    activate: bool = True


class PromptListResponse(BaseModel):
    """Response listing prompts for a model."""

    model: str
    active_version: int | None = None
    versions: list[PromptResponse]


def _prompt_response(p: LLMPrompt, include_prompt: bool = False) -> PromptResponse:
    return PromptResponse(
        id=p.id,
        model=p.model,
        version=p.version,
        active=p.active,
        notes=p.notes,
        created_at=p.created_at.isoformat() if p.created_at else None,
        system_prompt=p.system_prompt if include_prompt else None,
    )


@router.get("/models")
async def list_models(db: AsyncSession = Depends(get_db)) -> list[dict]:
    """List all models that have prompts, with their active version."""
    result = await db.execute(
        select(
            LLMPrompt.model,
            func.max(LLMPrompt.version).label("latest_version"),
            func.count(LLMPrompt.id).label("total_versions"),
        ).group_by(LLMPrompt.model)
    )
    models = []
    for row in result.all():
        # Find active version
        active = await db.scalar(
            select(LLMPrompt.version)
            .where(LLMPrompt.model == row.model, LLMPrompt.active == True)  # noqa: E712
        )
        models.append({
            "model": row.model,
            "latest_version": row.latest_version,
            "active_version": active,
            "total_versions": row.total_versions,
        })
    return models


@router.get("/{model}")
async def get_active_prompt(
    model: str,
    db: AsyncSession = Depends(get_db),
) -> PromptResponse:
    """Get the active prompt for a model (includes full prompt text)."""
    prompt = await db.scalar(
        select(LLMPrompt)
        .where(LLMPrompt.model == model, LLMPrompt.active == True)  # noqa: E712
    )
    if not prompt:
        raise HTTPException(status_code=404, detail=f"No active prompt for model {model}")
    return _prompt_response(prompt, include_prompt=True)


@router.get("/{model}/history")
async def get_prompt_history(
    model: str,
    db: AsyncSession = Depends(get_db),
) -> PromptListResponse:
    """Get all prompt versions for a model."""
    result = await db.execute(
        select(LLMPrompt)
        .where(LLMPrompt.model == model)
        .order_by(LLMPrompt.version.desc())
    )
    prompts = result.scalars().all()
    if not prompts:
        raise HTTPException(status_code=404, detail=f"No prompts found for model {model}")

    active_version = next((p.version for p in prompts if p.active), None)

    return PromptListResponse(
        model=model,
        active_version=active_version,
        versions=[_prompt_response(p) for p in prompts],
    )


@router.get("/{model}/v/{version}")
async def get_prompt_version(
    model: str,
    version: int,
    db: AsyncSession = Depends(get_db),
) -> PromptResponse:
    """Get a specific prompt version (includes full prompt text)."""
    prompt = await db.scalar(
        select(LLMPrompt)
        .where(LLMPrompt.model == model, LLMPrompt.version == version)
    )
    if not prompt:
        raise HTTPException(status_code=404, detail=f"Prompt {model} v{version} not found")
    return _prompt_response(prompt, include_prompt=True)


@router.post("/admin", dependencies=[Depends(require_admin_key)])
async def create_prompt(
    data: PromptCreate,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Create a new prompt version for a model.

    Auto-increments version number. Optionally activates it (deactivating previous).
    """
    # Get next version number
    max_version = await db.scalar(
        select(func.max(LLMPrompt.version)).where(LLMPrompt.model == data.model)
    )
    new_version = (max_version or 0) + 1

    # Deactivate previous if activating new one
    if data.activate:
        result = await db.execute(
            select(LLMPrompt)
            .where(LLMPrompt.model == data.model, LLMPrompt.active == True)  # noqa: E712
        )
        for old in result.scalars().all():
            old.active = False

    prompt = LLMPrompt(
        model=data.model,
        version=new_version,
        system_prompt=data.system_prompt,
        active=data.activate,
        notes=data.notes,
    )
    db.add(prompt)
    await db.commit()
    await db.refresh(prompt)

    return {
        "success": True,
        "message": f"Created prompt v{new_version} for {data.model}",
        "prompt": _prompt_response(prompt),
    }


@router.post("/admin/{model}/activate/{version}", dependencies=[Depends(require_admin_key)])
async def activate_prompt(
    model: str,
    version: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Activate a specific prompt version (deactivates others for same model).

    Note: The LLM worker caches the processor, so changes take effect
    on next worker restart or processor recreation.
    """
    target = await db.scalar(
        select(LLMPrompt)
        .where(LLMPrompt.model == model, LLMPrompt.version == version)
    )
    if not target:
        raise HTTPException(status_code=404, detail=f"Prompt {model} v{version} not found")

    # Deactivate all for this model
    result = await db.execute(
        select(LLMPrompt)
        .where(LLMPrompt.model == model, LLMPrompt.active == True)  # noqa: E712
    )
    for old in result.scalars().all():
        old.active = False

    target.active = True
    await db.commit()

    return {
        "success": True,
        "message": f"Activated prompt v{version} for {model}",
        "prompt": _prompt_response(target),
    }
