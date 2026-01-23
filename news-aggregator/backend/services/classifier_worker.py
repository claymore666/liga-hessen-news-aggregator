"""
Classifier Background Worker.

Continuously processes items through the embedding classifier:
1. Items without pre_filter (never classified)
2. Updates priority based on classifier confidence

This ensures items fetched during classifier downtime get properly
evaluated when the classifier comes back online.
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.orm import selectinload

from database import async_session_maker
from models import Channel, Item, Priority
from services.pipeline import _strip_boilerplate

logger = logging.getLogger(__name__)


# Priority thresholds based on classifier confidence
CONFIDENCE_HIGH = 0.5      # conf >= 0.5: likely relevant
CONFIDENCE_EDGE = 0.25     # 0.25 <= conf < 0.5: edge case, needs LLM
# conf < 0.25: certainly irrelevant, skip LLM


class ClassifierWorker:
    """
    Background worker for classifier processing.

    Processes items that have never been classified (no pre_filter metadata)
    and updates their priority based on classifier confidence.
    """

    def __init__(
        self,
        batch_size: int = 50,
        idle_sleep: float = 60.0,
    ):
        """
        Initialize the classifier worker.

        Args:
            batch_size: Items to process per batch
            idle_sleep: Seconds to sleep when no work available
        """
        self.batch_size = batch_size
        self.idle_sleep = idle_sleep

        # Worker state
        self._running = False
        self._paused = False
        self._task: Optional[asyncio.Task] = None
        self._classifier = None

        # Statistics
        self._stats = {
            "processed": 0,
            "priority_changed": 0,
            "duplicates_found": 0,
            "duplicates_checked": 0,
            "vectordb_indexed": 0,
            "errors": 0,
            "started_at": None,
            "last_processed_at": None,
        }

    async def start(self):
        """Start the worker background task."""
        if self._running:
            logger.warning("Classifier worker already running")
            return

        self._running = True
        self._stats["started_at"] = datetime.utcnow().isoformat()
        self._task = asyncio.create_task(self._run())
        logger.info("Classifier worker started")

    async def stop(self):
        """Stop the worker gracefully."""
        if not self._running:
            return

        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Classifier worker stopped")

    def pause(self):
        """Pause processing."""
        self._paused = True
        logger.info("Classifier worker paused")

    def resume(self):
        """Resume processing."""
        self._paused = False
        logger.info("Classifier worker resumed")

    def get_status(self) -> dict:
        """Get worker status and statistics."""
        return {
            "running": self._running,
            "paused": self._paused,
            "stats": self._stats.copy(),
        }

    async def _get_classifier(self):
        """Get or create the classifier instance."""
        if self._classifier is None:
            from services.relevance_filter import create_relevance_filter
            self._classifier = await create_relevance_filter()
        return self._classifier

    async def _run(self):
        """Main worker loop."""
        logger.info("Classifier worker loop started")

        while self._running:
            try:
                # Check if paused
                if self._paused:
                    await asyncio.sleep(1.0)
                    continue

                # Priority 1: Process unclassified items
                processed = await self._process_unclassified_items()
                if processed > 0:
                    # More items might be available
                    await asyncio.sleep(0.5)
                    continue

                # Priority 2: Re-check duplicates for items that missed the check
                duplicates_checked = await self._process_unchecked_duplicates()
                if duplicates_checked > 0:
                    await asyncio.sleep(0.5)
                    continue

                # Priority 3: Re-index items that weren't indexed in vector store
                indexed = await self._process_unindexed_items()
                if indexed > 0:
                    await asyncio.sleep(0.5)
                    continue

                # No work available, sleep
                logger.debug(f"No unclassified items, unchecked duplicates, or unindexed items, sleeping {self.idle_sleep}s")
                await asyncio.sleep(self.idle_sleep)

            except asyncio.CancelledError:
                logger.info("Classifier worker cancelled")
                break
            except Exception as e:
                logger.error(f"Classifier worker error: {e}", exc_info=True)
                self._stats["errors"] += 1
                await asyncio.sleep(10.0)  # Back off on error

        logger.info("Classifier worker loop ended")

    async def _process_unclassified_items(self) -> int:
        """
        Process items without pre_filter classification.

        Returns:
            Number of items processed
        """
        try:
            classifier = await self._get_classifier()
            if not classifier:
                logger.debug("Classifier unavailable")
                return 0
        except Exception as e:
            logger.debug(f"Cannot create classifier: {e}")
            return 0

        # Phase 1: Query items without pre_filter (read-only, no lock needed)
        async with async_session_maker() as db:
            from database import json_extract_path
            query = (
                select(Item)
                .where(
                    json_extract_path(Item.metadata_, "pre_filter").is_(None),
                )
                .options(selectinload(Item.channel).selectinload(Channel.source))
                .order_by(Item.fetched_at.desc())
                .limit(self.batch_size)
            )

            result = await db.execute(query)
            items = result.scalars().all()

            if not items:
                return 0

            # Extract data needed for classification (avoid keeping ORM objects)
            items_to_classify = []
            for item in items:
                source_name = ""
                if item.channel and item.channel.source:
                    source_name = item.channel.source.name
                items_to_classify.append({
                    "id": item.id,
                    "title": item.title,
                    "content": item.content,
                    "source": source_name,
                    "old_priority": item.priority.value if hasattr(item.priority, 'value') else str(item.priority),
                    "old_metadata": dict(item.metadata_) if item.metadata_ else {},
                })

        logger.info(f"Classifying {len(items_to_classify)} unclassified items")

        # Phase 2: Classify items (external HTTP calls, no lock needed)
        updates = []
        processed = 0
        priority_changed = 0

        for item_data in items_to_classify:
            if self._paused or not self._running:
                break

            try:
                # Classify the item
                result = await classifier.classify(
                    title=item_data["title"],
                    content=item_data["content"],
                    source=item_data["source"],
                )

                confidence = result.get("relevance_confidence", 0.5)
                old_priority = item_data["old_priority"]

                # Determine new priority based on confidence
                new_priority, new_score, skip_llm = self._determine_priority(confidence)

                # Prepare updated metadata
                new_metadata = dict(item_data["old_metadata"])
                new_metadata["pre_filter"] = {
                    "relevance_confidence": confidence,
                    "ak_suggestion": result.get("ak"),
                    "ak_confidence": result.get("ak_confidence"),
                    "priority_suggestion": result.get("priority"),
                    "priority_confidence": result.get("priority_confidence"),
                    "classified_at": datetime.utcnow().isoformat(),
                }

                # Set retry priority for LLM worker
                if confidence >= CONFIDENCE_HIGH:
                    new_metadata["retry_priority"] = "high"
                elif confidence >= CONFIDENCE_EDGE:
                    new_metadata["retry_priority"] = "edge_case"
                else:
                    new_metadata["retry_priority"] = "low"

                # Collect update
                updates.append({
                    "id": item_data["id"],
                    "old_priority": old_priority,
                    "priority": new_priority.value,
                    "priority_score": new_score,
                    "metadata_": new_metadata,
                    "needs_llm_processing": not skip_llm,
                })

                processed += 1
                if old_priority != new_priority.value:
                    priority_changed += 1
                    logger.info(
                        f"Classified: {item_data['title'][:40]}... "
                        f"conf={confidence:.2f} {old_priority}->{new_priority.value}"
                    )

            except Exception as e:
                logger.warning(f"Failed to classify item {item_data['id']}: {e}")
                self._stats["errors"] += 1

        # Phase 3: Apply updates to database
        # Note: No global lock needed - PostgreSQL MVCC handles concurrent writes
        if updates:
            from services.item_events import record_events_batch, EVENT_CLASSIFIER_PROCESSED

            try:
                async with async_session_maker() as db:
                    # Batch update items (individual updates required due to different metadata per item)
                    for upd in updates:
                        await db.execute(
                            update(Item)
                            .where(Item.id == upd["id"])
                            .values(
                                priority=upd["priority"],
                                priority_score=upd["priority_score"],
                                metadata_=upd["metadata_"],
                                needs_llm_processing=upd["needs_llm_processing"],
                            )
                        )

                    # Batch record classifier events (more efficient than individual calls)
                    events_data = [
                        {
                            "item_id": upd["id"],
                            "event_type": EVENT_CLASSIFIER_PROCESSED,
                            "data": {
                                "confidence": upd["metadata_"]["pre_filter"]["relevance_confidence"],
                                "priority": upd["priority"],
                                "ak_suggestion": upd["metadata_"]["pre_filter"].get("ak_suggestion"),
                            },
                        }
                        for upd in updates
                    ]
                    record_events_batch(db, events_data)

                    # Batch log classifier processing for analytics
                    try:
                        from services.processing_logger import ProcessingLogger

                        plogger = ProcessingLogger(db)
                        await plogger.log_classifier_worker_batch(updates)
                    except Exception as log_err:
                        logger.warning(f"Failed to log classifier processing batch: {log_err}")

                    await db.commit()
            except Exception as e:
                logger.error(f"Failed to commit classifier updates: {e}")
                raise

        self._stats["processed"] += processed
        self._stats["priority_changed"] += priority_changed
        self._stats["last_processed_at"] = datetime.utcnow().isoformat()

        if processed > 0:
            logger.info(f"Classified {processed} items ({priority_changed} priority changes)")

        return processed

    def _determine_priority(
        self, confidence: float
    ) -> tuple[Priority, int, bool]:
        """
        Determine priority based on classifier confidence.

        Args:
            confidence: Relevance confidence from classifier (0-1)

        Returns:
            Tuple of (priority, score, skip_llm)
        """
        if confidence >= CONFIDENCE_HIGH:
            # Likely relevant - high priority, let LLM confirm
            return Priority.MEDIUM, 70, False
        elif confidence >= CONFIDENCE_EDGE:
            # Edge case - low priority, let LLM decide
            return Priority.LOW, 55, False
        else:
            # Certainly irrelevant - none priority, skip LLM
            return Priority.NONE, 20, True

    async def _process_unchecked_duplicates(self) -> int:
        """
        Re-check duplicates for items that were ingested without the check.

        This catches items that were saved while the classifier API was down.
        Only checks items from the last 7 days to limit scope.

        Returns:
            Number of items checked
        """
        from datetime import timedelta

        try:
            classifier = await self._get_classifier()
            if not classifier:
                logger.debug("Classifier unavailable for duplicate check")
                return 0
        except Exception as e:
            logger.debug(f"Cannot create classifier for duplicate check: {e}")
            return 0

        # Find items without similar_to_id and without duplicate_checked flag
        # Limit to recent items by default (configurable via DUPLICATE_CHECK_DAYS, 0 = no limit)
        import os
        check_days = int(os.environ.get("DUPLICATE_CHECK_DAYS", "7"))

        async with async_session_maker() as db:
            from database import json_extract_path

            conditions = [
                Item.similar_to_id.is_(None),
                json_extract_path(Item.metadata_, "duplicate_checked").is_(None),
            ]

            if check_days > 0:
                cutoff = datetime.utcnow() - timedelta(days=check_days)
                conditions.append(Item.fetched_at >= cutoff)

            query = (
                select(Item)
                .where(*conditions)
                .order_by(Item.fetched_at.desc())
                .limit(self.batch_size)
            )

            result = await db.execute(query)
            items = result.scalars().all()

            if not items:
                return 0

            # Extract data needed for duplicate check
            items_to_check = []
            for item in items:
                items_to_check.append({
                    "id": item.id,
                    "title": item.title,
                    "content": item.content or "",
                    "old_metadata": dict(item.metadata_) if item.metadata_ else {},
                })

        logger.info(f"Checking {len(items_to_check)} items for duplicates")

        # Check each item for duplicates
        checked = 0
        duplicates_found = 0
        updates = []

        for item_data in items_to_check:
            if self._paused or not self._running:
                break

            try:
                # Strip boilerplate before duplicate check to avoid false positives
                clean_title = _strip_boilerplate(item_data["title"])
                clean_content = _strip_boilerplate(item_data["content"])

                # Find potential duplicates
                duplicates = await classifier.find_duplicates(
                    title=clean_title,
                    content=clean_content,
                    threshold=0.75,
                )

                # Prepare updated metadata
                new_metadata = dict(item_data["old_metadata"])
                new_metadata["duplicate_checked"] = True
                new_metadata["duplicate_checked_at"] = datetime.utcnow().isoformat()

                similar_to_id = None
                if duplicates:
                    # Find the best match that isn't the item itself and won't create circular reference
                    for dup in duplicates:
                        dup_id = int(dup["id"])
                        if dup_id != item_data["id"]:
                            # Only link to older items (lower ID) to prevent circular references
                            # This ensures the oldest article in a cluster is always the primary
                            if dup_id < item_data["id"]:
                                similar_to_id = dup_id
                                new_metadata["duplicate_score"] = dup.get("score")
                                duplicates_found += 1
                                logger.info(
                                    f"Duplicate found: '{item_data['title'][:40]}...' "
                                    f"similar to item {similar_to_id} (score: {dup.get('score', 0):.3f})"
                                )
                                break
                            else:
                                logger.debug(
                                    f"Skipping newer duplicate {dup_id} for item {item_data['id']} "
                                    f"(would create circular reference)"
                                )

                updates.append({
                    "id": item_data["id"],
                    "similar_to_id": similar_to_id,
                    "metadata_": new_metadata,
                })
                checked += 1

            except Exception as e:
                logger.warning(f"Failed to check duplicates for item {item_data['id']}: {e}")
                self._stats["errors"] += 1

        # Apply updates to database
        if updates:
            try:
                async with async_session_maker() as db:
                    # Verify that similar_to_ids exist in database (ChromaDB may have stale entries)
                    similar_ids_to_check = [
                        upd["similar_to_id"] for upd in updates
                        if upd["similar_to_id"] is not None
                    ]
                    if similar_ids_to_check:
                        result = await db.execute(
                            select(Item.id).where(Item.id.in_(similar_ids_to_check))
                        )
                        existing_ids = set(row[0] for row in result.fetchall())

                        # Clear similar_to_id for non-existent items
                        for upd in updates:
                            if upd["similar_to_id"] is not None and upd["similar_to_id"] not in existing_ids:
                                logger.warning(
                                    f"Skipping similar_to_id={upd['similar_to_id']} for item {upd['id']} - "
                                    f"referenced item no longer exists (stale ChromaDB entry)"
                                )
                                upd["similar_to_id"] = None
                                # Remove duplicate_score from metadata since no valid duplicate
                                if "duplicate_score" in upd["metadata_"]:
                                    del upd["metadata_"]["duplicate_score"]

                    for upd in updates:
                        await db.execute(
                            update(Item)
                            .where(Item.id == upd["id"])
                            .values(
                                similar_to_id=upd["similar_to_id"],
                                metadata_=upd["metadata_"],
                            )
                        )
                    await db.commit()
            except Exception as e:
                logger.error(f"Failed to commit duplicate check updates: {e}")
                raise

        self._stats["duplicates_checked"] += checked
        self._stats["duplicates_found"] += duplicates_found

        if checked > 0:
            logger.info(f"Checked {checked} items for duplicates ({duplicates_found} found)")

        return checked

    async def _process_unindexed_items(self) -> int:
        """
        Re-index items that weren't added to the vector store during ingestion.

        This catches items that were saved while the classifier API was down.

        Returns:
            Number of items indexed
        """
        try:
            classifier = await self._get_classifier()
            if not classifier:
                logger.debug("Classifier unavailable for indexing")
                return 0
        except Exception as e:
            logger.debug(f"Cannot create classifier for indexing: {e}")
            return 0

        # Find items without vectordb_indexed flag
        async with async_session_maker() as db:
            from database import json_extract_path
            from sqlalchemy.orm import selectinload

            query = (
                select(Item)
                .where(
                    json_extract_path(Item.metadata_, "vectordb_indexed").is_(None),
                )
                .options(selectinload(Item.channel).selectinload(Channel.source))
                .order_by(Item.fetched_at.desc())
                .limit(self.batch_size)
            )

            result = await db.execute(query)
            items = result.scalars().all()

            if not items:
                return 0

            # Prepare items for indexing (strip boilerplate for better embeddings)
            items_to_index = []
            item_ids = []
            for item in items:
                source_name = ""
                if item.channel and item.channel.source:
                    source_name = item.channel.source.name
                items_to_index.append({
                    "id": str(item.id),
                    "title": _strip_boilerplate(item.title),
                    "content": _strip_boilerplate(item.content or ""),
                    "metadata": {
                        "source": source_name,
                        "priority": item.priority.value if hasattr(item.priority, 'value') else str(item.priority),
                        "channel_id": str(item.channel_id) if item.channel_id else "",
                    },
                })
                item_ids.append(item.id)

        logger.info(f"Indexing {len(items_to_index)} items in vector store")

        # Index items
        try:
            indexed = await classifier.index_items_batch(items_to_index)
        except Exception as e:
            logger.warning(f"Failed to index items: {e}")
            self._stats["errors"] += 1
            return 0

        # Update metadata for all items (even if indexed=0, the API succeeded)
        if item_ids:
            try:
                async with async_session_maker() as db:
                    for item_id in item_ids:
                        # Get current metadata
                        result = await db.execute(
                            select(Item.metadata_).where(Item.id == item_id)
                        )
                        current_meta = result.scalar() or {}
                        new_meta = dict(current_meta)
                        new_meta["vectordb_indexed"] = True
                        new_meta["vectordb_indexed_at"] = datetime.utcnow().isoformat()

                        await db.execute(
                            update(Item)
                            .where(Item.id == item_id)
                            .values(metadata_=new_meta)
                        )
                    await db.commit()
            except Exception as e:
                logger.error(f"Failed to update vectordb_indexed flags: {e}")
                raise

        self._stats["vectordb_indexed"] += len(item_ids)

        if len(item_ids) > 0:
            logger.info(f"Indexed {len(item_ids)} items in vector store")

        return len(item_ids)


# Global worker instance
_worker: Optional[ClassifierWorker] = None


def get_classifier_worker() -> Optional[ClassifierWorker]:
    """Get the global classifier worker instance."""
    return _worker


async def start_classifier_worker(
    batch_size: int = 50,
    idle_sleep: float = 60.0,
) -> ClassifierWorker:
    """
    Start the global classifier worker.

    Args:
        batch_size: Items to process per batch
        idle_sleep: Seconds to sleep when idle

    Returns:
        The started worker instance
    """
    global _worker

    if _worker is not None:
        logger.warning("Classifier worker already exists, stopping old instance")
        await _worker.stop()

    _worker = ClassifierWorker(
        batch_size=batch_size,
        idle_sleep=idle_sleep,
    )
    await _worker.start()
    return _worker


async def stop_classifier_worker():
    """Stop the global classifier worker."""
    global _worker

    if _worker is not None:
        await _worker.stop()
        _worker = None


async def get_unclassified_count() -> int:
    """Get count of items without classifier results."""
    from database import json_extract_path
    async with async_session_maker() as db:
        result = await db.execute(
            select(func.count(Item.id)).where(
                json_extract_path(Item.metadata_, "pre_filter").is_(None)
            )
        )
        return result.scalar() or 0


async def get_unchecked_duplicates_count() -> int:
    """Get count of items that haven't been checked for duplicates."""
    import os
    from datetime import timedelta
    from database import json_extract_path

    check_days = int(os.environ.get("DUPLICATE_CHECK_DAYS", "7"))

    conditions = [
        Item.similar_to_id.is_(None),
        json_extract_path(Item.metadata_, "duplicate_checked").is_(None),
    ]

    if check_days > 0:
        cutoff = datetime.utcnow() - timedelta(days=check_days)
        conditions.append(Item.fetched_at >= cutoff)

    async with async_session_maker() as db:
        result = await db.execute(
            select(func.count(Item.id)).where(*conditions)
        )
        return result.scalar() or 0
