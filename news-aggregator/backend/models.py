"""SQLAlchemy database models."""

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
import uuid

from database import Base

if TYPE_CHECKING:
    pass


class ConnectorType(str, Enum):
    """Available connector types."""

    RSS = "rss"
    HTML = "html"
    BLUESKY = "bluesky"
    TWITTER = "twitter"
    MASTODON = "mastodon"
    LINKEDIN = "linkedin"
    PDF = "pdf"
    X_SCRAPER = "x_scraper"
    INSTAGRAM = "instagram"
    INSTAGRAM_SCRAPER = "instagram_scraper"
    TELEGRAM = "telegram"
    GOOGLE_ALERTS = "google_alerts"


class Priority(str, Enum):
    """Item priority levels."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"  # Not relevant


class RuleType(str, Enum):
    """Rule matching types."""

    KEYWORD = "keyword"
    REGEX = "regex"
    SEMANTIC = "semantic"


class Source(Base):
    """An organization or entity we track (e.g., BMAS, SPD Hessen).

    Sources represent organizations that may have multiple channels (RSS, X.com, etc.).
    """

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_stakeholder: Mapped[bool] = mapped_column(default=False)  # Never filter out
    enabled: Mapped[bool] = mapped_column(default=True)  # Master toggle for all channels
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    channels: Mapped[list["Channel"]] = relationship(
        back_populates="source",
        cascade="all, delete-orphan",
        order_by="Channel.connector_type",
    )

    @property
    def active_channels(self) -> list["Channel"]:
        """Return enabled channels for this source."""
        return [c for c in self.channels if c.enabled]

    @property
    def channel_count(self) -> int:
        """Return total number of channels."""
        return len(self.channels)

    @property
    def has_errors(self) -> bool:
        """Return True if any channel has an error."""
        return any(c.last_error for c in self.channels)


class Channel(Base):
    """A specific feed/channel for a source (e.g., RSS, X.com, Instagram).

    Each channel represents one data source (URL, handle, etc.) with its own
    fetch interval and configuration.
    """

    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)  # e.g., "Aktuell", "Politik"
    connector_type: Mapped[ConnectorType] = mapped_column(String(50))
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source_identifier: Mapped[str | None] = mapped_column(String(500), nullable=True)
    enabled: Mapped[bool] = mapped_column(default=True)
    fetch_interval_minutes: Mapped[int] = mapped_column(default=30)
    last_fetch_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    source: Mapped["Source"] = relationship(back_populates="channels")
    items: Mapped[list["Item"]] = relationship(back_populates="channel", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_channels_source_id", "source_id"),
        Index("ix_channels_connector_type", "connector_type"),
        Index(
            "ix_channels_unique_identifier",
            "source_id",
            "connector_type",
            "source_identifier",
            unique=True,
        ),
    )

    @staticmethod
    def extract_identifier(connector_type: str, config: dict[str, Any]) -> str | None:
        """Extract the unique identifier from config based on connector type."""
        if connector_type in ("x_scraper", "twitter", "instagram", "instagram_scraper"):
            return config.get("username", "").lower()
        elif connector_type in ("mastodon", "bluesky"):
            return config.get("handle", "").lower()
        elif connector_type in ("rss", "html", "pdf", "google_alerts"):
            return config.get("url", "").lower()
        elif connector_type == "telegram":
            return config.get("channel", "").lower()
        elif connector_type == "linkedin":
            # Extract profile ID from URL (e.g., /company/microsoft or /in/satya-nadella)
            import re

            url = config.get("profile_url", "")
            if "/company/" in url:
                match = re.search(r"/company/([^/]+)", url)
                return match.group(1).lower() if match else None
            elif "/in/" in url:
                match = re.search(r"/in/([^/]+)", url)
                return match.group(1).lower() if match else None
            return url.lower() if url else None
        return None

    @property
    def display_name(self) -> str:
        """Return display name for this channel."""
        if self.name:
            return self.name
        return {
            "rss": "RSS Feed",
            "x_scraper": "X.com",
            "mastodon": "Mastodon",
            "bluesky": "Bluesky",
            "instagram_scraper": "Instagram",
            "instagram": "Instagram",
            "telegram": "Telegram",
            "html": "Website",
            "pdf": "PDF",
            "google_alerts": "Google Alerts",
            "twitter": "Twitter",
            "linkedin": "LinkedIn",
        }.get(self.connector_type, self.connector_type)

    @property
    def is_effectively_enabled(self) -> bool:
        """Return True if both channel and parent source are enabled."""
        return self.enabled and self.source.enabled


class Item(Base):
    """A fetched news item."""

    __tablename__ = "items"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"))
    external_id: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(500))
    content: Mapped[str] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    detailed_analysis: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str] = mapped_column(String(2000))
    author: Mapped[str | None] = mapped_column(String(255), nullable=True)
    published_at: Mapped[datetime] = mapped_column(DateTime)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    content_hash: Mapped[str] = mapped_column(String(64))
    priority: Mapped[Priority] = mapped_column(String(20), default=Priority.LOW)
    priority_score: Mapped[int] = mapped_column(default=50)
    is_read: Mapped[bool] = mapped_column(default=False)
    is_starred: Mapped[bool] = mapped_column(default=False)
    is_archived: Mapped[bool] = mapped_column(default=False)
    assigned_ak: Mapped[str | None] = mapped_column(String(10), nullable=True)  # Deprecated, use assigned_aks
    assigned_aks: Mapped[list[str]] = mapped_column(JSON, default=list)  # Array of AK codes
    is_manually_reviewed: Mapped[bool] = mapped_column(default=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    # LLM processing status - True if item needs (re)processing due to GPU unavailability
    needs_llm_processing: Mapped[bool] = mapped_column(default=False, index=True)
    # Semantic duplicate grouping - points to the "primary" item this is a duplicate of
    similar_to_id: Mapped[int | None] = mapped_column(
        ForeignKey("items.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Relationships
    channel: Mapped["Channel"] = relationship(back_populates="items")
    # Self-referential relationship for duplicates
    similar_to: Mapped["Item | None"] = relationship(
        "Item", remote_side="Item.id", foreign_keys=[similar_to_id], backref="duplicates"
    )
    matched_rules: Mapped[list["ItemRuleMatch"]] = relationship(
        back_populates="item", cascade="all, delete-orphan"
    )
    events: Mapped[list["ItemEvent"]] = relationship(
        back_populates="item", cascade="all, delete-orphan", order_by="ItemEvent.timestamp.desc()"
    )

    __table_args__ = (
        Index("ix_items_channel_id", "channel_id"),
        Index("ix_items_external_id", "external_id"),
        Index("ix_items_content_hash", "content_hash"),
        Index("ix_items_published_at", "published_at"),
        Index("ix_items_priority", "priority"),
        Index("ix_items_is_read", "is_read"),
    )

    @property
    def source(self) -> "Source":
        """Get the parent source (organization) through the channel."""
        return self.channel.source

    @property
    def source_id(self) -> int:
        """Backward compatibility: get source_id through channel."""
        return self.channel.source_id


class Rule(Base):
    """A filtering/priority rule."""

    __tablename__ = "rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    rule_type: Mapped[RuleType] = mapped_column(String(20))
    pattern: Mapped[str] = mapped_column(Text)  # keyword, regex pattern, or semantic description
    priority_boost: Mapped[int] = mapped_column(default=0)  # Additive score adjustment
    target_priority: Mapped[Priority | None] = mapped_column(String(20), nullable=True)
    enabled: Mapped[bool] = mapped_column(default=True)
    order: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    matches: Mapped[list["ItemRuleMatch"]] = relationship(
        back_populates="rule", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_rules_enabled_order", "enabled", "order"),)


class ItemRuleMatch(Base):
    """Junction table for items matched by rules."""

    __tablename__ = "item_rule_matches"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"))
    rule_id: Mapped[int] = mapped_column(ForeignKey("rules.id", ondelete="CASCADE"))
    matched_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    match_details: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # Relationships
    item: Mapped["Item"] = relationship(back_populates="matched_rules")
    rule: Mapped["Rule"] = relationship(back_populates="matches")

    __table_args__ = (
        Index("ix_item_rule_matches_item_id", "item_id"),
        Index("ix_item_rule_matches_rule_id", "rule_id"),
    )


class ItemEvent(Base):
    """Audit trail event for an item."""

    __tablename__ = "item_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"))
    event_type: Mapped[str] = mapped_column(String(50))
    timestamp: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)  # IPv6 compatible
    session_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # Relationships
    item: Mapped["Item"] = relationship(back_populates="events")

    __table_args__ = (
        Index("ix_item_events_item_id", "item_id"),
        Index("ix_item_events_event_type", "event_type"),
        Index("ix_item_events_timestamp", "timestamp"),
    )


class Setting(Base):
    """Application settings stored in database."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class ProcessingStepType(str, Enum):
    """Processing step types for analytics logging."""

    FETCH = "fetch"
    PRE_FILTER = "pre_filter"
    DUPLICATE_CHECK = "duplicate_check"
    RULE_MATCH = "rule_match"
    CLASSIFIER_OVERRIDE = "classifier_override"
    LLM_ANALYSIS = "llm_analysis"
    REPROCESS = "reprocess"


class ItemProcessingLog(Base):
    """Processing step log for analytics and debugging.

    Records every processing step for items to enable:
    - Reproducing how a message ended up with its current priority/classification
    - Finding items where classifier and/or LLM were unsure (low confidence)
    - Tracking reprocessing events
    - Comparing classifier vs LLM decisions
    - Training data collection for model improvement
    """

    __tablename__ = "item_processing_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int | None] = mapped_column(
        ForeignKey("items.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # UUID for linking all steps in one processing run
    processing_run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)

    # Step identification
    step_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)

    # Timestamps
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Model information
    model_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    model_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Queryable scores (denormalized for fast filtering)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    priority_input: Mapped[str | None] = mapped_column(String(20), nullable=True)
    priority_output: Mapped[str | None] = mapped_column(String(20), nullable=True)
    priority_changed: Mapped[bool] = mapped_column(Boolean, default=False)
    ak_suggestions: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    ak_primary: Mapped[str | None] = mapped_column(String(10), nullable=True)
    ak_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    relevant: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    relevance_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Processing outcome
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    skipped: Mapped[bool] = mapped_column(Boolean, default=False)
    skip_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Full data (JSON for flexibility)
    input_data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    output_data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # Relationships
    item: Mapped["Item | None"] = relationship(
        "Item", foreign_keys=[item_id], backref="processing_logs"
    )

    __table_args__ = (
        Index("ix_processing_logs_created_at", "created_at"),
        Index(
            "ix_processing_logs_low_confidence",
            "step_type",
            "confidence_score",
            postgresql_where=(confidence_score < 0.5),
        ),
        Index(
            "ix_processing_logs_priority_changed",
            "step_type",
            "priority_changed",
            postgresql_where=(priority_changed == True),  # noqa: E712
        ),
    )
