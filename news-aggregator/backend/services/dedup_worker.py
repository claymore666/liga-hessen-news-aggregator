"""
Deduplication Background Worker.

Independent worker that handles all deduplication duties:
1. Index items in the classifier's vector store (prerequisite for semantic dedup)
2. Phase 1: URL match and content hash (no GPU needed)
3. Phase 2: Semantic similarity on the stored embeddings (classifier API,
   id-based lookup) combined with the title rule from services.dedup_groups
4. Daily vector store <-> DB consistency check

Serial ID-ascending processing eliminates race conditions from
concurrent channel fetches. Links always point at the top item of a group
(never copy -> copy), see services.dedup_groups.
"""

import asyncio
import logging
import os
import time
from datetime import timedelta, timezone

from sqlalchemy import cast, func, or_, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import selectinload

from database import async_session_maker, json_extract_path, json_merge, utcnow
from models import Channel, Item
from services.dedup_groups import (
    DUPLICATE_THRESHOLD_MAYBE,
    decide_semantic_link,
    rebalance_group,
    resolve_primary,
)
from services.pipeline import _normalize_url, _strip_boilerplate

logger = logging.getLogger(__name__)

# Phase 2: wait this long before asking the classifier again about an item it
# could not compare (not indexed yet, or the call failed).
PHASE2_RETRY_SECONDS = 300.0
# Matches fetched per lookup. A title-confirmed grey-band match can rank well
# below the best-scoring ones, so the default of 5 loses real duplicates.
LOOKUP_N_RESULTS = 20
# Indexing: after this many passes in a row in which a batch with the same
# lowest item id failed, the batch is indexed item by item.
INDEX_ISOLATE_AFTER_FAILURES = 3
# Item-by-item indexing gives up after this long without a single success.
INDEX_ISOLATE_BUDGET_SECONDS = 120.0


def _json_merge_bound(column, patch: dict):
    """Like database.json_merge, but with the patch as a bound parameter.

    Used for patches that carry free text (titles): json_merge embeds the JSON
    in a text() literal, where a " :word" sequence is read as a bind parameter.
    """
    return func.coalesce(cast(column, JSONB), cast({}, JSONB)).op("||")(cast(patch, JSONB))


class DedupWorker:
    """
    Background worker for deduplication processing.

    Two-phase approach:
    - Phase 1 (always runs, no GPU): URL match, content hash
    - Phase 2 (when classifier available): semantic similarity on the stored
      embeddings, grey-band matches only together with similar titles
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
        self._consecutive_errors = 0

        # Phase 2: items the classifier could not compare yet (not indexed or
        # call failed) -> monotonic time of the next attempt. Keeps them from
        # blocking the head of the id-ordered queue.
        self._phase2_retry_at: dict[int, float] = {}

        # Indexing: lowest item id of the last failed batch and how many
        # passes in a row a batch headed by it has failed (see
        # _index_items_one_by_one).
        self._index_fail_head: int | None = None
        self._index_fail_count = 0

    async def start(self):
        """Start the worker background task."""
        if self._running:
            logger.warning("Dedup worker already running")
            return

        self._running = True
        self._stopped_due_to_errors = False
        self._consecutive_errors = 0
        self._stats["started_at"] = utcnow().isoformat()
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
        self._stopped_due_to_errors = False
        self._consecutive_errors = 0
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
        """Clear error state on successful processing."""
        self._consecutive_errors = 0
        if self._stopped_due_to_errors:
            self._stopped_due_to_errors = False
            logger.info("Dedup worker recovered from error state")
            from services.worker_status import write_state
            await write_state("dedup", running=True)

    async def _get_classifier(self):
        """Get or create the classifier instance."""
        if self._classifier is None:
            from services.relevance_filter import create_relevance_filter
            self._classifier = await create_relevance_filter()
        return self._classifier

    async def _run(self):
        """Main worker loop."""
        logger.info("Dedup worker loop started")

        max_consecutive_errors = 10
        last_sync_check_date = None

        while self._running:
            try:
                if self._paused:
                    await asyncio.sleep(1.0)
                    continue

                from services.embeddings_gate import embeddings_allowed
                embeds_ok, _gate_reason = await embeddings_allowed()

                # Priority 1: Index items in vector store (prerequisite for Phase 2)
                if embeds_ok:
                    indexed = await self._process_unindexed_items()
                    if indexed > 0:
                        await self._on_success()
                        await asyncio.sleep(0.5)
                        continue

                # Priority 2: Phase 1 dedup (URL, hash — no embeddings needed,
                # runs regardless of the embeddings gate so URL-level dupes still
                # collapse during off-hours-gpu1-down.)
                phase1 = await self._process_phase1_dedup()
                if phase1 > 0:
                    await self._on_success()
                    await asyncio.sleep(0.5)
                    continue

                # Priority 3: Phase 2 dedup (semantic via classifier API)
                if embeds_ok:
                    phase2 = await self._process_phase2_dedup()
                    if phase2 > 0:
                        await self._on_success()
                        await asyncio.sleep(0.5)
                        continue

                # Daily sync check: verify DB and vector store are in sync
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
                self._consecutive_errors += 1
                logger.error(f"Dedup worker error ({self._consecutive_errors}/{max_consecutive_errors}): {e}", exc_info=True)
                async with self._stats_lock:
                    self._stats["errors"] += 1

                if self._consecutive_errors >= max_consecutive_errors and not self._stopped_due_to_errors:
                    logger.warning(
                        f"Dedup worker in error state after {self._consecutive_errors} errors. "
                        "Retrying every 300s. Use the System page to resume."
                    )
                    self._stopped_due_to_errors = True
                    from services.worker_status import write_state
                    await write_state("dedup", running=True, stopped_due_to_errors=True)

                if self._stopped_due_to_errors:
                    await asyncio.sleep(300.0)
                else:
                    # Exponential backoff: 10s, 20s, 40s, ... capped at 300s
                    backoff = min(300.0, 10.0 * (2 ** (self._consecutive_errors - 1)))
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
    # Shared: store the outcome of a dedup check
    # -------------------------------------------------------------------------

    async def _save_dedup_result(
        self,
        db,
        item_id: int,
        similar_to_id: int | None,
        metadata_patch: dict,
    ) -> None:
        """Write the phase flags (and the link, if any) for one item and commit.

        Committed per item so that the next item of the batch already sees the
        link when it resolves the top item of its own match.
        """
        values = {"metadata_": json_merge(Item.metadata_, metadata_patch)}
        if similar_to_id is not None:
            values["similar_to_id"] = similar_to_id
        await db.execute(update(Item).where(Item.id == item_id).values(**values))
        await db.commit()

        if similar_to_id is None:
            return

        # The item may have had copies of its own, or be rated higher than its
        # new top item: flatten the group and put the best-rated member on top.
        try:
            await rebalance_group(db, item_id)
            await db.commit()
        except Exception as e:
            await db.rollback()
            logger.warning(f"Rebalancing duplicate group of item {item_id} failed: {e}")

    # -------------------------------------------------------------------------
    # Phase 1: URL match, content hash (no GPU needed)
    # -------------------------------------------------------------------------

    async def _find_exact_match(self, db, item_data: dict) -> tuple[int | None, str | None]:
        """Find an earlier item with the same URL (other channel) or content hash.

        Returns:
            (matched item id, duplicate_method) or (None, None)
        """
        url = item_data.get("url")
        if url:
            url_match = await db.scalar(
                select(Item.id).where(
                    Item.url == url,
                    Item.id < item_data["id"],
                    Item.channel_id != item_data["channel_id"],
                ).order_by(Item.id).limit(1)
            )

            if not url_match:
                norm_url = _normalize_url(url)
                if norm_url != url:
                    url_match = await db.scalar(
                        select(Item.id).where(
                            Item.url == norm_url,
                            Item.id < item_data["id"],
                            Item.channel_id != item_data["channel_id"],
                        ).order_by(Item.id).limit(1)
                    )

            if url_match:
                return url_match, "url_match"

        if item_data.get("content_hash"):
            hash_match = await db.scalar(
                select(Item.id).where(
                    Item.content_hash == item_data["content_hash"],
                    Item.id < item_data["id"],
                ).order_by(Item.id).limit(1)
            )
            if hash_match:
                return hash_match, "content_hash"

        return None, None

    async def _process_phase1_dedup(self) -> int:
        """
        Phase 1 dedup: URL match and content hash.
        Runs without GPU/classifier API. Processes items by ID ascending
        to eliminate race conditions.

        Titles alone are not compared here: rubric headlines ("Auf einen
        Blick", "Der Tag") made title-only links wrong. Titles are only used
        together with the embedding score in Phase 2.

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
                select(Item.id, Item.title, Item.url, Item.channel_id, Item.content_hash)
                .where(*conditions)
                .order_by(Item.id.asc())
                .limit(self.batch_size)
            )

            result = await db.execute(query)
            items_data = [
                {
                    "id": row.id,
                    "title": row.title,
                    "url": row.url,
                    "channel_id": row.channel_id,
                    "content_hash": row.content_hash,
                }
                for row in result.all()
            ]

        if not items_data:
            return 0

        logger.info(f"Phase 1 dedup: checking {len(items_data)} items")

        checked = 0
        duplicates_found = 0

        for item_data in items_data:
            if self._paused or not self._running:
                break

            try:
                metadata_patch = {}
                similar_to_id = None

                async with async_session_maker() as db:
                    match_id, method = await self._find_exact_match(db, item_data)

                    if match_id is not None:
                        # Never link copy -> copy: point at the top item of the match
                        primary_id = await resolve_primary(db, match_id)
                        if primary_id is not None and primary_id != item_data["id"]:
                            similar_to_id = primary_id
                            metadata_patch["duplicate_method"] = method
                            logger.info(
                                f"Phase 1 {method} duplicate: '{item_data['title'][:40]}...' "
                                f"matches item {match_id} (top item {primary_id})"
                            )

                    # Mark Phase 1 complete
                    now = utcnow().isoformat()
                    metadata_patch["dedup_phase1"] = True
                    metadata_patch["dedup_phase1_at"] = now
                    # Backward compatibility
                    metadata_patch["duplicate_checked"] = True
                    metadata_patch["duplicate_checked_at"] = now

                    await self._save_dedup_result(
                        db, item_data["id"], similar_to_id, metadata_patch
                    )

                checked += 1
                if similar_to_id is not None:
                    duplicates_found += 1

            except Exception as e:
                logger.warning(f"Phase 1 dedup failed for item {item_data['id']}: {e}")
                async with self._stats_lock:
                    self._stats["errors"] += 1

        async with self._stats_lock:
            self._stats["phase1_checked"] += checked
            self._stats["duplicates_found"] += duplicates_found
            self._stats["last_processed_at"] = utcnow().isoformat()

        if checked > 0:
            logger.info(f"Phase 1 dedup: checked {checked} items ({duplicates_found} duplicates found)")

        return checked

    # -------------------------------------------------------------------------
    # Phase 2: Semantic similarity on stored embeddings (requires classifier API)
    # -------------------------------------------------------------------------

    async def _process_phase2_dedup(self) -> int:
        """
        Phase 2 dedup: semantic similarity via the classifier's id-based lookup
        (exact cosine similarity on the stored embeddings, no embedding call).

        - score >= DUPLICATE_THRESHOLD_CONFIRMED: linked
        - grey band (MAYBE..CONFIRMED): linked only if the titles are similar,
          otherwise kept as ``duplicate_candidate`` for the LLM (only while the
          item is still waiting for the LLM)
        - item not indexed yet / call failed: not marked, retried later. It
          leaves the queue by itself once older than DUPLICATE_CHECK_DAYS.

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

        # Items whose retry time has come are eligible again
        now_mono = time.monotonic()
        self._phase2_retry_at = {
            item_id: retry_at
            for item_id, retry_at in self._phase2_retry_at.items()
            if retry_at > now_mono
        }

        async with async_session_maker() as db:
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

            if self._phase2_retry_at:
                conditions.append(Item.id.notin_(list(self._phase2_retry_at)))

            query = (
                select(Item.id, Item.title, Item.fetched_at)
                .where(*conditions)
                .order_by(Item.id.asc())
                .limit(self.batch_size)
            )

            result = await db.execute(query)
            items_data = [
                {"id": row.id, "title": row.title, "fetched_at": row.fetched_at}
                for row in result.all()
            ]

        if not items_data:
            return 0

        logger.info(f"Phase 2 dedup: checking {len(items_data)} items")

        checked = 0
        deferred = 0
        duplicates_found = 0

        for item_data in items_data:
            if self._paused or not self._running:
                break

            item_id = item_data["id"]

            try:
                # Candidates: every earlier item (top items and copies) fetched
                # within the check window before this item. IDs only.
                candidate_query = select(Item.id).where(Item.id < item_id)
                if check_days > 0 and item_data["fetched_at"] is not None:
                    candidate_query = candidate_query.where(
                        Item.fetched_at >= item_data["fetched_at"] - timedelta(days=check_days)
                    )
                async with async_session_maker() as db:
                    candidate_ids = [
                        row[0] for row in (await db.execute(candidate_query)).all()
                    ]

                matches: list[dict] | None = []
                if candidate_ids:
                    matches = await classifier.find_duplicates_by_id(
                        item_id,
                        candidate_ids,
                        threshold=DUPLICATE_THRESHOLD_MAYBE,
                        n_results=LOOKUP_N_RESULTS,
                    )

                if matches is None:
                    # Not indexed yet or the call failed: leave Phase 2 open
                    # and look at the item again later. Not an error.
                    self._phase2_retry_at[item_id] = time.monotonic() + PHASE2_RETRY_SECONDS
                    deferred += 1
                    continue

                decision = decide_semantic_link(
                    _strip_boilerplate(item_data["title"]), matches
                )

                metadata_patch = {}
                similar_to_id = None

                async with async_session_maker() as db:
                    if decision.link_id is not None:
                        # Never link copy -> copy: point at the top item of the match
                        primary_id = await resolve_primary(db, decision.link_id)
                        if primary_id is not None and primary_id != item_id:
                            similar_to_id = primary_id
                            metadata_patch["duplicate_method"] = decision.method
                            metadata_patch["duplicate_score"] = decision.score
                            logger.info(
                                f"Phase 2 {decision.method} duplicate: "
                                f"'{item_data['title'][:40]}...' similar to item "
                                f"{decision.link_id} (top item {primary_id}, "
                                f"score: {decision.score or 0:.3f})"
                            )
                    elif decision.candidate:
                        # Grey band without similar titles: let the LLM decide,
                        # but only if the item is still waiting for it. The
                        # flag itself is never set here.
                        await db.execute(
                            update(Item)
                            .where(
                                Item.id == item_id,
                                Item.needs_llm_processing.is_(True),
                            )
                            .values(
                                metadata_=_json_merge_bound(
                                    Item.metadata_,
                                    {"duplicate_candidate": decision.candidate},
                                )
                            )
                        )

                    # Mark Phase 2 complete
                    now = utcnow().isoformat()
                    metadata_patch["dedup_phase2"] = True
                    metadata_patch["dedup_phase2_at"] = now

                    await self._save_dedup_result(db, item_id, similar_to_id, metadata_patch)

                checked += 1
                if similar_to_id is not None:
                    duplicates_found += 1

            except Exception as e:
                logger.warning(f"Phase 2 dedup failed for item {item_id}: {e}")
                async with self._stats_lock:
                    self._stats["errors"] += 1

        async with self._stats_lock:
            self._stats["phase2_checked"] += checked
            self._stats["duplicates_found"] += duplicates_found
            self._stats["last_processed_at"] = utcnow().isoformat()

        if checked > 0 or deferred > 0:
            logger.info(
                f"Phase 2 dedup: checked {checked} items ({duplicates_found} duplicates found, "
                f"{deferred} not comparable yet)"
            )

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
                        # Store as UTC epoch float — the classifier's windowed
                        # dedup query casts fetched_at to ::float (ISO strings
                        # crash it). fetched_at is a naive UTC datetime, and the
                        # container runs in CET/CEST, so attach UTC explicitly
                        # before .timestamp() to avoid a local-offset skew.
                        # See pgvector_store.find_duplicates.
                        "fetched_at": item.fetched_at.replace(tzinfo=timezone.utc).timestamp() if item.fetched_at else None,
                    },
                })
                item_ids.append(item.id)

        logger.info(f"Indexing {len(items_to_index)} items in vector store")

        # raise_on_error: a failed call (classifier/embeddings down, timeout,
        # HTTP error) must not be mistaken for "0 new, all already indexed".
        # The items keep no flag and are picked up again on the next pass.
        try:
            indexed = await classifier.index_items_batch(items_to_index, raise_on_error=True)
        except Exception as e:
            logger.warning(
                f"Vector indexing failed for {len(items_to_index)} items "
                f"(ids {item_ids[0]}..{item_ids[-1]}), not flagged, will retry: "
                f"{type(e).__name__}: {e}"
            )
            async with self._stats_lock:
                self._stats["errors"] += 1

            # The queue is ordered by id, so a batch that keeps failing would
            # block all indexing behind it. After INDEX_ISOLATE_AFTER_FAILURES
            # passes with the same head, find out which items are to blame.
            if self._index_fail_head == item_ids[0]:
                self._index_fail_count += 1
            else:
                self._index_fail_head, self._index_fail_count = item_ids[0], 1
            if self._index_fail_count < INDEX_ISOLATE_AFTER_FAILURES:
                return 0
            self._index_fail_head, self._index_fail_count = None, 0
            return await self._index_items_one_by_one(classifier, items_to_index, item_ids)

        self._index_fail_head, self._index_fail_count = None, 0

        if indexed == 0:
            logger.info(f"Batch index returned 0 new for {len(items_to_index)} items (already indexed), marking as indexed")

        await self._mark_indexed(item_ids)
        return len(item_ids)

    async def _write_index_metadata(self, item_ids: list[int], values: dict) -> None:
        """Merge ``values`` into the metadata of the given items."""
        if not item_ids:
            return
        try:
            async with async_session_maker() as db:
                for item_id in item_ids:
                    await db.execute(
                        update(Item)
                        .where(Item.id == item_id)
                        .values(metadata_=json_merge(Item.metadata_, values))
                    )
                await db.commit()
        except Exception as e:
            logger.error(f"Failed to update vectordb_indexed flags: {e}")
            raise

    async def _mark_indexed(self, item_ids: list[int]) -> None:
        """Flag items the classifier confirmed as stored."""
        await self._write_index_metadata(item_ids, {
            "vectordb_indexed": True,
            "vectordb_indexed_at": utcnow().isoformat(),
        })

        async with self._stats_lock:
            self._stats["vectordb_indexed"] += len(item_ids)

        if item_ids:
            logger.info(f"Indexed {len(item_ids)} items in vector store")

    async def _index_items_one_by_one(
        self, classifier, items_to_index: list[dict], item_ids: list[int]
    ) -> int:
        """
        Index a repeatedly failing batch item by item to isolate the items
        that make it fail.

        - Every item fails: the classifier is down. Nothing is flagged, the
          batch is retried as before.
        - Items that succeed are flagged as indexed.
        - An item is given up on (``vectordb_indexed: false`` plus
          ``vectordb_index_error_at``, so the queue skips it) only if it failed,
          a later item succeeded, and it failed again right after. That rules
          out a classifier that came back or went away during the pass; failed
          items without a later success stay unflagged and are retried.

        Returns:
            Number of items indexed
        """
        started = time.monotonic()
        indexed_ids: list[int] = []
        failed: list[tuple[int, int, dict]] = []  # (position, item id, payload)
        last_success_pos = -1

        for pos, (item_id, payload) in enumerate(zip(item_ids, items_to_index)):
            try:
                await classifier.index_items_batch([payload], raise_on_error=True)
                indexed_ids.append(item_id)
                last_success_pos = pos
            except Exception:
                failed.append((pos, item_id, payload))
                # A hanging classifier costs a full timeout per item: stop
                # probing once it is clear that nothing gets through.
                if not indexed_ids and time.monotonic() - started > INDEX_ISOLATE_BUDGET_SECONDS:
                    break

        if not indexed_ids:
            logger.warning(
                f"Vector indexing item by item: all {len(failed)} calls failed "
                f"(ids {item_ids[0]}..{failed[-1][1]}), classifier looks down, nothing flagged"
            )
            return 0

        unindexable: list[int] = []
        for pos, item_id, payload in failed:
            if pos > last_success_pos:
                continue
            try:
                await classifier.index_items_batch([payload], raise_on_error=True)
                indexed_ids.append(item_id)
            except Exception as e:
                unindexable.append(item_id)
                logger.warning(
                    f"Item {item_id} cannot be indexed in vector store while other items "
                    f"can, skipping it from now on (vectordb_indexed=false): "
                    f"{type(e).__name__}: {e}"
                )

        await self._mark_indexed(indexed_ids)
        await self._write_index_metadata(unindexable, {
            "vectordb_indexed": False,
            "vectordb_index_error_at": utcnow().isoformat(),
        })
        return len(indexed_ids)

    # -------------------------------------------------------------------------
    # Daily vector store <-> DB sync check (moved from classifier_worker)
    # -------------------------------------------------------------------------

    async def _check_vectordb_sync(self) -> None:
        """Daily check: compare DB indexed count with the vector store item count."""
        try:
            classifier = await self._get_classifier()
            if not classifier:
                return

            health = await classifier.get_health()
            if not health:
                logger.warning("VectorDB sync check: classifier health unavailable")
                return

            vector_count = health.get("duplicate_index_items", 0)

            async with async_session_maker() as db:
                db_count = await db.scalar(
                    select(func.count()).select_from(Item).where(
                        json_extract_path(Item.metadata_, "vectordb_indexed").isnot(None),
                    )
                )

            diff = (db_count or 0) - vector_count
            if abs(diff) > 50:
                logger.error(
                    f"VECTORDB SYNC CHECK: DB says {db_count} items indexed, "
                    f"vector store has {vector_count} items. "
                    f"Difference: {diff} items. "
                    f"POST /admin/housekeeping/vector-sync to delete orphans "
                    f"and requeue missing items for re-indexing."
                )
            elif diff > 0:
                logger.warning(
                    f"VectorDB sync: {diff} items in DB but not in vector store "
                    f"(DB: {db_count}, vector store: {vector_count})"
                )
            else:
                logger.info(
                    f"VectorDB sync check OK: DB={db_count}, vector store={vector_count}"
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
