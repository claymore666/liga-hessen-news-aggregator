"""
Deduplication Background Worker.

Independent worker that handles all deduplication duties:
1. Index items in ChromaDB vector store (prerequisite for semantic dedup)
2. Phase 1: URL match, content hash, title similarity (no GPU needed)
3. Phase 2: Semantic similarity via ChromaDB (requires classifier API)
4. Daily ChromaDB<->DB consistency check

Serial ID-ascending processing eliminates race conditions from
concurrent channel fetches.
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import selectinload

from database import async_session_maker, json_extract_path, json_merge, utcnow
from models import Channel, Item, Priority
from services.pipeline import _strip_boilerplate, _titles_similar, _normalize_url

logger = logging.getLogger(__name__)


class DedupWorker:
    """
    Background worker for deduplication processing.

    Two-phase approach:
    - Phase 1 (always runs, no GPU): URL match, content hash, title similarity
    - Phase 2 (when classifier available): semantic similarity via ChromaDB
    """

    def __init__(
        self,
        batch_size: int = 50,
        idle_sleep: float = 30.0,
    ):
        self.batch_size = batch_size
        self.idle_sleep = idle_sleep

        # Worker state
        self._running = False
        self._paused = False
        self._task: asyncio.Task | None = None
        self._poll_task: asyncio.Task | None = None
        self._sync_task: asyncio.Task | None = None
        self._classifier = None

        # Statistics
        self._stats = {
            "phase1_checked": 0,
            "phase2_checked": 0,
            "duplicates_found": 0,
            "vectordb_indexed": 0,
            "errors": 0,
            "started_at": None,
            "last_processed_at": None,
        }
        self._stats_lock = asyncio.Lock()
        self._stopped_due_to_errors = False

    async def start(self):
        """Start the worker background task."""
        if self._running:
            logger.warning("Dedup worker already running")
            return

        self._running = True
        self._stats["started_at"] = utcnow().isoformat()
        self._stopped_due_to_errors = False
        self._task = asyncio.create_task(self._run())
        self._poll_task = asyncio.create_task(self._poll_commands())
        self._sync_task = asyncio.create_task(self._sync_stats())

        from services.worker_status import write_state
        await write_state("dedup", running=True)
        logger.info("Dedup worker started")

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
        await write_state("dedup", running=False)
        async with self._stats_lock:
            await write_stats("dedup", self._stats.copy())
        logger.info("Dedup worker stopped")

    async def pause(self):
        """Pause processing."""
        self._paused = True
        from services.worker_status import write_state
        await write_state("dedup", running=True, paused=True)
        logger.info("Dedup worker paused")

    async def resume(self):
        """Resume processing."""
        self._paused = False
        from services.worker_status import write_state
        await write_state("dedup", running=True, paused=False)
        logger.info("Dedup worker resumed")

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

    async def _on_success(self):
        """Clear degraded state on successful processing."""
        if self._stopped_due_to_errors:
            self._stopped_due_to_errors = False
            logger.info("Dedup worker recovered from degraded state")
            from services.worker_status import write_state
            await write_state("dedup", running=True, stopped_due_to_errors=False)

    async def _get_classifier(self):
        """Get or create the classifier instance."""
        if self._classifier is None:
            from services.relevance_filter import create_relevance_filter
            self._classifier = await create_relevance_filter()
        return self._classifier

    async def _run(self):
        """Main worker loop."""
        logger.info("Dedup worker loop started")

        consecutive_errors = 0
        max_consecutive_errors = 10
        last_sync_check_date = None

        while self._running:
            try:
                if self._paused:
                    await asyncio.sleep(1.0)
                    continue

                # Priority 1: Index items in vector store (prerequisite for Phase 2)
                indexed = await self._process_unindexed_items()
                if indexed > 0:
                    consecutive_errors = 0
                    await self._on_success()
                    await asyncio.sleep(0.5)
                    continue

                # Priority 2: Phase 1 dedup (URL, hash, title - no GPU needed)
                phase1 = await self._process_phase1_dedup()
                if phase1 > 0:
                    consecutive_errors = 0
                    await self._on_success()
                    await asyncio.sleep(0.5)
                    continue

                # Priority 3: Phase 2 dedup (semantic via classifier API)
                phase2 = await self._process_phase2_dedup()
                if phase2 > 0:
                    consecutive_errors = 0
                    await self._on_success()
                    await asyncio.sleep(0.5)
                    continue

                # Daily sync check: verify DB and ChromaDB are in sync
                today = utcnow().date()
                if last_sync_check_date != today and utcnow().hour >= 0:
                    last_sync_check_date = today
                    await self._check_vectordb_sync()

                # No work available, sleep
                logger.debug(f"Dedup worker idle, sleeping {self.idle_sleep}s")
                await asyncio.sleep(self.idle_sleep)

            except asyncio.CancelledError:
                logger.info("Dedup worker cancelled")
                break
            except Exception as e:
                consecutive_errors += 1
                logger.error(f"Dedup worker error ({consecutive_errors}/{max_consecutive_errors}): {e}", exc_info=True)
                async with self._stats_lock:
                    self._stats["errors"] += 1

                if consecutive_errors >= max_consecutive_errors and not self._stopped_due_to_errors:
                    logger.warning(
                        f"Dedup worker in degraded state after {consecutive_errors} errors. "
                        "Will keep retrying with backoff."
                    )
                    self._stopped_due_to_errors = True
                    from services.worker_status import write_state
                    await write_state("dedup", running=True, stopped_due_to_errors=True)

                # Exponential backoff: 10s, 20s, 40s, ... capped at 300s
                backoff = min(300.0, 10.0 * (2 ** (consecutive_errors - 1)))
                logger.info(f"Backing off for {backoff:.0f}s before retry")
                await asyncio.sleep(backoff)

        logger.info("Dedup worker loop ended")

    async def _poll_commands(self):
        """Poll for commands (pause/resume/stop) from API workers."""
        from services.worker_status import read_and_clear_command, get_poll_interval

        while self._running:
            try:
                interval = await get_poll_interval()
                await asyncio.sleep(interval)
                action = await read_and_clear_command("dedup")
                if action == "pause":
                    await self.pause()
                elif action == "resume":
                    await self.resume()
                elif action == "stop":
                    self._running = False
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Dedup command poll error: {e}")
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
                await write_stats("dedup", stats)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Dedup stats sync error: {e}")
                await asyncio.sleep(10)

    # -------------------------------------------------------------------------
    # Phase 1: URL match, content hash, title similarity (no GPU needed)
    # -------------------------------------------------------------------------

    async def _process_phase1_dedup(self) -> int:
        """
        Phase 1 dedup: URL match, content hash, and title similarity.
        Runs without GPU/classifier API. Processes items by ID ascending
        to eliminate race conditions.

        Returns:
            Number of items checked
        """
        check_days = int(os.environ.get("DUPLICATE_CHECK_DAYS", "3"))

        async with async_session_maker() as db:
            conditions = [
                Item.similar_to_id.is_(None),
                json_extract_path(Item.metadata_, "dedup_phase1").is_(None),
                # Backward compat: also pick up items without old flag
                json_extract_path(Item.metadata_, "duplicate_checked").is_(None),
            ]

            if check_days > 0:
                cutoff = utcnow() - timedelta(days=check_days)
                conditions.append(Item.fetched_at >= cutoff)

            query = (
                select(Item)
                .where(*conditions)
                .order_by(Item.id.asc())
                .limit(self.batch_size)
            )

            result = await db.execute(query)
            items = result.scalars().all()

            if not items:
                return 0

            items_data = []
            for item in items:
                items_data.append({
                    "id": item.id,
                    "title": item.title,
                    "url": item.url,
                    "channel_id": item.channel_id,
                    "content_hash": item.content_hash,
                })

        logger.info(f"Phase 1 dedup: checking {len(items_data)} items")

        checked = 0
        duplicates_found = 0
        updates = []

        for item_data in items_data:
            if self._paused or not self._running:
                break

            try:
                metadata_patch = {}
                similar_to_id = None

                # 1. URL-based duplicate check
                if item_data.get("url"):
                    async with async_session_maker() as url_db:
                        # Exact URL match
                        url_match = await url_db.scalar(
                            select(Item.id).where(
                                Item.url == item_data["url"],
                                Item.id < item_data["id"],
                                Item.channel_id != item_data["channel_id"],
                            ).order_by(Item.id).limit(1)
                        )

                        # Normalized URL match
                        if not url_match:
                            norm_url = _normalize_url(item_data["url"])
                            if norm_url != item_data["url"]:
                                url_match = await url_db.scalar(
                                    select(Item.id).where(
                                        Item.url == norm_url,
                                        Item.id < item_data["id"],
                                        Item.channel_id != item_data["channel_id"],
                                    ).order_by(Item.id).limit(1)
                                )

                        if url_match:
                            similar_to_id = url_match
                            metadata_patch["duplicate_method"] = "url_match"
                            duplicates_found += 1
                            logger.info(
                                f"Phase 1 URL duplicate: '{item_data['title'][:40]}...' "
                                f"same URL as item {similar_to_id}"
                            )

                # 2. Content hash duplicate check
                if not similar_to_id and item_data.get("content_hash"):
                    async with async_session_maker() as hash_db:
                        hash_match = await hash_db.scalar(
                            select(Item.id).where(
                                Item.content_hash == item_data["content_hash"],
                                Item.id < item_data["id"],
                            ).order_by(Item.id).limit(1)
                        )
                        if hash_match:
                            similar_to_id = hash_match
                            metadata_patch["duplicate_method"] = "content_hash"
                            duplicates_found += 1
                            logger.info(
                                f"Phase 1 hash duplicate: '{item_data['title'][:40]}...' "
                                f"same hash as item {similar_to_id}"
                            )

                # 3. Title similarity check against recent items
                if not similar_to_id:
                    clean_title = _strip_boilerplate(item_data["title"]).lower()
                    async with async_session_maker() as title_db:
                        # Check against items from last N days with lower ID
                        title_cutoff = utcnow() - timedelta(days=check_days)
                        recent_items = await title_db.execute(
                            select(Item.id, Item.title).where(
                                Item.id < item_data["id"],
                                Item.fetched_at >= title_cutoff,
                                Item.similar_to_id.is_(None),
                            ).order_by(Item.id.desc()).limit(200)
                        )
                        for row in recent_items.fetchall():
                            other_title = _strip_boilerplate(row[1]).lower()
                            if _titles_similar(clean_title, other_title):
                                similar_to_id = row[0]
                                metadata_patch["duplicate_method"] = "title_similarity"
                                duplicates_found += 1
                                logger.info(
                                    f"Phase 1 title duplicate: '{item_data['title'][:40]}...' "
                                    f"similar to item {similar_to_id}"
                                )
                                break

                # Mark Phase 1 complete
                now = utcnow().isoformat()
                metadata_patch["dedup_phase1"] = True
                metadata_patch["dedup_phase1_at"] = now
                # Backward compatibility
                metadata_patch["duplicate_checked"] = True
                metadata_patch["duplicate_checked_at"] = now

                updates.append({
                    "id": item_data["id"],
                    "similar_to_id": similar_to_id,
                    "metadata_patch": metadata_patch,
                })
                checked += 1

            except Exception as e:
                logger.warning(f"Phase 1 dedup failed for item {item_data['id']}: {e}")
                async with self._stats_lock:
                    self._stats["errors"] += 1

        # Apply updates
        if updates:
            try:
                async with async_session_maker() as db:
                    # Verify similar_to_ids exist
                    similar_ids = [u["similar_to_id"] for u in updates if u["similar_to_id"] is not None]
                    if similar_ids:
                        result = await db.execute(
                            select(Item.id).where(Item.id.in_(similar_ids))
                        )
                        existing_ids = set(row[0] for row in result.fetchall())

                        for upd in updates:
                            if upd["similar_to_id"] is not None and upd["similar_to_id"] not in existing_ids:
                                logger.warning(
                                    f"Skipping similar_to_id={upd['similar_to_id']} for item {upd['id']} - "
                                    f"referenced item no longer exists"
                                )
                                upd["similar_to_id"] = None
                                upd["metadata_patch"].pop("duplicate_method", None)

                    for upd in updates:
                        await db.execute(
                            update(Item)
                            .where(Item.id == upd["id"])
                            .values(
                                similar_to_id=upd["similar_to_id"],
                                metadata_=json_merge(Item.metadata_, upd["metadata_patch"]),
                            )
                        )
                    await db.commit()
            except Exception as e:
                logger.error(f"Failed to commit Phase 1 dedup updates: {e}")
                raise

        async with self._stats_lock:
            self._stats["phase1_checked"] += checked
            self._stats["duplicates_found"] += duplicates_found
            self._stats["last_processed_at"] = utcnow().isoformat()

        if checked > 0:
            logger.info(f"Phase 1 dedup: checked {checked} items ({duplicates_found} duplicates found)")

        return checked

    # -------------------------------------------------------------------------
    # Phase 2: Semantic similarity via ChromaDB (requires classifier API)
    # -------------------------------------------------------------------------

    async def _process_phase2_dedup(self) -> int:
        """
        Phase 2 dedup: semantic similarity via classifier API.
        Skips gracefully if classifier unavailable.

        Returns:
            Number of items checked
        """
        try:
            classifier = await self._get_classifier()
            if not classifier:
                logger.debug("Classifier unavailable for Phase 2 dedup")
                return 0
        except Exception as e:
            logger.debug(f"Cannot create classifier for Phase 2 dedup: {e}")
            return 0

        check_days = int(os.environ.get("DUPLICATE_CHECK_DAYS", "3"))

        async with async_session_maker() as db:
            from sqlalchemy import or_

            conditions = [
                Item.similar_to_id.is_(None),
                # Phase 1 done (new flag) OR old duplicate_checked flag (backward compat)
                or_(
                    json_extract_path(Item.metadata_, "dedup_phase1").isnot(None),
                    json_extract_path(Item.metadata_, "duplicate_checked").isnot(None),
                ),
                json_extract_path(Item.metadata_, "dedup_phase2").is_(None),
            ]

            if check_days > 0:
                cutoff = utcnow() - timedelta(days=check_days)
                conditions.append(Item.fetched_at >= cutoff)

            query = (
                select(Item)
                .where(*conditions)
                .order_by(Item.id.asc())
                .limit(self.batch_size)
            )

            result = await db.execute(query)
            items = result.scalars().all()

            if not items:
                return 0

            items_data = []
            for item in items:
                items_data.append({
                    "id": item.id,
                    "title": item.title,
                    "content": item.content or "",
                })

        logger.info(f"Phase 2 dedup: checking {len(items_data)} items")

        checked = 0
        duplicates_found = 0
        updates = []

        for item_data in items_data:
            if self._paused or not self._running:
                break

            try:
                metadata_patch = {}
                similar_to_id = None

                # Semantic duplicate check
                clean_title = _strip_boilerplate(item_data["title"])
                clean_content = _strip_boilerplate(item_data["content"])

                # Only match against items from recent days
                fetched_after = None
                if check_days > 0:
                    fetched_after = (utcnow() - timedelta(days=check_days)).isoformat()

                duplicates = await classifier.find_duplicates(
                    title=clean_title,
                    content=clean_content,
                    threshold=0.75,
                    fetched_after=fetched_after,
                )

                if duplicates:
                    for dup in duplicates:
                        dup_id = int(dup["id"])
                        if dup_id != item_data["id"] and dup_id < item_data["id"]:
                            similar_to_id = dup_id
                            metadata_patch["duplicate_score"] = dup.get("score")
                            metadata_patch["duplicate_method"] = "semantic"
                            duplicates_found += 1
                            logger.info(
                                f"Phase 2 semantic duplicate: '{item_data['title'][:40]}...' "
                                f"similar to item {similar_to_id} (score: {dup.get('score', 0):.3f})"
                            )
                            break

                # Mark Phase 2 complete
                now = utcnow().isoformat()
                metadata_patch["dedup_phase2"] = True
                metadata_patch["dedup_phase2_at"] = now

                updates.append({
                    "id": item_data["id"],
                    "similar_to_id": similar_to_id,
                    "metadata_patch": metadata_patch,
                })
                checked += 1

            except Exception as e:
                logger.warning(f"Phase 2 dedup failed for item {item_data['id']}: {e}")
                async with self._stats_lock:
                    self._stats["errors"] += 1

        # Apply updates
        if updates:
            try:
                async with async_session_maker() as db:
                    # Verify similar_to_ids exist
                    similar_ids = [u["similar_to_id"] for u in updates if u["similar_to_id"] is not None]
                    if similar_ids:
                        result = await db.execute(
                            select(Item.id).where(Item.id.in_(similar_ids))
                        )
                        existing_ids = set(row[0] for row in result.fetchall())

                        for upd in updates:
                            if upd["similar_to_id"] is not None and upd["similar_to_id"] not in existing_ids:
                                logger.warning(
                                    f"Skipping similar_to_id={upd['similar_to_id']} for item {upd['id']} - "
                                    f"referenced item no longer exists (stale ChromaDB entry)"
                                )
                                upd["similar_to_id"] = None
                                upd["metadata_patch"].pop("duplicate_score", None)
                                upd["metadata_patch"].pop("duplicate_method", None)

                    for upd in updates:
                        values = {"metadata_": json_merge(Item.metadata_, upd["metadata_patch"])}
                        if upd["similar_to_id"] is not None:
                            values["similar_to_id"] = upd["similar_to_id"]
                        await db.execute(
                            update(Item)
                            .where(Item.id == upd["id"])
                            .values(**values)
                        )
                    await db.commit()
            except Exception as e:
                logger.error(f"Failed to commit Phase 2 dedup updates: {e}")
                raise

        async with self._stats_lock:
            self._stats["phase2_checked"] += checked
            self._stats["duplicates_found"] += duplicates_found
            self._stats["last_processed_at"] = utcnow().isoformat()

        if checked > 0:
            logger.info(f"Phase 2 dedup: checked {checked} items ({duplicates_found} duplicates found)")

        return checked

    # -------------------------------------------------------------------------
    # Vector store indexing (moved from classifier_worker)
    # -------------------------------------------------------------------------

    async def _process_unindexed_items(self) -> int:
        """
        Index items in vector store that weren't indexed during ingestion.
        Catches items saved while classifier API was down.

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

        async with async_session_maker() as db:
            query = (
                select(Item)
                .where(
                    json_extract_path(Item.metadata_, "vectordb_indexed").is_(None),
                )
                .options(selectinload(Item.channel).selectinload(Channel.source))
                .order_by(Item.id.asc())
                .limit(self.batch_size)
            )

            result = await db.execute(query)
            items = result.scalars().all()

            if not items:
                return 0

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
                        "fetched_at": item.fetched_at.isoformat() if item.fetched_at else "",
                    },
                })
                item_ids.append(item.id)

        logger.info(f"Indexing {len(items_to_index)} items in vector store")

        try:
            indexed = await classifier.index_items_batch(items_to_index)
        except Exception as e:
            logger.warning(f"Failed to index items: {e}")
            async with self._stats_lock:
                self._stats["errors"] += 1
            return 0

        if indexed == 0:
            logger.warning(f"Batch index returned 0 for {len(items_to_index)} items, skipping flag update")
            return 0

        # Mark items as indexed
        if item_ids:
            try:
                async with async_session_maker() as db:
                    for item_id in item_ids:
                        await db.execute(
                            update(Item)
                            .where(Item.id == item_id)
                            .values(metadata_=json_merge(Item.metadata_, {
                                "vectordb_indexed": True,
                                "vectordb_indexed_at": utcnow().isoformat(),
                            }))
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

    # -------------------------------------------------------------------------
    # Daily ChromaDB <-> DB sync check (moved from classifier_worker)
    # -------------------------------------------------------------------------

    async def _check_vectordb_sync(self) -> None:
        """Daily check: compare DB indexed count with ChromaDB item count."""
        try:
            classifier = await self._get_classifier()
            if not classifier:
                return

            health = await classifier.get_health()
            if not health:
                logger.warning("VectorDB sync check: classifier health unavailable")
                return

            chromadb_count = health.get("duplicate_index_items", 0)

            async with async_session_maker() as db:
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
_worker: DedupWorker | None = None


def get_dedup_worker() -> DedupWorker | None:
    """Get the global dedup worker instance."""
    return _worker


async def start_dedup_worker(
    batch_size: int = 50,
    idle_sleep: float = 30.0,
) -> DedupWorker:
    """Start the global dedup worker."""
    global _worker

    if _worker is not None:
        logger.warning("Dedup worker already exists, stopping old instance")
        await _worker.stop()

    _worker = DedupWorker(
        batch_size=batch_size,
        idle_sleep=idle_sleep,
    )
    await _worker.start()
    return _worker


async def stop_dedup_worker():
    """Stop the global dedup worker."""
    global _worker

    if _worker is not None:
        await _worker.stop()
        _worker = None
