"""SQLAlchemy database models."""

from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class ConnectorType(str, Enum):
    """Available connector types."""

    RSS = "rss"
    HTML = "html"
    BLUESKY = "bluesky"
    TWITTER = "twitter"
    MASTODON = "mastodon"
    LINKEDIN = "linkedin"
    PDF = "pdf"


class Priority(str, Enum):
    """Item priority levels."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class RuleType(str, Enum):
    """Rule matching types."""

    KEYWORD = "keyword"
    REGEX = "regex"
    SEMANTIC = "semantic"


class Source(Base):
    """A configured news source."""

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    connector_type: Mapped[ConnectorType] = mapped_column(String(50))
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(default=True)
    fetch_interval_minutes: Mapped[int] = mapped_column(default=30)
    last_fetch_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    items: Mapped[list["Item"]] = relationship(back_populates="source", cascade="all, delete-orphan")

    __table_args__ = (Index("ix_sources_connector_type", "connector_type"),)


class Item(Base):
    """A fetched news item."""

    __tablename__ = "items"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))
    external_id: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(500))
    content: Mapped[str] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str] = mapped_column(String(2000))
    author: Mapped[str | None] = mapped_column(String(255), nullable=True)
    published_at: Mapped[datetime] = mapped_column(DateTime)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    content_hash: Mapped[str] = mapped_column(String(64))
    priority: Mapped[Priority] = mapped_column(String(20), default=Priority.MEDIUM)
    priority_score: Mapped[int] = mapped_column(default=50)
    is_read: Mapped[bool] = mapped_column(default=False)
    is_starred: Mapped[bool] = mapped_column(default=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)

    # Relationships
    source: Mapped["Source"] = relationship(back_populates="items")
    matched_rules: Mapped[list["ItemRuleMatch"]] = relationship(
        back_populates="item", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_items_source_id", "source_id"),
        Index("ix_items_external_id", "external_id"),
        Index("ix_items_content_hash", "content_hash"),
        Index("ix_items_published_at", "published_at"),
        Index("ix_items_priority", "priority"),
        Index("ix_items_is_read", "is_read"),
    )


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


class Setting(Base):
    """Application settings stored in database."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
