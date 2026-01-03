"""API endpoints for filtering rules."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import Rule
from schemas import RuleCreate, RuleResponse, RuleUpdate

router = APIRouter()


@router.get("/rules", response_model=list[RuleResponse])
async def list_rules(
    db: AsyncSession = Depends(get_db),
    enabled: bool | None = None,
) -> list[RuleResponse]:
    """List all rules."""
    query = select(Rule).order_by(Rule.order, Rule.name)

    if enabled is not None:
        query = query.where(Rule.enabled == enabled)

    result = await db.execute(query)
    rules = result.scalars().all()

    return [RuleResponse.model_validate(rule) for rule in rules]


@router.get("/rules/{rule_id}", response_model=RuleResponse)
async def get_rule(
    rule_id: int,
    db: AsyncSession = Depends(get_db),
) -> RuleResponse:
    """Get a single rule by ID."""
    query = select(Rule).where(Rule.id == rule_id)
    result = await db.execute(query)
    rule = result.scalar_one_or_none()

    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")

    return RuleResponse.model_validate(rule)


@router.post("/rules", response_model=RuleResponse, status_code=201)
async def create_rule(
    rule_data: RuleCreate,
    db: AsyncSession = Depends(get_db),
) -> RuleResponse:
    """Create a new rule."""
    rule = Rule(
        name=rule_data.name,
        description=rule_data.description,
        rule_type=rule_data.rule_type,
        pattern=rule_data.pattern,
        priority_boost=rule_data.priority_boost,
        target_priority=rule_data.target_priority,
        enabled=rule_data.enabled,
        order=rule_data.order,
    )
    db.add(rule)
    await db.flush()
    await db.refresh(rule)

    return RuleResponse.model_validate(rule)


@router.patch("/rules/{rule_id}", response_model=RuleResponse)
async def update_rule(
    rule_id: int,
    update: RuleUpdate,
    db: AsyncSession = Depends(get_db),
) -> RuleResponse:
    """Update a rule."""
    query = select(Rule).where(Rule.id == rule_id)
    result = await db.execute(query)
    rule = result.scalar_one_or_none()

    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")

    update_data = update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(rule, key, value)

    await db.flush()
    await db.refresh(rule)

    return RuleResponse.model_validate(rule)


@router.delete("/rules/{rule_id}", status_code=204)
async def delete_rule(
    rule_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a rule."""
    query = select(Rule).where(Rule.id == rule_id)
    result = await db.execute(query)
    rule = result.scalar_one_or_none()

    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")

    await db.delete(rule)


@router.post("/rules/{rule_id}/test")
async def test_rule(
    rule_id: int,
    content: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Test a rule against sample content."""
    query = select(Rule).where(Rule.id == rule_id)
    result = await db.execute(query)
    rule = result.scalar_one_or_none()

    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")

    # TODO: Implement rule testing logic
    # matcher = RuleMatcher()
    # matches = await matcher.test(rule, content)

    return {
        "rule_id": rule_id,
        "matches": False,  # Placeholder
        "details": "Rule testing not yet implemented",
    }


@router.post("/rules/reorder")
async def reorder_rules(
    rule_orders: list[dict[str, int]],  # [{"id": 1, "order": 0}, ...]
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Reorder rules."""
    for item in rule_orders:
        rule_id = item["id"]
        new_order = item["order"]

        query = select(Rule).where(Rule.id == rule_id)
        result = await db.execute(query)
        rule = result.scalar_one_or_none()

        if rule:
            rule.order = new_order

    return {"status": "ok"}
