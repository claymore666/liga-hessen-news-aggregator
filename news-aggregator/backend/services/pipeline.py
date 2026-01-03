"""Item normalization and processing pipeline."""

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Item, Priority, Rule, RuleType, Source

logger = logging.getLogger(__name__)


@dataclass
class RawItem:
    """Normalized item format from connectors."""

    external_id: str
    title: str
    content: str
    url: str
    author: str | None = None
    published_at: datetime = field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)


class Pipeline:
    """Processing pipeline for raw items."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def process(self, raw_items: list[RawItem], source: Source) -> list[Item]:
        """Process raw items through the pipeline.

        Steps:
        1. Normalize content
        2. Check for duplicates
        3. Apply rules
        4. Calculate priority
        5. Store in database

        Returns:
            List of newly created items.
        """
        new_items = []

        for raw in raw_items:
            # 1. Normalize content
            normalized = self._normalize_content(raw)

            # 2. Check for duplicates
            content_hash = self._compute_hash(normalized.content)
            is_duplicate = await self._is_duplicate(
                source.id, normalized.external_id, content_hash
            )

            if is_duplicate:
                logger.debug(f"Skipping duplicate: {normalized.title[:50]}")
                continue

            # 3. Create item
            item = Item(
                source_id=source.id,
                external_id=normalized.external_id,
                title=normalized.title,
                content=normalized.content,
                url=normalized.url,
                author=normalized.author,
                published_at=normalized.published_at,
                content_hash=content_hash,
                metadata_=normalized.metadata,
            )

            # 4. Apply rules and calculate priority
            await self._apply_rules(item)

            # 5. Add to database
            self.db.add(item)
            new_items.append(item)

        if new_items:
            await self.db.flush()
            logger.info(f"Created {len(new_items)} new items from source {source.id}")

        return new_items

    def _normalize_content(self, raw: RawItem) -> RawItem:
        """Normalize content (strip HTML, fix encoding, etc.)."""
        # Strip HTML tags from content
        content = re.sub(r"<[^>]+>", "", raw.content)

        # Normalize whitespace
        content = re.sub(r"\s+", " ", content).strip()

        # Fix common encoding issues
        content = content.replace("&amp;", "&")
        content = content.replace("&lt;", "<")
        content = content.replace("&gt;", ">")
        content = content.replace("&quot;", '"')
        content = content.replace("&#39;", "'")

        # Strip HTML tags from title and normalize whitespace
        title = re.sub(r"<[^>]+>", "", raw.title)
        title = re.sub(r"\s+", " ", title).strip()

        return RawItem(
            external_id=raw.external_id,
            title=title,
            content=content,
            url=raw.url,
            author=raw.author,
            published_at=raw.published_at,
            metadata=raw.metadata,
        )

    def _compute_hash(self, content: str) -> str:
        """Compute SHA-256 hash of content."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    async def _is_duplicate(
        self, source_id: int, external_id: str, content_hash: str
    ) -> bool:
        """Check if item already exists."""
        # Check by external_id first (faster)
        query = select(Item.id).where(
            Item.source_id == source_id,
            Item.external_id == external_id,
        )
        result = await self.db.execute(query)
        if result.scalar_one_or_none() is not None:
            return True

        # Check by content hash (catches reposts/copies)
        query = select(Item.id).where(Item.content_hash == content_hash)
        result = await self.db.execute(query)
        return result.scalar_one_or_none() is not None

    async def _apply_rules(self, item: Item) -> None:
        """Apply matching rules and calculate priority score."""
        # Get enabled rules
        query = select(Rule).where(Rule.enabled == True).order_by(Rule.order)  # noqa: E712
        result = await self.db.execute(query)
        rules = result.scalars().all()

        total_boost = 0
        target_priority = None

        for rule in rules:
            matched = self._match_rule(rule, item)
            if matched:
                total_boost += rule.priority_boost
                if rule.target_priority:
                    target_priority = rule.target_priority

                # Record match (TODO: create ItemRuleMatch)
                logger.debug(f"Rule '{rule.name}' matched item: {item.title[:50]}")

        # Calculate final priority score (0-100)
        base_score = 50
        item.priority_score = max(0, min(100, base_score + total_boost))

        # Set priority level
        if target_priority:
            item.priority = target_priority
        else:
            item.priority = self._score_to_priority(item.priority_score)

    def _match_rule(self, rule: Rule, item: Item) -> bool:
        """Check if rule matches item."""
        text = f"{item.title} {item.content}".lower()

        if rule.rule_type == RuleType.KEYWORD:
            # Simple keyword matching (case-insensitive)
            keywords = [k.strip().lower() for k in rule.pattern.split(",")]
            return any(kw in text for kw in keywords)

        elif rule.rule_type == RuleType.REGEX:
            # Regex matching
            try:
                pattern = re.compile(rule.pattern, re.IGNORECASE)
                return bool(pattern.search(text))
            except re.error:
                logger.warning(f"Invalid regex in rule {rule.id}: {rule.pattern}")
                return False

        elif rule.rule_type == RuleType.SEMANTIC:
            # TODO: Implement LLM-based semantic matching
            # This requires the LLM service to be available
            return False

        return False

    def _score_to_priority(self, score: int) -> Priority:
        """Convert numeric score to priority level."""
        if score >= 90:
            return Priority.CRITICAL
        elif score >= 70:
            return Priority.HIGH
        elif score >= 40:
            return Priority.MEDIUM
        else:
            return Priority.LOW


async def process_items(
    db: AsyncSession, raw_items: list[RawItem], source: Source
) -> list[Item]:
    """Convenience function to process items through the pipeline."""
    pipeline = Pipeline(db)
    return await pipeline.process(raw_items, source)
