"""Pydantic schemas for API request/response validation."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from models import ConnectorType, Priority, RuleType


# === Base schemas ===


class BaseSchema(BaseModel):
    """Base schema with common configuration."""

    model_config = ConfigDict(from_attributes=True)


# === Channel schemas ===


class ChannelBase(BaseModel):
    """Base channel fields."""

    name: str | None = Field(None, max_length=255)  # Display name like "Aktuell", "Politik"
    connector_type: ConnectorType
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    fetch_interval_minutes: int = Field(default=30, ge=5, le=1440)


class ChannelCreate(ChannelBase):
    """Schema for creating a channel."""

    pass


class ChannelUpdate(BaseModel):
    """Schema for updating a channel."""

    name: str | None = Field(None, max_length=255)
    config: dict[str, Any] | None = None
    enabled: bool | None = None
    fetch_interval_minutes: int | None = Field(None, ge=5, le=1440)


class ChannelResponse(ChannelBase, BaseSchema):
    """Schema for channel response."""

    id: int
    source_id: int
    source_identifier: str | None
    last_fetch_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class ChannelBrief(BaseSchema):
    """Minimal channel info for embedding in item responses."""

    id: int
    connector_type: ConnectorType
    name: str | None
    enabled: bool
    last_error: str | None


# === Source schemas ===


class SourceBase(BaseModel):
    """Base source fields (organization-level)."""

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    is_stakeholder: bool = False  # Never filter out, always keep for training data
    enabled: bool = True  # Master toggle for all channels


class SourceCreate(SourceBase):
    """Schema for creating a source with optional initial channels."""

    channels: list[ChannelCreate] = Field(default_factory=list)


class SourceUpdate(BaseModel):
    """Schema for updating a source (organization-level fields only)."""

    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    enabled: bool | None = None
    is_stakeholder: bool | None = None


class SourceResponse(SourceBase, BaseSchema):
    """Schema for source response with nested channels."""

    id: int
    channels: list[ChannelResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    # Computed properties (set by the API endpoint)
    channel_count: int = 0
    enabled_channel_count: int = 0


class SourceBrief(BaseSchema):
    """Minimal source info for embedding in item responses."""

    id: int
    name: str


# === Item schemas ===


class ItemBase(BaseModel):
    """Base item fields."""

    title: str
    content: str
    url: str
    author: str | None = None
    published_at: datetime


class DuplicateBrief(BaseSchema):
    """Minimal duplicate item info for collapsible grouping."""

    id: int
    title: str
    url: str
    priority: Priority
    source: SourceBrief | None = None
    published_at: datetime


class ItemResponse(ItemBase, BaseSchema):
    """Schema for item response."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    channel_id: int
    channel: ChannelBrief | None = None
    source: SourceBrief | None = None  # Derived from channel.source
    external_id: str
    summary: str | None
    detailed_analysis: str | None = None
    fetched_at: datetime
    priority: Priority
    priority_score: int
    is_read: bool
    is_starred: bool
    is_archived: bool = False
    assigned_aks: list[str] = Field(default_factory=list)
    assigned_ak: str | None = None  # Deprecated, use assigned_aks
    is_manually_reviewed: bool = False
    reviewed_at: datetime | None = None
    notes: str | None
    needs_llm_processing: bool = False
    metadata_: dict[str, Any] = Field(default_factory=dict, serialization_alias="metadata")
    # Duplicate grouping
    similar_to_id: int | None = None  # ID of primary item if this is a duplicate
    duplicates: list[DuplicateBrief] = Field(default_factory=list)  # Child duplicates if this is primary


class ItemUpdate(BaseModel):
    """Schema for updating an item."""

    is_read: bool | None = None
    is_starred: bool | None = None
    is_archived: bool | None = None
    assigned_aks: list[str] | None = None
    assigned_ak: str | None = None  # Deprecated, use assigned_aks
    notes: str | None = None
    # Admin fields for manual corrections
    content: str | None = None
    summary: str | None = None
    detailed_analysis: str | None = None
    priority: str | None = None  # "high", "medium", "low", "none"


class ItemListResponse(BaseModel):
    """Paginated list of items."""

    items: list[ItemResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class BulkArchiveRequest(BaseModel):
    """Request body for bulk archive operations."""

    ids: list[int]
    is_archived: bool = True  # True to archive, False to restore


class BulkArchiveResponse(BaseModel):
    """Response for bulk archive operations."""

    archived: int  # Number of items archived/restored
    item_ids: list[int]  # All affected item IDs (including duplicates)


# === Topic grouping schemas ===


class TopicItemBrief(BaseModel):
    """Brief item info for topic grouping."""

    id: int
    title: str
    url: str
    priority: Priority
    source_name: str | None = None
    source_domain: str | None = None
    published_at: datetime | None = None
    summary: str | None = None
    assigned_aks: list[str] = Field(default_factory=list)
    is_read: bool = False
    duplicates: list[DuplicateBrief] = Field(default_factory=list)


class TopicGroup(BaseModel):
    """A group of items sharing a topic."""

    topic: str
    items: list[TopicItemBrief]


class TopicGroupsResponse(BaseModel):
    """Response for topic-grouped items."""

    topics: list[TopicGroup]
    ungrouped_count: int = 0
    ungrouped_items: list[TopicItemBrief] = Field(default_factory=list)


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
    relevant_items: int  # Items with priority != NONE (Liga-relevant)
    unread_items: int
    starred_items: int
    high_items: int  # High priority count
    medium_items: int  # Medium priority count
    sources_count: int
    channels_count: int  # New: total number of channels
    enabled_sources: int
    enabled_channels: int  # New: enabled channels
    rules_count: int
    items_today: int
    items_this_week: int
    items_by_priority: dict[str, int]  # For frontend compatibility
    last_fetch_at: str | None = None


class SourceStats(BaseModel):
    """Statistics for a single source (organization)."""

    source_id: int
    name: str
    is_stakeholder: bool
    enabled: bool
    channel_count: int
    item_count: int
    unread_count: int


class ChannelStats(BaseModel):
    """Statistics for a single channel."""

    channel_id: int
    source_id: int
    source_name: str
    connector_type: ConnectorType
    name: str | None
    enabled: bool
    item_count: int
    unread_count: int
    last_fetch_at: datetime | None
    last_error: str | None


# === Validation schemas ===


class ValidationResult(BaseModel):
    """Result of source validation."""

    valid: bool
    message: str
