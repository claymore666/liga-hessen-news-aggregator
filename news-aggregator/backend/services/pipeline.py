"""Item normalization and processing pipeline."""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, UTC
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Channel, Item, Priority, Rule, RuleType
from config import settings

if TYPE_CHECKING:
    from services.processor import ItemProcessor
    from services.relevance_filter import RelevanceFilter

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

    # Maximum age of items to store (days)
    MAX_AGE_DAYS = 7

    def __init__(
        self,
        db: AsyncSession,
        processor: ItemProcessor | None = None,
        relevance_filter: "RelevanceFilter | None" = None,
        training_mode: bool = False,
        pre_filter_results: dict[str, dict] | None = None,
    ):
        """Initialize pipeline.

        Args:
            db: Database session
            processor: Optional LLM processor for summarization and semantic rules
            relevance_filter: Optional pre-filter for relevance classification
            training_mode: If True, disables filtering (age, keyword, LLM relevance)
                          for training data collection. Only deduplication remains.
            pre_filter_results: Pre-computed filter results keyed by external_id.
                              If provided, uses these instead of calling relevance_filter
                              to avoid async context conflicts with SQLAlchemy.
        """
        self.db = db
        self.processor = processor
        self.relevance_filter = relevance_filter
        self.training_mode = training_mode
        self.pre_filter_results = pre_filter_results or {}
        self.cutoff_date = datetime.now(UTC) - timedelta(days=self.MAX_AGE_DAYS)

    async def process(self, raw_items: list[RawItem], channel: Channel) -> list[Item]:
        """Process raw items through the pipeline.

        Steps:
        1. Skip items older than MAX_AGE_DAYS (disabled in training_mode)
        2. Normalize content
        3. Check for duplicates
        4. Apply rules
        5. Skip irrelevant items (disabled in training_mode)
        6. LLM analysis with relevance filter (disabled in training_mode)
        7. Store in database

        Args:
            raw_items: List of raw items from connector
            channel: Channel the items came from

        Returns:
            List of newly created items.
        """
        new_items = []

        for raw in raw_items:
            # 1. Skip old items (older than MAX_AGE_DAYS) - disabled in training_mode
            if not self.training_mode and raw.published_at.replace(tzinfo=UTC) < self.cutoff_date:
                logger.debug(f"Skipping old item: {raw.title[:50]} ({raw.published_at})")
                continue

            # 2. Normalize content
            normalized = self._normalize_content(raw)

            # 3. Check for duplicates
            content_hash = self._compute_hash(normalized.content)
            is_duplicate = await self._is_duplicate(
                channel.id, normalized.external_id, content_hash
            )

            if is_duplicate:
                logger.debug(f"Skipping duplicate: {normalized.title[:50]}")
                continue

            # 3. Create item
            item = Item(
                channel_id=channel.id,
                external_id=normalized.external_id,
                title=normalized.title,
                content=normalized.content,
                url=normalized.url,
                author=normalized.author,
                published_at=normalized.published_at,
                content_hash=content_hash,
                metadata_=normalized.metadata,
            )

            # 4. Apply rules and calculate priority (keyword-based first pass)
            await self._apply_rules(item)

            # 5. Pre-filter with embedding classifier (skip LLM for clearly irrelevant items)
            # Use pre-computed results if available (passed from scheduler to avoid async conflicts)
            pre_filter_result = None
            skip_llm = False
            if not self.training_mode:
                # Check for pre-computed results first (avoids SQLAlchemy async context issues)
                if normalized.external_id in self.pre_filter_results:
                    cached = self.pre_filter_results[normalized.external_id]
                    should_process = cached["should_process"]
                    pre_filter_result = cached["result"]
                    if not should_process:
                        logger.info(f"Pre-filtered (irrelevant): {normalized.title[:50]}...")
                        item.priority = Priority.NONE
                        item.priority_score = 10
                        skip_llm = True
                elif self.relevance_filter:
                    # Fallback: call classifier directly (may fail in async SQLAlchemy context)
                    try:
                        should_process, pre_filter_result = await self.relevance_filter.should_process(
                            title=normalized.title,
                            content=normalized.content,
                            source=channel.source.name if channel.source else "",
                        )
                        if not should_process:
                            logger.info(f"Pre-filtered (irrelevant): {normalized.title[:50]}...")
                            item.priority = Priority.NONE
                            item.priority_score = 10
                            skip_llm = True
                    except Exception as e:
                        logger.warning(f"Pre-filter failed, continuing with LLM: {e}")

            # 6. LLM processing is now handled by the LLM worker (llm_worker.py)
            # The worker runs continuously and processes items with priority ordering.
            # Fresh items are enqueued after database flush for immediate processing.

            # 6a. Mark items for LLM processing (worker will process them)
            # Skip if pre-filtered as irrelevant or in training mode
            if not skip_llm and not self.training_mode:
                item.needs_llm_processing = True
                # Store classifier confidence for retry prioritization
                # Thresholds: high >= 0.5, edge_case >= 0.25, low < 0.25
                if pre_filter_result:
                    confidence = pre_filter_result.get("relevance_confidence", 0.5)
                    if confidence >= 0.5:
                        item.metadata_["retry_priority"] = "high"
                    elif confidence >= 0.25:
                        item.metadata_["retry_priority"] = "edge_case"
                    else:
                        item.metadata_["retry_priority"] = "low"
                else:
                    # No classifier result - treat as unknown
                    item.metadata_["retry_priority"] = "unknown"
                logger.info(f"Marked for LLM retry: {normalized.title[:40]} (priority: {item.metadata_.get('retry_priority')})")

            # 7. Store pre-filter result in metadata if available
            if pre_filter_result:
                item.metadata_["pre_filter"] = {
                    "relevance_confidence": pre_filter_result.get("relevance_confidence"),
                    "ak_suggestion": pre_filter_result.get("ak"),
                    "ak_confidence": pre_filter_result.get("ak_confidence"),
                    "priority_suggestion": pre_filter_result.get("priority"),
                    "priority_confidence": pre_filter_result.get("priority_confidence"),
                }

                # 7a. Optionally use classifier priority instead of LLM
                # Map classifier output to new priority system (critical→high, high→medium, medium→low, low→none)
                if settings.classifier_use_priority and pre_filter_result.get("priority"):
                    clf_priority = pre_filter_result["priority"]
                    if clf_priority == "critical":
                        item.priority = Priority.HIGH
                        item.priority_score = 90
                    elif clf_priority == "high":
                        item.priority = Priority.MEDIUM
                        item.priority_score = 70
                    elif clf_priority == "medium":
                        item.priority = Priority.LOW
                        item.priority_score = 50
                    else:
                        # "low" or unknown → NONE (not relevant)
                        item.priority = Priority.NONE
                        item.priority_score = 30
                    logger.debug(f"Using classifier priority: {clf_priority} -> {item.priority.value}")

                # 7b. Optionally use classifier AK instead of LLM
                if settings.classifier_use_ak and pre_filter_result.get("ak"):
                    clf_ak = pre_filter_result["ak"]
                    # Store in metadata (AK is stored in llm_analysis.assigned_ak)
                    if "llm_analysis" not in item.metadata_:
                        item.metadata_["llm_analysis"] = {}
                    item.metadata_["llm_analysis"]["assigned_ak"] = clf_ak
                    item.metadata_["llm_analysis"]["ak_source"] = "classifier"
                    logger.debug(f"Using classifier AK: {clf_ak}")

            # 8. Add to database
            self.db.add(item)
            new_items.append(item)

        if new_items:
            await self.db.flush()
            logger.info(f"Created {len(new_items)} new items from channel {channel.id}")

            # 9. Index items in vector store for semantic search (async, non-blocking)
            if self.relevance_filter and not self.training_mode:
                try:
                    items_to_index = [
                        {
                            "id": str(item.id),
                            "title": item.title,
                            "content": item.content,
                            "metadata": {
                                "source": channel.source.name if channel.source else "",
                                "priority": item.priority.value if item.priority else None,
                                "channel_id": str(channel.id),
                            },
                        }
                        for item in new_items
                    ]
                    indexed = await self.relevance_filter.index_items_batch(items_to_index)
                    if indexed > 0:
                        logger.info(f"Indexed {indexed} items in vector store")
                except Exception as e:
                    logger.warning(f"Failed to index items in vector store: {e}")

            # 10. Enqueue fresh items to LLM worker for immediate processing
            if not self.training_mode:
                from services.llm_worker import enqueue_fresh_item

                items_to_process = [
                    item for item in new_items
                    if item.needs_llm_processing
                ]
                for item in items_to_process:
                    await enqueue_fresh_item(item.id)

                if items_to_process:
                    logger.info(f"Enqueued {len(items_to_process)} fresh items to LLM worker")

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
        self, channel_id: int, external_id: str, content_hash: str
    ) -> bool:
        """Check if item already exists."""
        # Check by external_id first (faster)
        query = select(Item.id).where(
            Item.channel_id == channel_id,
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
            # Check non-semantic rules synchronously
            if rule.rule_type != RuleType.SEMANTIC:
                matched = self._match_rule(rule, item)
            else:
                # Check semantic rules using LLM
                matched = await self._match_semantic_rule(rule, item)

            if matched:
                total_boost += rule.priority_boost
                if rule.target_priority:
                    target_priority = rule.target_priority

                # Record match (TODO: create ItemRuleMatch)
                logger.debug(f"Rule '{rule.name}' matched item: {item.title[:50]}")

        # Calculate keyword-based score if processor available
        keyword_score = 0
        if self.processor:
            keyword_score, _ = self.processor.calculate_keyword_score(item)
            keyword_score = keyword_score - 50  # Convert to boost (base is 50)

        # Calculate final priority score (0-100)
        base_score = 50
        item.priority_score = max(0, min(100, base_score + total_boost + keyword_score))

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
            # LLM-based semantic matching (handled async separately)
            # This is checked in _apply_rules_async
            return False

        return False

    async def _match_semantic_rule(self, rule: Rule, item: Item) -> bool:
        """Check if item matches a semantic rule using LLM.

        Args:
            rule: Semantic rule to check
            item: Item to match against

        Returns:
            True if rule matches, False otherwise
        """
        if not self.processor:
            return False

        try:
            return await self.processor.check_semantic_rule(item, rule)
        except Exception as e:
            logger.warning(f"Semantic rule check failed: {e}")
            return False

    def _score_to_priority(self, score: int) -> Priority:
        """Convert numeric score to priority level.

        Base score is 50. Items must have keyword matches to be relevant.
        - >= 90: HIGH (major issues, urgent)
        - >= 70: MEDIUM (legislation, reforms)
        - > 50: LOW (has some Liga-relevant keywords)
        - <= 50: NONE (no relevant keyword matches - not Liga-relevant)
        """
        if score >= 90:
            return Priority.HIGH
        elif score >= 70:
            return Priority.MEDIUM
        elif score > 50:
            return Priority.LOW
        else:
            return Priority.NONE


async def process_items(
    db: AsyncSession,
    raw_items: list[RawItem],
    channel: Channel,
    processor: "ItemProcessor | None" = None,
    relevance_filter: "RelevanceFilter | None" = None,
    training_mode: bool = False,
    pre_filter_results: dict[str, dict] | None = None,
) -> list[Item]:
    """Convenience function to process items through the pipeline.

    Args:
        db: Database session
        raw_items: List of raw items from connector
        channel: Channel the items came from
        processor: Optional LLM processor for summarization and semantic rules
        relevance_filter: Optional pre-filter for relevance classification
        training_mode: If True, disables filtering for training data collection
        pre_filter_results: Pre-computed filter results keyed by external_id

    Returns:
        List of newly created Item objects
    """
    pipeline = Pipeline(
        db,
        processor=processor,
        relevance_filter=relevance_filter,
        training_mode=training_mode,
        pre_filter_results=pre_filter_results,
    )
    return await pipeline.process(raw_items, channel)
