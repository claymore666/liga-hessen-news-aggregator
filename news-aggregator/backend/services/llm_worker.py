"""
LLM Processing Worker with Priority Queue.

Continuously processes items through the LLM with priority:
1. Fresh items (from fetch) - immediate processing
2. Backlog items (needs_llm_processing=True) - continuous when idle

Fresh items always take priority over backlog processing.
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import case, func, select
from sqlalchemy.orm import selectinload

from database import async_session_maker
from models import Channel, Item, Priority
from sqlalchemy import and_, or_

logger = logging.getLogger(__name__)


class LLMWorker:
    """
    Priority queue worker for LLM processing.

    Processes items continuously with priority ordering:
    1. Fresh items (just fetched, in memory queue)
    2. Backlog items (from database, ordered by retry_priority)
    """

    def __init__(
        self,
        batch_size: int = 10,
        idle_sleep: float = 30.0,
        backlog_batch_size: int = 50,
    ):
        """
        Initialize the LLM worker.

        Args:
            batch_size: Items to process per batch from fresh queue
            idle_sleep: Seconds to sleep when no work available
            backlog_batch_size: Items to fetch from backlog per query
        """
        self.batch_size = batch_size
        self.idle_sleep = idle_sleep
        self.backlog_batch_size = backlog_batch_size

        # Fresh items queue (in-memory, highest priority)
        self._fresh_queue: asyncio.Queue[int] = asyncio.Queue()

        # Worker state
        self._running = False
        self._paused = False
        self._task: Optional[asyncio.Task] = None
        self._processor = None

        # Statistics
        self._stats = {
            "fresh_processed": 0,
            "backlog_processed": 0,
            "errors": 0,
            "started_at": None,
            "last_processed_at": None,
            "total_processing_time": 0.0,  # Total seconds spent processing
            "items_timed": 0,  # Number of items with timing data
        }

    async def start(self):
        """Start the worker background task."""
        if self._running:
            logger.warning("LLM worker already running")
            return

        self._running = True
        self._stats["started_at"] = datetime.utcnow().isoformat()
        self._task = asyncio.create_task(self._run())
        logger.info("LLM worker started")

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
        logger.info("LLM worker stopped")

    def pause(self):
        """Pause processing (items still queued)."""
        self._paused = True
        logger.info("LLM worker paused")

    def resume(self):
        """Resume processing."""
        self._paused = False
        logger.info("LLM worker resumed")

    async def enqueue_fresh(self, item_id: int):
        """
        Enqueue a fresh item for immediate processing.

        Args:
            item_id: Database ID of the item to process
        """
        await self._fresh_queue.put(item_id)
        logger.debug(f"Enqueued fresh item {item_id} (queue size: {self._fresh_queue.qsize()})")

    def get_status(self) -> dict:
        """Get worker status and statistics."""
        return {
            "running": self._running,
            "paused": self._paused,
            "fresh_queue_size": self._fresh_queue.qsize(),
            "stats": self._stats.copy(),
        }

    async def _get_processor(self):
        """Get or create the LLM processor, waking gpu1 if needed."""
        if self._processor is None:
            from services.gpu1_power import get_power_manager
            from services.processor import create_processor_from_settings

            # Check if gpu1 needs waking
            power_mgr = get_power_manager()
            if power_mgr is not None:
                if not await power_mgr.is_available():
                    logger.info("gpu1 not available, attempting Wake-on-LAN...")
                    if await power_mgr.ensure_available():
                        logger.info("gpu1 woken and ready for LLM processing")
                    else:
                        logger.warning("Failed to wake gpu1, LLM processing unavailable")
                        return None

            self._processor = await create_processor_from_settings()
        return self._processor

    async def _run(self):
        """Main worker loop."""
        logger.info("LLM worker loop started")

        while self._running:
            try:
                # Check if paused
                if self._paused:
                    await asyncio.sleep(1.0)
                    continue

                # Priority 1: Process fresh items
                fresh_processed = await self._process_fresh_items()
                if fresh_processed > 0:
                    self._record_gpu1_activity()
                    continue  # Check for more fresh items immediately

                # Priority 2: Process backlog items
                backlog_processed = await self._process_backlog_items()
                if backlog_processed > 0:
                    self._record_gpu1_activity()
                    # Check for fresh items before continuing backlog
                    continue

                # No work available - check if we should shutdown gpu1
                await self._check_gpu1_idle_shutdown()

                # Sleep before next check
                logger.debug(f"No items to process, sleeping {self.idle_sleep}s")
                await asyncio.sleep(self.idle_sleep)

            except asyncio.CancelledError:
                logger.info("LLM worker cancelled")
                break
            except Exception as e:
                logger.error(f"LLM worker error: {e}", exc_info=True)
                self._stats["errors"] += 1
                await asyncio.sleep(5.0)  # Back off on error

        logger.info("LLM worker loop ended")

    def _record_gpu1_activity(self):
        """Record LLM processing activity for gpu1 idle tracking."""
        from services.gpu1_power import get_power_manager

        power_mgr = get_power_manager()
        if power_mgr is not None:
            power_mgr.record_activity()

    async def _check_gpu1_idle_shutdown(self):
        """Check if gpu1 should be shutdown due to idle timeout."""
        from services.gpu1_power import get_power_manager

        power_mgr = get_power_manager()
        if power_mgr is None:
            return

        if await power_mgr.shutdown_if_idle():
            # Clear processor so we wake gpu1 again next time
            self._processor = None
            logger.info("gpu1 shutdown due to idle timeout, processor cleared")

    async def _process_fresh_items(self) -> int:
        """
        Process items from the fresh queue.

        Returns:
            Number of items processed
        """
        processed = 0
        item_ids = []

        # Drain up to batch_size items from queue (non-blocking)
        while len(item_ids) < self.batch_size:
            try:
                item_id = self._fresh_queue.get_nowait()
                item_ids.append(item_id)
            except asyncio.QueueEmpty:
                break

        if not item_ids:
            return 0

        logger.info(f"Processing {len(item_ids)} fresh items")

        try:
            processor = await self._get_processor()
            if not processor:
                # Re-enqueue items if processor unavailable
                for item_id in item_ids:
                    await self._fresh_queue.put(item_id)
                logger.warning("LLM processor unavailable, re-enqueued fresh items")
                return 0

            processed = await self._process_items(item_ids, processor, is_fresh=True)
            self._stats["fresh_processed"] += processed

        except Exception as e:
            logger.error(f"Error processing fresh items: {e}")
            self._stats["errors"] += 1

        return processed

    async def _process_backlog_items(self) -> int:
        """
        Process items from the backlog (needs_llm_processing=True).

        Returns:
            Number of items processed
        """
        try:
            processor = await self._get_processor()
            if not processor:
                logger.debug("LLM processor unavailable for backlog")
                return 0
        except Exception as e:
            logger.debug(f"Cannot create processor for backlog: {e}")
            return 0

        # Query backlog items ordered by priority
        # Only process items that have been classified (have pre_filter metadata)
        # This ensures classifier runs before LLM, avoiding wasted compute on irrelevant items
        async with async_session_maker() as db:
            from database import json_extract_path
            retry_priority = json_extract_path(Item.metadata_, "retry_priority")
            pre_filter = json_extract_path(Item.metadata_, "pre_filter")
            assigned_aks = json_extract_path(Item.metadata_, "llm_analysis", "assigned_aks")
            priority_order = case(
                (retry_priority == "high", 1),
                (retry_priority == "edge_case", 2),
                (retry_priority == "low", 3),
                else_=4,
            )

            query = (
                select(Item.id)
                .where(
                    # Must be classified first (pre_filter exists)
                    pre_filter.is_not(None),
                    or_(
                        # Standard backlog: needs processing and not certainly irrelevant
                        and_(
                            Item.needs_llm_processing == True,  # noqa: E712
                            or_(retry_priority != "low", retry_priority.is_(None)),
                        ),
                        # Relevant items without AK assigned need reprocessing
                        and_(
                            Item.priority != "none",
                            or_(assigned_aks.is_(None), assigned_aks == "[]"),
                        ),
                    )
                )
                .order_by(priority_order, Item.fetched_at.desc())
                .limit(self.backlog_batch_size)
            )

            result = await db.execute(query)
            item_ids = [row[0] for row in result.fetchall()]

        if not item_ids:
            return 0

        logger.info(f"Processing {len(item_ids)} backlog items")

        try:
            processed = await self._process_items(item_ids, processor, is_fresh=False)
            self._stats["backlog_processed"] += processed
        except Exception as e:
            logger.error(f"Error processing backlog items: {e}")
            self._stats["errors"] += 1
            return 0

        return processed

    async def _process_items(
        self,
        item_ids: list[int],
        processor,
        is_fresh: bool,
    ) -> int:
        """
        Process a batch of items through the LLM.

        Args:
            item_ids: List of item database IDs
            processor: ItemProcessor instance
            is_fresh: Whether these are fresh items (for logging)

        Returns:
            Number of items successfully processed
        """
        processed = 0
        item_type = "fresh" if is_fresh else "backlog"

        async with async_session_maker() as db:
            for item_id in item_ids:
                # Check for fresh items interrupting backlog
                if not is_fresh and not self._fresh_queue.empty():
                    logger.info(f"Fresh items arrived, pausing backlog after {processed} items")
                    break

                # Check if paused
                if self._paused:
                    logger.info(f"Worker paused, stopping after {processed} items")
                    break

                try:
                    # Load item with relationships
                    result = await db.execute(
                        select(Item)
                        .where(Item.id == item_id)
                        .options(selectinload(Item.channel).selectinload(Channel.source))
                    )
                    item = result.scalar_one_or_none()

                    if not item:
                        logger.warning(f"Item {item_id} not found")
                        continue

                    # Skip if already processed (race condition)
                    if not is_fresh and not item.needs_llm_processing:
                        continue

                    # Run LLM analysis with timing
                    import time
                    start_time = time.time()
                    source_name = item.channel.source.name if item.channel and item.channel.source else "Unbekannt"
                    analysis = await processor.analyze(item, source_name=source_name)
                    elapsed = time.time() - start_time
                    self._stats["total_processing_time"] += elapsed
                    self._stats["items_timed"] += 1

                    # Update item with results
                    if analysis.get("summary"):
                        item.summary = analysis["summary"]
                    if analysis.get("detailed_analysis"):
                        item.detailed_analysis = analysis["detailed_analysis"]

                    # Use LLM priority directly (no remapping)
                    llm_priority = analysis.get("priority") or analysis.get("priority_suggestion")
                    if analysis.get("relevant") is False:
                        llm_priority = None

                    if llm_priority == "high":
                        item.priority = Priority.HIGH
                        item.priority_score = max(item.priority_score or 0, 90)
                    elif llm_priority == "medium":
                        item.priority = Priority.MEDIUM
                        item.priority_score = max(item.priority_score or 0, 70)
                    elif llm_priority == "low":
                        item.priority = Priority.LOW
                        item.priority_score = max(item.priority_score or 0, 40)
                    else:
                        item.priority = Priority.NONE
                        item.priority_score = min(item.priority_score or 100, 20)

                    # Set assigned_aks: LLM takes precedence, classifier AK as fallback
                    llm_aks = analysis.get("assigned_aks", [])
                    if llm_aks:
                        item.assigned_aks = llm_aks
                        item.assigned_ak = llm_aks[0] if llm_aks else None  # Deprecated field
                    elif not item.assigned_aks:
                        # Use classifier AK suggestion as fallback
                        pre_filter = (item.metadata_ or {}).get("pre_filter", {})
                        classifier_ak = pre_filter.get("ak_suggestion")
                        if classifier_ak:
                            item.assigned_aks = [classifier_ak]
                            item.assigned_ak = classifier_ak  # Deprecated field
                            logger.debug(f"Using classifier AK: {classifier_ak}")

                    # Store LLM analysis in metadata
                    new_metadata = dict(item.metadata_) if item.metadata_ else {}
                    new_metadata["llm_analysis"] = {
                        "relevance_score": analysis.get("relevance_score", 0.5),
                        "priority_suggestion": llm_priority,
                        "assigned_aks": llm_aks,
                        "assigned_ak": llm_aks[0] if llm_aks else None,  # Deprecated, for backward compat
                        "tags": analysis.get("tags", []),
                        "reasoning": analysis.get("reasoning"),
                        "processed_at": datetime.utcnow().isoformat(),
                        "source": "llm_worker",
                    }
                    item.metadata_ = new_metadata

                    # Clear retry flag
                    item.needs_llm_processing = False

                    # Commit after each item so count updates in real-time
                    await db.commit()

                    # Record LLM processing event
                    from services.item_events import record_event, EVENT_LLM_PROCESSED

                    await record_event(
                        db,
                        item.id,
                        EVENT_LLM_PROCESSED,
                        data={
                            "priority": llm_priority,
                            "assigned_aks": llm_aks,
                            "relevance_score": analysis.get("relevance_score"),
                            "source": item_type,
                        },
                    )

                    # Log LLM analysis for analytics
                    try:
                        from services.processing_logger import ProcessingLogger

                        plogger = ProcessingLogger(db)
                        # Get priority before LLM (from pre_filter or previous value)
                        pre_filter = (item.metadata_ or {}).get("pre_filter", {})
                        priority_input = pre_filter.get("priority_suggestion") or "unknown"

                        await plogger.log_llm_analysis(
                            item_id=item.id,
                            analysis=analysis,
                            priority_input=priority_input,
                            priority_output=llm_priority,
                            duration_ms=int(elapsed * 1000),
                        )
                    except Exception as log_err:
                        logger.warning(f"Failed to log LLM analysis for item {item_id}: {log_err}")

                    processed += 1
                    self._stats["last_processed_at"] = datetime.utcnow().isoformat()

                    logger.info(f"LLM {item_type}: {item.title[:40]}... -> {llm_priority}")

                except Exception as e:
                    logger.warning(f"Failed to process {item_type} item {item_id}: {e}")
                    self._stats["errors"] += 1

        return processed


# Global worker instance
_worker: Optional[LLMWorker] = None


def get_worker() -> Optional[LLMWorker]:
    """Get the global worker instance."""
    return _worker


async def start_worker(
    batch_size: int = 10,
    idle_sleep: float = 30.0,
    backlog_batch_size: int = 50,
) -> LLMWorker:
    """
    Start the global LLM worker.

    Args:
        batch_size: Fresh items to process per batch
        idle_sleep: Seconds to sleep when idle
        backlog_batch_size: Backlog items to fetch per query

    Returns:
        The started worker instance
    """
    global _worker

    if _worker is not None:
        logger.warning("Worker already exists, stopping old instance")
        await _worker.stop()

    _worker = LLMWorker(
        batch_size=batch_size,
        idle_sleep=idle_sleep,
        backlog_batch_size=backlog_batch_size,
    )
    await _worker.start()
    return _worker


async def stop_worker():
    """Stop the global LLM worker."""
    global _worker

    if _worker is not None:
        await _worker.stop()
        _worker = None


async def enqueue_fresh_item(item_id: int):
    """
    Enqueue a fresh item for immediate LLM processing.

    Args:
        item_id: Database ID of the item
    """
    if _worker is not None:
        await _worker.enqueue_fresh(item_id)
    else:
        logger.warning(f"No worker available, cannot enqueue item {item_id}")
