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

from models import Channel, Item, Priority, ProcessingStepType, Rule, RuleType
from config import settings

if TYPE_CHECKING:
    from services.processor import ItemProcessor
    from services.processing_logger import ProcessingLogger
    from services.relevance_filter import RelevanceFilter

logger = logging.getLogger(__name__)


def _get_priority_value(priority) -> str:
    """Safely get priority value whether it's an enum or string."""
    if hasattr(priority, 'value'):
        return priority.value
    return str(priority) if priority else "none"


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
        processing_logger: "ProcessingLogger | None" = None,
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
            processing_logger: Optional logger for analytics tracking
        """
        self.db = db
        self.processor = processor
        self.relevance_filter = relevance_filter
        self.training_mode = training_mode
        self.pre_filter_results = pre_filter_results or {}
        self.processing_logger = processing_logger
        # Use naive UTC datetime (consistent with DB storage)
        self.cutoff_date = datetime.utcnow() - timedelta(days=self.MAX_AGE_DAYS)

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
            # Create item-specific logger for this processing run
            item_logger = self.processing_logger.new_item_run() if self.processing_logger else None
            # 1. Skip old items (older than MAX_AGE_DAYS) - disabled in training_mode
            # Normalize published_at to naive UTC for comparison
            pub_dt = raw.published_at
            if pub_dt.tzinfo is not None:
                pub_dt = pub_dt.astimezone(UTC).replace(tzinfo=None)
            if not self.training_mode and pub_dt < self.cutoff_date:
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

            # 3a. Check for semantic duplicates (cross-channel)
            # Instead of skipping, we store the duplicate with similar_to_id pointing to primary
            similar_to_id = None
            duplicates = []
            similarity_score = None
            if self.relevance_filter and not self.training_mode:
                try:
                    duplicates = await self.relevance_filter.find_duplicates(
                        title=normalized.title,
                        content=normalized.content,
                        threshold=0.70,  # Paraphrase model threshold for same-story detection
                    )
                    if duplicates:
                        best_match = duplicates[0]
                        similar_to_id = int(best_match["id"])
                        similarity_score = best_match.get("score")
                        logger.info(
                            f"Semantic duplicate: '{normalized.title[:40]}...' "
                            f"similar to item {similar_to_id} '{best_match.get('title', '')[:40]}...' "
                            f"(score: {best_match.get('score', 0):.3f})"
                        )

                        # Record duplicate detection in audit trail of EXISTING item
                        # First verify the item still exists (vector index may be out of sync)
                        from sqlalchemy import select
                        from services.item_events import record_event, EVENT_DUPLICATE_DETECTED
                        try:
                            existing = await self.db.scalar(
                                select(Item.id).where(Item.id == similar_to_id)
                            )
                            if existing:
                                await record_event(
                                    self.db,
                                    similar_to_id,  # Existing item ID
                                    EVENT_DUPLICATE_DETECTED,
                                    data={
                                        "duplicate_title": normalized.title,
                                        "duplicate_source": channel.source.name if channel.source else None,
                                        "duplicate_channel_id": channel.id,
                                        "duplicate_url": normalized.url,
                                        "similarity_score": best_match.get("score"),
                                    },
                                )
                            else:
                                logger.warning(
                                    f"Skipping duplicate event: item {similar_to_id} no longer exists "
                                    "(vector index out of sync)"
                                )
                        except Exception as e:
                            logger.warning(f"Failed to record duplicate event: {e}")
                except Exception as e:
                    logger.warning(f"Semantic duplicate check failed, continuing: {e}")

            # Log duplicate check result
            if item_logger:
                try:
                    await item_logger.log_duplicate_check(
                        item_id=None,  # Item not created yet
                        is_duplicate=False,  # Not a duplicate (we're continuing)
                        similar_to_id=similar_to_id,
                        similarity_score=similarity_score,
                    )
                except Exception as e:
                    logger.warning(f"Failed to log duplicate check: {e}")

            # 4. Create item
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
                similar_to_id=similar_to_id,  # Link to primary item if duplicate
            )

            # 4. Apply rules and calculate priority (keyword-based first pass)
            # Keywords serve as temporary fallback until classifier processes the item
            await self._apply_rules(item)
            keyword_priority = item.priority
            keyword_score = item.priority_score

            # 5. Classifier takes precedence over keywords
            # Use pre-computed results if available (passed from scheduler to avoid async conflicts)
            # Classifier worker will process items missed here (e.g., during classifier downtime)
            pre_filter_result = None
            skip_llm = False
            if not self.training_mode:
                # Check for pre-computed results first (avoids SQLAlchemy async context issues)
                if normalized.external_id in self.pre_filter_results:
                    cached = self.pre_filter_results[normalized.external_id]
                    pre_filter_result = cached["result"]
                elif self.relevance_filter:
                    # Fallback: call classifier directly (may fail in async SQLAlchemy context)
                    try:
                        _, pre_filter_result = await self.relevance_filter.should_process(
                            title=normalized.title,
                            content=normalized.content,
                            source=channel.source.name if channel.source else "",
                        )
                    except Exception as e:
                        logger.warning(f"Pre-filter failed, using keywords as fallback: {e}")

                # 5a. Apply classifier-based priority (takes precedence over keywords)
                if pre_filter_result:
                    confidence = pre_filter_result.get("relevance_confidence", 0.5)
                    item.priority, item.priority_score, skip_llm = self._priority_from_confidence(confidence)

                    if item.priority != keyword_priority:
                        logger.info(
                            f"Classifier override: {normalized.title[:40]}... "
                            f"conf={confidence:.2f} {_get_priority_value(keyword_priority)}->{_get_priority_value(item.priority)}"
                        )
                    elif skip_llm:
                        logger.info(f"Pre-filtered (irrelevant): {normalized.title[:50]}...")

                    # Log pre-filter step (item_id set later after flush)
                    if item_logger:
                        item_logger._pending_prefilter_log = {
                            "result": pre_filter_result,
                            "priority_input": keyword_priority,
                            "priority_output": item.priority,
                            "skip_llm": skip_llm,
                        }

            # 6. LLM processing is now handled by the LLM worker (llm_worker.py)
            # The worker runs continuously and processes items with priority ordering.
            # Fresh items are enqueued after database flush for immediate processing.

            # 6a. Mark items for LLM processing (worker will process them)
            # Skip if pre-filtered as irrelevant or in training mode
            if not skip_llm and not self.training_mode:
                item.needs_llm_processing = True
                # Store classifier confidence for retry prioritization
                if pre_filter_result:
                    confidence = pre_filter_result.get("relevance_confidence", 0.5)
                    if confidence >= 0.5:
                        item.metadata_["retry_priority"] = "high"
                    elif confidence >= 0.25:
                        item.metadata_["retry_priority"] = "edge_case"
                    else:
                        item.metadata_["retry_priority"] = "low"
                else:
                    # No classifier result - treat as unknown (will be processed by classifier worker)
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
                # Direct mapping: classifier high/medium/low → backend HIGH/MEDIUM/LOW
                if settings.classifier_use_priority and pre_filter_result.get("priority"):
                    clf_priority = pre_filter_result["priority"]
                    if clf_priority == "high":
                        item.priority = Priority.HIGH
                        item.priority_score = 90
                    elif clf_priority == "medium":
                        item.priority = Priority.MEDIUM
                        item.priority_score = 70
                    elif clf_priority == "low":
                        item.priority = Priority.LOW
                        item.priority_score = 50
                    else:
                        # unknown → default to MEDIUM
                        item.priority = Priority.MEDIUM
                        item.priority_score = 60
                    logger.debug(f"Using classifier priority: {clf_priority} -> {_get_priority_value(item.priority)}")

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
            # Store logger reference for post-flush logging
            item._processing_logger = item_logger
            new_items.append(item)

        if new_items:
            await self.db.flush()
            logger.info(f"Created {len(new_items)} new items from channel {channel.id}")

            # Record creation events
            from services.item_events import record_event, EVENT_CREATED

            for item in new_items:
                try:
                    await record_event(
                        self.db,
                        item.id,
                        EVENT_CREATED,
                        data={
                            "channel_id": channel.id,
                            "source": channel.source.name if channel.source else None,
                            "priority": _get_priority_value(item.priority),
                        },
                    )
                except Exception as e:
                    logger.warning(f"Failed to record creation event for item {item.id}: {e}")

                # Log processing steps now that we have item.id
                item_logger = getattr(item, "_processing_logger", None)
                if item_logger:
                    try:
                        # Log pending pre-filter result
                        pending = getattr(item_logger, "_pending_prefilter_log", None)
                        if pending:
                            await item_logger.log_pre_filter(
                                item_id=item.id,
                                result=pending["result"],
                                priority_input=pending["priority_input"],
                                priority_output=pending["priority_output"],
                                skip_llm=pending["skip_llm"],
                            )
                    except Exception as e:
                        logger.warning(f"Failed to log processing steps for item {item.id}: {e}")

            # 9. Index items in vector store for semantic search (async, non-blocking)
            if self.relevance_filter and not self.training_mode:
                indexed_ids = []
                try:
                    items_to_index = [
                        {
                            "id": str(item.id),
                            "title": item.title,
                            "content": item.content,
                            "metadata": {
                                "source": channel.source.name if channel.source else "",
                                "priority": _get_priority_value(item.priority) if item.priority else None,
                                "channel_id": str(channel.id),
                            },
                        }
                        for item in new_items
                    ]
                    indexed = await self.relevance_filter.index_items_batch(items_to_index)
                    if indexed > 0:
                        logger.info(f"Indexed {indexed} items in vector store")
                        # Track which items were indexed (batch indexing is all-or-nothing)
                        indexed_ids = [item.id for item in new_items]
                except Exception as e:
                    logger.warning(f"Failed to index items in vector store: {e}")

                # Update vectordb_indexed flag for successfully indexed items
                if indexed_ids:
                    try:
                        for item in new_items:
                            if item.id in indexed_ids:
                                item.metadata_ = item.metadata_ or {}
                                item.metadata_["vectordb_indexed"] = True
                                item.metadata_["vectordb_indexed_at"] = datetime.utcnow().isoformat()
                        await self.db.commit()
                    except Exception as e:
                        logger.warning(f"Failed to update vectordb_indexed flags: {e}")

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
        # Normalize published_at to UTC naive datetime
        # Database uses TIMESTAMP WITHOUT TIME ZONE, so we must strip tzinfo
        published_at = raw.published_at
        if published_at.tzinfo is not None:
            # Convert to UTC first, then remove timezone info
            published_at = published_at.astimezone(UTC).replace(tzinfo=None)

        # Strip HTML tags from content
        content = re.sub(r"<[^>]+>", "", raw.content)

        # Remove control characters (except newline, tab, carriage return)
        # This prevents issues with parsing, storing, and JSON serialization
        content = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", content)

        # Normalize whitespace
        content = re.sub(r"\s+", " ", content).strip()

        # Fix common encoding issues
        content = content.replace("&amp;", "&")
        content = content.replace("&lt;", "<")
        content = content.replace("&gt;", ">")
        content = content.replace("&quot;", '"')
        content = content.replace("&#39;", "'")

        # Strip HTML tags from title, remove control chars, normalize whitespace
        title = re.sub(r"<[^>]+>", "", raw.title)
        title = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", title)
        title = re.sub(r"\s+", " ", title).strip()

        # Fix HTML entities in title
        title = title.replace("&amp;", "&")
        title = title.replace("&lt;", "<")
        title = title.replace("&gt;", ">")
        title = title.replace("&quot;", '"')
        title = title.replace("&#39;", "'")
        title = title.replace("&nbsp;", " ")

        return RawItem(
            external_id=raw.external_id,
            title=title,
            content=content,
            url=raw.url,
            author=raw.author,
            published_at=published_at,
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

    def _priority_from_confidence(self, confidence: float) -> tuple[Priority, int, bool]:
        """Determine priority based on classifier confidence.

        Classifier takes precedence over keywords.
        Thresholds match classifier_worker.py and admin endpoint.

        Args:
            confidence: Relevance confidence from classifier (0-1)

        Returns:
            Tuple of (priority, score, skip_llm)
        """
        if confidence >= 0.5:
            # Likely relevant - medium priority, let LLM confirm
            return Priority.MEDIUM, 70, False
        elif confidence >= 0.25:
            # Edge case - low priority, let LLM decide
            return Priority.LOW, 55, False
        else:
            # Certainly irrelevant - none priority, skip LLM
            return Priority.NONE, 20, True


async def process_items(
    db: AsyncSession,
    raw_items: list[RawItem],
    channel: Channel,
    processor: "ItemProcessor | None" = None,
    relevance_filter: "RelevanceFilter | None" = None,
    training_mode: bool = False,
    pre_filter_results: dict[str, dict] | None = None,
    processing_logger: "ProcessingLogger | None" = None,
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
        processing_logger: Optional logger for analytics tracking

    Returns:
        List of newly created Item objects
    """
    pipeline = Pipeline(
        db,
        processor=processor,
        relevance_filter=relevance_filter,
        training_mode=training_mode,
        pre_filter_results=pre_filter_results,
        processing_logger=processing_logger,
    )
    return await pipeline.process(raw_items, channel)
