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
        self._poll_task: Optional[asyncio.Task] = None
        self._sync_task: Optional[asyncio.Task] = None
        self._classifier = None

        # Statistics (protected by _stats_lock for thread-safe updates)
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
        self._stats_lock = asyncio.Lock()
        self._stopped_due_to_errors = False  # Track if stopped due to max consecutive errors

    async def start(self):
        """Start the worker background task."""
        if self._running:
            logger.warning("Classifier worker already running")
            return

        self._running = True
        self._stats["started_at"] = datetime.utcnow().isoformat()
        self._stopped_due_to_errors = False  # Reset on start
        self._task = asyncio.create_task(self._run())
        self._poll_task = asyncio.create_task(self._poll_commands())
        self._sync_task = asyncio.create_task(self._sync_stats())

        from services.worker_status import write_state
        await write_state("classifier", running=True)
        logger.info("Classifier worker started")

    async def stop(self):
        """Stop the worker gracefully."""
        if not self._running:
            return

        self._running = False
        for task in (self._task, self._poll_task, self._sync_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Close HTTP client in classifier to prevent resource leak
        if self._classifier:
            await self._classifier.close()
            self._classifier = None

        from services.worker_status import write_state, write_stats
        await write_state("classifier", running=False)
        async with self._stats_lock:
            await write_stats("classifier", self._stats.copy())
        logger.info("Classifier worker stopped")

    async def pause(self):
        """Pause processing."""
        self._paused = True
        from services.worker_status import write_state
        await write_state("classifier", running=True, paused=True)
        logger.info("Classifier worker paused")

    async def resume(self):
        """Resume processing."""
        self._paused = False
        from services.worker_status import write_state
        await write_state("classifier", running=True, paused=False)
        logger.info("Classifier worker resumed")

    async def get_status(self) -> dict:
        """Get worker status and statistics."""
        async with self._stats_lock:
            stats_copy = self._stats.copy()
        return {
            "running": self._running,
            "paused": self._paused,
            "stopped_due_to_errors": self._stopped_due_to_errors,
            "stats": stats_copy,
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

        consecutive_errors = 0
        max_consecutive_errors = 10
        last_sync_check_date = None

        while self._running:
            try:
                # Check if paused
                if self._paused:
                    await asyncio.sleep(1.0)
                    continue

                # Priority 1: Process unclassified items
                processed = await self._process_unclassified_items()
                if processed > 0:
                    consecutive_errors = 0  # Reset on success
                    # More items might be available
                    await asyncio.sleep(0.5)
                    continue

                # Priority 2: Index items in vector store (before duplicate check
                # so that items from the same batch are findable as duplicates)
                indexed = await self._process_unindexed_items()
                if indexed > 0:
                    consecutive_errors = 0  # Reset on success
                    await asyncio.sleep(0.5)
                    continue

                # Priority 3: Re-check duplicates for items that missed the check
                duplicates_checked = await self._process_unchecked_duplicates()
                if duplicates_checked > 0:
                    consecutive_errors = 0  # Reset on success
                    await asyncio.sleep(0.5)
                    continue

                # Daily sync check: verify DB and ChromaDB are in sync
                today = datetime.utcnow().date()
                if last_sync_check_date != today and datetime.utcnow().hour >= 0:
                    last_sync_check_date = today
                    await self._check_vectordb_sync()

                # No work available, sleep
                logger.debug(f"No unclassified items, unindexed items, or unchecked duplicates, sleeping {self.idle_sleep}s")
                await asyncio.sleep(self.idle_sleep)

            except asyncio.CancelledError:
                logger.info("Classifier worker cancelled")
                break
            except Exception as e:
                consecutive_errors += 1
                logger.error(f"Classifier worker error ({consecutive_errors}/{max_consecutive_errors}): {e}", exc_info=True)
                async with self._stats_lock:
                    self._stats["errors"] += 1

                if consecutive_errors >= max_consecutive_errors:
                    logger.critical(
                        f"Classifier worker exceeded {max_consecutive_errors} consecutive errors, stopping. "
                        "Manual restart required after fixing the issue."
                    )
                    self._stopped_due_to_errors = True
                    self._running = False
                    from services.worker_status import write_state
                    await write_state("classifier", running=False, stopped_due_to_errors=True)
                    break

                # Exponential backoff: 10s, 20s, 40s, ... up to 120s
                backoff = min(120.0, 10.0 * (2 ** (consecutive_errors - 1)))
                logger.info(f"Backing off for {backoff:.0f}s before retry")
                await asyncio.sleep(backoff)

        logger.info("Classifier worker loop ended")

    async def _poll_commands(self):
        """Poll DB for commands (pause/resume/stop) from API workers."""
        from services.worker_status import read_and_clear_command, get_poll_interval

        while self._running:
            try:
                interval = await get_poll_interval()
                await asyncio.sleep(interval)
                action = await read_and_clear_command("classifier")
                if action == "pause":
                    await self.pause()
                elif action == "resume":
                    await self.resume()
                elif action == "stop":
                    await self.stop()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Classifier command poll error: {e}")
                await asyncio.sleep(10)

    async def _sync_stats(self):
        """Periodically sync stats to DB for API workers to read."""
        from services.worker_status import write_stats, get_poll_interval

        while self._running:
            try:
                interval = await get_poll_interval()
                await asyncio.sleep(interval)
                async with self._stats_lock:
                    stats = self._stats.copy()
                await write_stats("classifier", stats)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Classifier stats sync error: {e}")
                await asyncio.sleep(10)

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
                async with self._stats_lock:
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

        async with self._stats_lock:
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
                    "url": item.url,
                    "channel_id": item.channel_id,
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
                # Prepare updated metadata
                new_metadata = dict(item_data["old_metadata"])
                new_metadata["duplicate_checked"] = True
                new_metadata["duplicate_checked_at"] = datetime.utcnow().isoformat()

                similar_to_id = None
                duplicates = []

                # 1. URL-based duplicate check (exact same article from different channel)
                if item_data.get("url"):
                    async with async_session_maker() as url_db:
                        url_match = await url_db.scalar(
                            select(Item.id).where(
                                Item.url == item_data["url"],
                                Item.id < item_data["id"],
                                Item.channel_id != item_data["channel_id"],
                            ).order_by(Item.id).limit(1)
                        )
                        if url_match:
                            similar_to_id = url_match
                            new_metadata["duplicate_method"] = "url_match"
                            duplicates_found += 1
                            logger.info(
                                f"URL duplicate: '{item_data['title'][:40]}...' "
                                f"same URL as item {similar_to_id}"
                            )

                # 2. Semantic duplicate check (same topic, different article)
                if not similar_to_id:
                    # Strip boilerplate before duplicate check to avoid false positives
                    clean_title = _strip_boilerplate(item_data["title"])
                    clean_content = _strip_boilerplate(item_data["content"])

                    # Find potential duplicates
                    duplicates = await classifier.find_duplicates(
                        title=clean_title,
                        content=clean_content,
                        threshold=0.75,
                    )

                if not similar_to_id and duplicates:
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
                async with self._stats_lock:
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

        async with self._stats_lock:
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
            async with self._stats_lock:
                self._stats["errors"] += 1
            return 0

        # Mark items as indexed — the API call succeeded, items are either
        # newly added or already existed in ChromaDB (both are valid states)
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

        async with self._stats_lock:
            self._stats["vectordb_indexed"] += len(item_ids)

        if len(item_ids) > 0:
            logger.info(f"Indexed {len(item_ids)} items in vector store")

        return len(item_ids)

    async def _check_vectordb_sync(self) -> None:
        """Daily check: compare DB indexed count with ChromaDB item count.

        Logs an error if the counts are significantly out of sync,
        and resets vectordb_indexed flags for items missing from ChromaDB.
        """
        try:
            classifier = await self._get_classifier()
            if not classifier:
                return

            # Get ChromaDB item count from health endpoint
            import httpx
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{classifier.base_url}/health")
                health = resp.json()

            chromadb_count = health.get("duplicate_index_items", 0)

            # Get DB indexed count
            async with async_session_maker() as db:
                from database import json_extract_path
                db_count = await db.scalar(
                    select(func.count()).select_from(Item).where(
                        json_extract_path(Item.metadata_, "vectordb_indexed").isnot(None),
                    )
                )

            diff = (db_count or 0) - chromadb_count
            if abs(diff) > 50:
                logger.error(
                    f"VECTORDB SYNC CHECK: DB says {db_count} items indexed, "
                    f"ChromaDB has {chromadb_count} items. "
                    f"Difference: {diff} items. "
                    f"Run /sync-duplicate-store or reset vectordb_indexed flags."
                )
            elif diff > 0:
                logger.warning(
                    f"VectorDB sync: {diff} items in DB but not in ChromaDB "
                    f"(DB: {db_count}, ChromaDB: {chromadb_count})"
                )
            else:
                logger.info(
                    f"VectorDB sync check OK: DB={db_count}, ChromaDB={chromadb_count}"
                )
        except Exception as e:
            logger.warning(f"VectorDB sync check failed: {e}")


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
