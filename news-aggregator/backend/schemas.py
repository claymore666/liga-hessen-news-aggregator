"""Pydantic schemas for API request/response validation."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from models import ConnectorType, Priority, RuleType


# === Base schemas ===


class BaseSchema(BaseModel):
    """Base schema with common configuration."""

    model_config = ConfigDict(from_attributes=True)


# === Source schemas ===


class SourceBase(BaseModel):
    """Base source fields."""

    name: str = Field(..., min_length=1, max_length=255)
    connector_type: ConnectorType
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    fetch_interval_minutes: int = Field(default=30, ge=5, le=1440)


class SourceCreate(SourceBase):
    """Schema for creating a source."""

    pass


class SourceUpdate(BaseModel):
    """Schema for updating a source."""

    name: str | None = Field(None, min_length=1, max_length=255)
    config: dict[str, Any] | None = None
    enabled: bool | None = None
    fetch_interval_minutes: int | None = Field(None, ge=5, le=1440)


class SourceResponse(SourceBase, BaseSchema):
    """Schema for source response."""

    id: int
    last_fetch_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


# === Item schemas ===


class ItemBase(BaseModel):
    """Base item fields."""

    title: str
    content: str
    url: str
    author: str | None = None
    published_at: datetime


class ItemResponse(ItemBase, BaseSchema):
    """Schema for item response."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    source_id: int
    external_id: str
    summary: str | None
    fetched_at: datetime
    priority: Priority
    priority_score: int
    is_read: bool
    is_starred: bool
    notes: str | None
    metadata_: dict[str, Any] = Field(default_factory=dict, serialization_alias="metadata")


class ItemUpdate(BaseModel):
    """Schema for updating an item."""

    is_read: bool | None = None
    is_starred: bool | None = None
    notes: str | None = None


class ItemListResponse(BaseModel):
    """Paginated list of items."""

    items: list[ItemResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


# === Rule schemas ===


class RuleBase(BaseModel):
    """Base rule fields."""

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    rule_type: RuleType
    pattern: str = Field(..., min_length=1)
    priority_boost: int = Field(default=0, ge=-100, le=100)
    target_priority: Priority | None = None
    enabled: bool = True
    order: int = Field(default=0, ge=0)


class RuleCreate(RuleBase):
    """Schema for creating a rule."""

    pass


class RuleUpdate(BaseModel):
    """Schema for updating a rule."""

    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    pattern: str | None = Field(None, min_length=1)
    priority_boost: int | None = Field(None, ge=-100, le=100)
    target_priority: Priority | None = None
    enabled: bool | None = None
    order: int | None = Field(None, ge=0)


class RuleResponse(RuleBase, BaseSchema):
    """Schema for rule response."""

    id: int
    created_at: datetime
    updated_at: datetime


# === Connector schemas ===


class ConnectorInfo(BaseModel):
    """Information about an available connector."""

    type: ConnectorType
    name: str
    description: str
    config_schema: dict[str, Any]


# === Stats schemas ===


class StatsResponse(BaseModel):
    """Dashboard statistics."""

    total_items: int
    unread_items: int
    starred_items: int
    critical_items: int
    high_priority_items: int
    sources_count: int
    enabled_sources: int
    rules_count: int
    items_today: int
    items_this_week: int


# === Validation schemas ===


class ValidationResult(BaseModel):
    """Result of source validation."""

    valid: bool
    message: str
