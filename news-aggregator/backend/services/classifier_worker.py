"""
Classifier Background Worker.

Processes items through the embedding classifier:
1. Items without pre_filter (never classified)
2. Updates priority based on classifier confidence

This ensures items fetched during classifier downtime get properly
evaluated when the classifier comes back online.

Note: Deduplication and vector indexing duties have been moved to
the independent DedupWorker (services/dedup_worker.py).
"""

import asyncio
import logging
from datetime import datetime

import httpx
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from database import async_session_maker
from models import Channel, Item, Priority

logger = logging.getLogger(__name__)


class ServiceUnavailableError(Exception):
    """Raised when the classifier/embedding service is down (5xx)."""
    pass


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
        self._task: asyncio.Task | None = None
        self._poll_task: asyncio.Task | None = None
        self._sync_task: asyncio.Task | None = None
        self._classifier = None

        # Statistics (protected by _stats_lock for thread-safe updates)
        self._stats = {
            "processed": 0,
            "priority_changed": 0,
            "errors": 0,
            "started_at": None,
            "last_processed_at": None,
        }
        self._stats_lock = asyncio.Lock()
        self._stopped_due_to_errors = False  # Track if stopped due to max consecutive errors
        self._service_unavailable = False  # Track if embedding service is down

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
            "service_available": not self._service_unavailable,
            "stats": stats_copy,
        }

    async def _on_success(self):
        """Clear degraded state on successful processing."""
        recovered = False
        if self._stopped_due_to_errors:
            self._stopped_due_to_errors = False
            recovered = True
        if self._service_unavailable:
            self._service_unavailable = False
            recovered = True
        if recovered:
            logger.info("Classifier worker recovered — service available again")
            from services.worker_status import write_state
            await write_state(
                "classifier", running=True,
                service_available=True,
                stopped_due_to_errors=False,
            )

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
        service_unavailable_backoff = 300.0  # 5 minutes

        while self._running:
            try:
                # Check if paused
                if self._paused:
                    await asyncio.sleep(1.0)
                    continue

                # Process unclassified items
                processed = await self._process_unclassified_items()
                if processed > 0:
                    consecutive_errors = 0
                    await self._on_success()
                    # More items might be available
                    await asyncio.sleep(0.5)
                    continue

                # No work available, sleep
                logger.debug(f"No unclassified items, sleeping {self.idle_sleep}s")
                await asyncio.sleep(self.idle_sleep)

            except asyncio.CancelledError:
                logger.info("Classifier worker cancelled")
                break
            except ServiceUnavailableError as e:
                # Embedding service / classifier backend is down.
                # Back off to 5 min immediately — don't inflate error counter.
                if not self._service_unavailable:
                    self._service_unavailable = True
                    logger.warning(
                        f"Classifier service unavailable: {e}. "
                        f"Retrying every {service_unavailable_backoff:.0f}s."
                    )
                    from services.worker_status import write_state
                    await write_state(
                        "classifier", running=True,
                        service_available=False,
                        stopped_due_to_errors=True,
                    )
                else:
                    logger.debug(f"Classifier still unavailable: {e}")
                await asyncio.sleep(service_unavailable_backoff)
            except Exception as e:
                consecutive_errors += 1
                logger.error(f"Classifier worker error ({consecutive_errors}/{max_consecutive_errors}): {e}", exc_info=True)
                async with self._stats_lock:
                    self._stats["errors"] += 1

                if consecutive_errors >= max_consecutive_errors and not self._stopped_due_to_errors:
                    logger.warning(
                        f"Classifier worker in degraded state after {consecutive_errors} errors. "
                        "Will keep retrying with backoff."
                    )
                    self._stopped_due_to_errors = True
                    from services.worker_status import write_state
                    await write_state("classifier", running=True, stopped_due_to_errors=True)

                # Exponential backoff: 10s, 20s, 40s, ... capped at 300s
                backoff = min(300.0, 10.0 * (2 ** (consecutive_errors - 1)))
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
                    self._running = False
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

                # Build metadata patch (only keys this worker manages)
                # Using a patch instead of full replace avoids race conditions
                # with the LLM worker writing llm_analysis concurrently
                pre_filter_data = {
                    "relevance_confidence": confidence,
                    "ak_suggestion": result.get("ak"),
                    "ak_confidence": result.get("ak_confidence"),
                    "priority_suggestion": result.get("priority"),
                    "priority_confidence": result.get("priority_confidence"),
                    "classified_at": datetime.utcnow().isoformat(),
                }

                # Set retry priority for LLM worker
                if confidence >= CONFIDENCE_HIGH:
                    retry_priority = "high"
                elif confidence >= CONFIDENCE_EDGE:
                    retry_priority = "edge_case"
                else:
                    retry_priority = "low"

                metadata_patch = {
                    "pre_filter": pre_filter_data,
                    "retry_priority": retry_priority,
                }

                # Collect update
                updates.append({
                    "id": item_data["id"],
                    "old_priority": old_priority,
                    "priority": new_priority.value,
                    "priority_score": new_score,
                    "metadata_patch": metadata_patch,
                    "needs_llm_processing": not skip_llm,
                })

                processed += 1
                if old_priority != new_priority.value:
                    priority_changed += 1
                    logger.info(
                        f"Classified: {item_data['title'][:40]}... "
                        f"conf={confidence:.2f} {old_priority}->{new_priority.value}"
                    )

            except httpx.HTTPStatusError as e:
                if e.response.status_code >= 500:
                    # Service-level failure (e.g. embedding backend down)
                    # Abort batch early — no point retrying remaining items
                    raise ServiceUnavailableError(
                        f"Classifier returned {e.response.status_code}"
                    ) from e
                logger.warning(f"Failed to classify item {item_data['id']}: {e}")
                async with self._stats_lock:
                    self._stats["errors"] += 1
            except httpx.RequestError as e:
                # Connection refused, timeout, DNS failure, etc.
                raise ServiceUnavailableError(
                    f"Classifier unreachable: {e}"
                ) from e
            except Exception as e:
                logger.warning(f"Failed to classify item {item_data['id']}: {e}")
                async with self._stats_lock:
                    self._stats["errors"] += 1

        # Phase 3: Apply updates to database
        # Note: No global lock needed - PostgreSQL MVCC handles concurrent writes
        if updates:
            from services.item_events import record_events_batch, EVENT_CLASSIFIER_PROCESSED

            try:
                from database import json_merge

                async with async_session_maker() as db:
                    # Batch update items (individual updates required due to different metadata per item)
                    # Use json_merge to atomically merge classifier keys without overwriting
                    # concurrent LLM worker writes (e.g. llm_analysis)
                    for upd in updates:
                        await db.execute(
                            update(Item)
                            .where(Item.id == upd["id"])
                            .values(
                                priority=upd["priority"],
                                priority_score=upd["priority_score"],
                                metadata_=json_merge(Item.metadata_, upd["metadata_patch"]),
                                needs_llm_processing=upd["needs_llm_processing"],
                            )
                        )

                    # Batch record classifier events (more efficient than individual calls)
                    events_data = [
                        {
                            "item_id": upd["id"],
                            "event_type": EVENT_CLASSIFIER_PROCESSED,
                            "data": {
                                "confidence": upd["metadata_patch"]["pre_filter"]["relevance_confidence"],
                                "priority": upd["priority"],
                                "ak_suggestion": upd["metadata_patch"]["pre_filter"].get("ak_suggestion"),
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


# Global worker instance
_worker: ClassifierWorker | None = None


def get_classifier_worker() -> ClassifierWorker | None:
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
    from sqlalchemy import func
    from database import json_extract_path
    async with async_session_maker() as db:
        result = await db.execute(
            select(func.count(Item.id)).where(
                json_extract_path(Item.metadata_, "pre_filter").is_(None)
            )
        )
        return result.scalar() or 0
