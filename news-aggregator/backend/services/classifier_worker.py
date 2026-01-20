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

                # Process unclassified items
                processed = await self._process_unclassified_items()
                if processed > 0:
                    # More items might be available
                    await asyncio.sleep(0.5)
                    continue

                # No work available, sleep
                logger.debug(f"No unclassified items, sleeping {self.idle_sleep}s")
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
            from services.item_events import record_event, EVENT_CLASSIFIER_PROCESSED

            try:
                async with async_session_maker() as db:
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
                        # Record classifier event
                        await record_event(
                            db,
                            upd["id"],
                            EVENT_CLASSIFIER_PROCESSED,
                            data={
                                "confidence": upd["metadata_"]["pre_filter"]["relevance_confidence"],
                                "priority": upd["priority"],
                                "ak_suggestion": upd["metadata_"]["pre_filter"].get("ak_suggestion"),
                            },
                        )

                        # Log classifier processing for analytics
                        try:
                            from services.processing_logger import ProcessingLogger

                            plogger = ProcessingLogger(db)
                            await plogger.log_classifier_worker(
                                item_id=upd["id"],
                                result=upd["metadata_"]["pre_filter"],
                                priority_input=upd.get("old_priority", "unknown"),
                                priority_output=upd["priority"],
                            )
                        except Exception as log_err:
                            logger.warning(f"Failed to log classifier processing for item {upd['id']}: {log_err}")

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
