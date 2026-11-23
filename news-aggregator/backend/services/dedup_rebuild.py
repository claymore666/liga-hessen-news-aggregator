"""Rebuild duplicate groups for a time range.

Repairs legacy ``similar_to_id`` links: copy -> copy chains, links that came
from a degenerate embedding, and relevant copies hidden under irrelevant top
items. Every item of the range is re-checked in id order against the earlier
items (exact URL / content hash, then the classifier's id-based similarity
lookup), the best-rated member of every group is put on top, and the
difference to the stored links is reported (dry run) or written.

Layout:
- ``plan_groups`` / ``summarize``: the decision part. No DB access; the
  classifier is any object with ``find_duplicates_by_id``.
- ``rebuild_groups``: load -> plan -> summarize -> apply.
- ``start_rebuild`` / ``get_rebuild_status``: the single background run behind
  ``/api/admin/dedup/rebuild``. A PostgreSQL advisory lock makes sure only one
  run exists across all worker processes; the status is mirrored to the
  cross-worker store so every process reports the same run.

What is read and written:
- Items fetched in [since, until) are re-checked and may be re-linked.
- Items fetched in the DUPLICATE_CHECK_DAYS before ``since`` are context:
  valid link targets, not re-checked themselves.
- Stored links are never evidence (most legacy links are wrong). A context
  item that is matched and is itself a stored copy has that link verified
  first; if it does not hold, the context item is unlinked and becomes the
  top. An item without a vector is unlinked and handed back to the dedup
  worker. Only a failed lookup keeps the stored link.
- Writes outside the range are limited to (a) unlinking such context items,
  (b) rows that point at an in-range item which became a copy (re-pointed to
  its new top) and (c) the final ``rebalance_group`` pass.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import time
from collections import deque
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, NamedTuple

from sqlalchemy import func, select, update
from sqlalchemy.orm import aliased

from database import (
    async_session_maker,
    json_extract_path,
    json_merge,
    json_merge_remove,
    utcnow,
)
from models import Item, Priority
from services.dedup_groups import (
    DUPLICATE_THRESHOLD_MAYBE,
    GROUP_REBALANCE_LOCK_KEY,
    decide_semantic_link,
    pick_top,
    rebalance_group,
)
from services.dedup_worker import LOOKUP_N_RESULTS
from services.pipeline import _normalize_url, _strip_boilerplate

logger = logging.getLogger(__name__)

APPLY_BATCH_SIZE = 500
REBALANCE_COMMIT_EVERY = 100
MAX_SAMPLES = 30
# fetched_at is not strictly ordered by id; keep candidates a little longer
# than the check window before dropping them from the sliding window.
_WINDOW_SLACK = timedelta(hours=6)
# This many failed lookups in a row mean the classifier is down. The run stops
# (dry runs too: their numbers would be meaningless). "Not indexed" is not a
# failure and has no limit.
MAX_FAILED_LOOKUP_STREAK = 10
_MAX_CHAIN_HOPS = 100
_IN_CHUNK = 1000

# duplicate_method values written by the rebuild in addition to the ones from
# the link rules (url_match, content_hash, semantic, semantic_title).
METHOD_REBALANCED = "rebalanced"  # former top item, now under a better-rated copy
METHOD_KEPT = "kept_unverified"  # lookup failed; reporting only, never written
# Done-marker of the dedup worker's semantic phase. Removed from items that
# could not be compared, so the worker checks them once they have a vector.
PHASE2_MARKER_KEYS = ["dedup_phase2", "dedup_phase2_at"]

ProgressCallback = Callable[[int, int], Awaitable[None] | None]


@dataclass(slots=True)
class RebuildItem:
    """The columns the rebuild needs (no content)."""

    id: int
    title: str
    url: str | None
    content_hash: str | None
    channel_id: int | None
    priority: str
    similar_to_id: int | None
    fetched_at: datetime
    is_archived: bool = False


class StoredLink(NamedTuple):
    """Stored state of an item outside the range."""

    similar_to_id: int | None
    priority: str = "none"
    is_archived: bool = False
    url: str | None = None
    content_hash: str | None = None
    channel_id: int | None = None


class ContextUnlink(NamedTuple):
    """A context item whose stored link did not hold and is removed."""

    stored_link: int
    recheck: bool  # could not be compared at all: hand back to the dedup worker


@dataclass(slots=True)
class LinkInfo:
    """Why an item is a copy in the new mapping."""

    method: str
    score: float | None = None


@dataclass
class RebuildPlan:
    """New grouping for the items of the range."""

    # in-range item id -> id of its top item, None if the item is a top item
    # itself. The top can lie outside the range (context item or older).
    new_top: dict[int, int | None] = field(default_factory=dict)
    # item id -> reason, for every item that is a copy in new_top
    link_info: dict[int, LinkInfo] = field(default_factory=dict)
    # top id -> priority of the item that ends up on top of that group (differs
    # from the top's own priority for the promotions in rebalance_tops)
    top_priority: dict[int, str] = field(default_factory=dict)
    # Tops outside the range with a better-rated in-range copy. The rebuild
    # does not re-link items outside the range itself; the rebalance pass
    # after applying promotes these on live data.
    rebalance_tops: list[int] = field(default_factory=list)
    # Context items that were matched and whose own stored link did not hold:
    # they become top items (the one write to context items).
    context_unlinked: dict[int, ContextUnlink] = field(default_factory=dict)
    # In-range top items that could not be compared (no vector, or their
    # stored target has none): their dedup worker done-marker is removed.
    recheck: set[int] = field(default_factory=set)
    promoted_groups: int = 0
    skipped_not_indexed: int = 0  # in-range items without a vector
    unlinked_not_indexed: int = 0  # ... of those and their copies: stored link removed
    lookup_errors: int = 0


class LookupFailedError(RuntimeError):
    """Too many consecutive lookups failed; the run was stopped before writing."""


def _priority_value(priority: Any) -> str:
    return str(getattr(priority, "value", priority) or "none")


def _naive_utc(value: datetime | None) -> datetime | None:
    """Timestamps are stored as naive UTC; accept aware datetimes from the API."""
    if value is not None and value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _check_days() -> int:
    return int(os.environ.get("DUPLICATE_CHECK_DAYS", "3"))


def _chunks(values: list, size: int) -> Iterable[list]:
    for start in range(0, len(values), size):
        yield values[start:start + size]


# ---------------------------------------------------------------------------
# Decision part (no DB)
# ---------------------------------------------------------------------------


class _ExactIndex:
    """Earlier items by normalized URL and by content hash."""

    def __init__(self) -> None:
        self._by_url: dict[str, list[tuple[int, int | None]]] = {}
        self._by_hash: dict[str, int] = {}

    def find(self, item: RebuildItem) -> tuple[int | None, str | None]:
        """Earlier item with the same URL on another channel, or the same hash."""
        if item.url:
            for other_id, other_channel in self._by_url.get(_normalize_url(item.url), ()):
                if other_channel != item.channel_id:
                    return other_id, "url_match"
        if item.content_hash:
            other_id = self._by_hash.get(item.content_hash)
            if other_id is not None:
                return other_id, "content_hash"
        return None, None

    def add(self, item: RebuildItem) -> None:
        if item.url:
            self._by_url.setdefault(_normalize_url(item.url), []).append(
                (item.id, item.channel_id)
            )
        if item.content_hash:
            self._by_hash.setdefault(item.content_hash, item.id)


def _root(parent: dict[int, int | None], item_id: int) -> int:
    """Top of ``item_id`` following ``parent``.

    Stops at an item without a parent, before an id that is not in ``parent``
    (row not loaded) and before closing a loop.
    """
    seen = {item_id}
    current = item_id
    while (nxt := parent.get(current)) is not None and nxt in parent and nxt not in seen:
        seen.add(nxt)
        current = nxt
    return current


def _exact_pair(a: Any, b: Any) -> bool:
    """Same URL on another channel, or the same content hash."""
    if a.content_hash and a.content_hash == b.content_hash:
        return True
    return bool(
        a.url
        and b.url
        and a.channel_id != b.channel_id
        and _normalize_url(a.url) == _normalize_url(b.url)
    )


async def plan_groups(
    items: list[RebuildItem],
    classifier: Any,
    *,
    context: list[RebuildItem] | None = None,
    outside: dict[int, StoredLink] | None = None,
    check_days: int | None = None,
    progress_cb: ProgressCallback | None = None,
    max_failed_streak: int | None = MAX_FAILED_LOOKUP_STREAK,
) -> RebuildPlan:
    """Decide the new duplicate groups for ``items``.

    Args:
        items: Items of the range (the only ones that are re-checked).
        classifier: Object with ``find_duplicates_by_id(item_id, candidate_ids,
            threshold=..., n_results=..., raise_on_error=True)`` returning
            matches, ``[]`` or None (not indexed) and raising when the call
            failed.
        context: Items fetched shortly before the range: link targets. A
            context item that is a stored copy is only used through its stored
            top if that link passes the link rule; otherwise it becomes a top
            (``plan.context_unlinked``).
        outside: Stored state of further items outside the range that stored
            chains lead through (e.g. an older top of a context item).
        check_days: Candidate window in days (default: DUPLICATE_CHECK_DAYS,
            0 = no window).
        progress_cb: Called with (processed, total).
        max_failed_streak: Raise LookupFailedError after this many consecutive
            failed lookups (None = never).
    """
    if check_days is None:
        check_days = _check_days()
    window_span = timedelta(days=check_days) if check_days > 0 else None

    items = sorted(items, key=lambda it: it.id)
    by_id = {it.id: it for it in items}
    in_range = set(by_id)
    context = [c for c in (context or []) if c.id not in in_range]
    context_by_id = {c.id: c for c in context}
    total = len(items)

    # Stored state of everything outside the range.
    stored: dict[int, StoredLink] = {
        oid: StoredLink(*entry) for oid, entry in (outside or {}).items() if oid not in in_range
    }
    for ctx in context:
        stored[ctx.id] = StoredLink(
            ctx.similar_to_id, ctx.priority, ctx.is_archived,
            ctx.url, ctx.content_hash, ctx.channel_id,
        )

    # parent: the link every known item has in the new grouping (flattened to
    # top items at the end). Stored links are not evidence, so everything
    # starts as a top; a context item only gets its stored top back through
    # verify_context().
    parent: dict[int, int | None] = dict.fromkeys([*stored, *in_range])
    info: dict[int, LinkInfo] = {}
    plan = RebuildPlan()

    failed_streak = answered = 0

    async def ask(item_id: int, candidate_ids: list[int]) -> tuple[list[dict] | None, bool]:
        """One lookup -> (matches or None if not indexed, call failed)."""
        nonlocal failed_streak, answered
        try:
            matches = await classifier.find_duplicates_by_id(
                item_id,
                candidate_ids,
                threshold=DUPLICATE_THRESHOLD_MAYBE,
                n_results=LOOKUP_N_RESULTS,
                raise_on_error=True,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            plan.lookup_errors += 1
            failed_streak += 1
            if max_failed_streak is not None and failed_streak >= max_failed_streak:
                raise LookupFailedError(
                    f"{failed_streak} lookups in a row failed (last item {item_id}: "
                    f"{type(e).__name__}: {e}). Classifier unavailable; nothing was written."
                ) from e
            return None, True
        failed_streak = 0
        answered += 1
        return matches, False

    def stored_anchor(item_id: int) -> int:
        """Follow stored links outside the range, up to a stored top or into the range."""
        seen = {item_id}
        current = item_id
        while current not in in_range:
            nxt = stored[current].similar_to_id
            if nxt is None or nxt in seen or nxt not in parent:
                break
            seen.add(nxt)
            current = nxt
        return current

    verified_context: set[int] = set()

    async def verify_context(context_id: int) -> None:
        """Before a context item is used as a target: does its own stored link hold?"""
        if context_id in verified_context or context_id not in context_by_id:
            return
        verified_context.add(context_id)
        ctx = context_by_id[context_id]
        if ctx.similar_to_id is None:
            return  # a top already

        top_id = stored_anchor(context_id)
        holds = recheck = False
        if top_id != context_id:
            if _exact_pair(ctx, by_id.get(top_id) or stored[top_id]):
                holds = True
            else:
                matches, failed = await ask(context_id, [top_id])
                if matches:
                    decision = decide_semantic_link(_strip_boilerplate(ctx.title), matches)
                    holds = decision.link_id == top_id
                else:
                    # No vector / failed call: nothing known about this item
                    recheck = matches is None
        if holds:
            parent[context_id] = top_id
        else:
            plan.context_unlinked[context_id] = ContextUnlink(ctx.similar_to_id, recheck)

    async def link(item_id: int, target: int | None, reason: LinkInfo) -> bool:
        """Link item -> target unless unknown or already in the item's group."""
        if target is None or target == item_id or target not in parent:
            return False
        await verify_context(target)
        if _root(parent, target) == item_id:
            return False  # would close a loop: the target already sits under the item
        parent[item_id] = target
        info[item_id] = reason
        return True

    exact = _ExactIndex()
    window: deque[tuple[datetime, int]] = deque()
    newest_seen: datetime | None = None
    not_indexed: set[int] = set()
    lookup_failed: set[int] = set()
    processed = 0

    for item in sorted([*items, *context], key=lambda it: it.id):
        if item.id in in_range:
            processed += 1
            match_id, method = exact.find(item)
            if match_id is None or not await link(item.id, match_id, LinkInfo(method=method)):
                if window_span is not None:
                    cutoff = item.fetched_at - window_span
                    candidate_ids = [cid for fetched, cid in window if fetched >= cutoff]
                else:
                    candidate_ids = [cid for _, cid in window]

                # Also asked without candidates: the answer tells whether the
                # item has a vector at all.
                matches, failed = await ask(item.id, candidate_ids)
                if failed:
                    lookup_failed.add(item.id)
                    # The one case a stored link is kept: as it is in the DB
                    # (through the stored top if the target is outside the range).
                    if item.similar_to_id in parent:
                        target = item.similar_to_id
                        if target not in in_range:
                            target = stored_anchor(target)
                        if _root(parent, target) != item.id:
                            parent[item.id] = target
                            info[item.id] = LinkInfo(method=METHOD_KEPT)
                elif matches is None:
                    # No vector: no link. Visible beats hidden under a link
                    # nobody could check.
                    not_indexed.add(item.id)
                else:
                    decision = decide_semantic_link(_strip_boilerplate(item.title), matches)
                    await link(
                        item.id,
                        decision.link_id,
                        LinkInfo(method=decision.method, score=decision.score),
                    )
                    # Grey-band candidates and no match: no link.

            if progress_cb is not None and (processed % 25 == 0 or processed == total):
                outcome = progress_cb(processed, total)
                if inspect.isawaitable(outcome):
                    await outcome

        exact.add(item)
        window.append((item.fetched_at, item.id))
        if newest_seen is None or item.fetched_at > newest_seen:
            newest_seen = item.fetched_at
        if window_span is not None:
            drop_before = newest_seen - window_span - _WINDOW_SLACK
            while window and window[0][0] < drop_before:
                window.popleft()

    if plan.lookup_errors and not answered:
        # Short range, fewer lookups than the streak limit: same conclusion.
        raise LookupFailedError(
            f"All {plan.lookup_errors} lookups failed. Classifier unavailable; nothing was written."
        )
    plan.skipped_not_indexed = len(not_indexed)

    # Flatten to groups and put the best-rated member on top.
    groups: dict[int, list[int]] = {}
    for item in items:
        groups.setdefault(_root(parent, item.id), []).append(item.id)

    for root_id, member_ids in groups.items():
        ranking = [(mid, by_id[mid].priority, by_id[mid].is_archived) for mid in member_ids]

        if root_id in in_range:
            top_id = pick_top(ranking, root_id) if len(member_ids) > 1 else root_id
            if top_id != root_id:
                plan.promoted_groups += 1
            plan.top_priority[top_id] = by_id[top_id].priority
        else:
            # The top lies outside the range and is not re-linked here. If an
            # in-range member outranks it, the rebalance pass promotes it.
            top_id = root_id
            root = stored[root_id]
            best_id = pick_top([(root_id, root.priority, root.is_archived), *ranking], root_id)
            if best_id != root_id:
                plan.promoted_groups += 1
                plan.rebalance_tops.append(root_id)
                plan.top_priority[top_id] = by_id[best_id].priority
            else:
                plan.top_priority[top_id] = root.priority

        for member_id in member_ids:
            if member_id == top_id:
                plan.new_top[member_id] = None
            else:
                plan.new_top[member_id] = top_id
                # Only a former top has no reason of its own to be a copy.
                plan.link_info[member_id] = info.get(member_id) or LinkInfo(
                    method=METHOD_REBALANCED
                )

    # Top items nobody could compare go back to the dedup worker: items
    # without a vector, and items whose stored target has none (that target
    # can never show up as a match).
    for item in items:
        if plan.new_top[item.id] is not None:
            continue
        if item.id in not_indexed or item.similar_to_id in not_indexed:
            plan.recheck.add(item.id)
            if item.similar_to_id is not None:
                plan.unlinked_not_indexed += 1
        elif item.id in lookup_failed:
            plan.recheck.add(item.id)

    return plan


def _top_before(item_id: int, chain: dict[int, StoredLink]) -> int:
    """Top item of ``item_id`` following the stored links (cycle-safe)."""
    seen = {item_id}
    current = item_id
    for _ in range(_MAX_CHAIN_HOPS):
        entry = chain.get(current)
        nxt = entry[0] if entry else None
        if nxt is None or nxt in seen or nxt not in chain:
            break
        seen.add(nxt)
        current = nxt
    return current


def summarize(
    items: list[RebuildItem],
    plan: RebuildPlan,
    outside: dict[int, StoredLink] | None = None,
) -> dict:
    """Compare the plan with the stored links.

    Args:
        items: Items of the range.
        plan: Result of plan_groups.
        outside: Stored state of the items outside the range that stored
            chains pass through (only needed for ``relevant_hidden_before``).

    Returns:
        ``{"summary": {...}, "changes": [(item, new_target), ...]}``
    """
    chain: dict[int, StoredLink] = {
        oid: StoredLink(*entry) for oid, entry in (outside or {}).items()
    }
    for item in items:
        chain[item.id] = StoredLink(item.similar_to_id, item.priority, item.is_archived)

    changes: list[tuple[RebuildItem, int | None]] = []
    counts = {"added": 0, "removed": 0, "repointed": 0}
    samples: dict[str, list[dict]] = {"added": [], "removed": [], "repointed": []}
    per_kind = MAX_SAMPLES // len(samples)
    links_before = links_after = 0
    hidden_before = hidden_after = kept = 0
    tops_with_copies: set[int] = set()

    for item in items:
        old = item.similar_to_id
        new = plan.new_top.get(item.id)
        relevant = item.priority != "none"

        if old is not None:
            links_before += 1
            if relevant and chain[_top_before(item.id, chain)].priority == "none":
                hidden_before += 1
        if new is not None:
            links_after += 1
            tops_with_copies.add(new)
            if relevant and plan.top_priority.get(new) == "none":
                hidden_after += 1
            if plan.link_info[item.id].method == METHOD_KEPT:
                kept += 1

        if old == new:
            continue
        kind = "added" if old is None else "removed" if new is None else "repointed"
        counts[kind] += 1
        changes.append((item, new))
        if len(samples[kind]) < per_kind:
            link = plan.link_info.get(item.id) if new is not None else None
            samples[kind].append({
                "id": item.id,
                "title": (item.title or "")[:80],
                "old_target": old,
                "new_target": new,
                "score": link.score if link else None,
                "method": link.method if link else None,
            })

    summary = {
        "items": len(items),
        # Groups with at least one copy after the rebuild
        "groups": len(tops_with_copies),
        "links_before": links_before,
        "links_after": links_after,
        "links_added": counts["added"],
        "links_removed": counts["removed"],
        "links_repointed": counts["repointed"],
        # Includes promotions over a top outside the range (done by the
        # rebalance pass of a real run)
        "promoted_groups": plan.promoted_groups,
        # Items of the range without a vector / stored links removed because
        # the item or its stored target has none
        "skipped_not_indexed": plan.skipped_not_indexed,
        "unlinked_not_indexed": plan.unlinked_not_indexed,
        "lookup_errors": plan.lookup_errors,
        # Stored links kept without a check (the lookup failed). They are not
        # covered by relevant_hidden_after being correct.
        "unverified_links_kept": kept,
        # Matched context items whose own stored link did not hold
        "context_unlinked": len(plan.context_unlinked),
        "relevant_hidden_before": hidden_before,
        "relevant_hidden_after": hidden_after,
        "samples": samples["added"] + samples["removed"] + samples["repointed"],
    }
    return {"summary": summary, "changes": changes}


# ---------------------------------------------------------------------------
# DB part
# ---------------------------------------------------------------------------

_ITEM_COLUMNS = (
    Item.id,
    Item.title,
    Item.url,
    Item.content_hash,
    Item.channel_id,
    Item.priority,
    Item.similar_to_id,
    Item.fetched_at,
    Item.is_archived,
)


async def _select_items(*conditions) -> list[RebuildItem]:
    async with async_session_maker() as db:
        rows = await db.execute(
            select(*_ITEM_COLUMNS).where(*conditions).order_by(Item.id.asc())
        )
        return [
            RebuildItem(
                id=row.id,
                title=row.title or "",
                url=row.url,
                content_hash=row.content_hash,
                channel_id=row.channel_id,
                priority=_priority_value(row.priority),
                similar_to_id=row.similar_to_id,
                fetched_at=row.fetched_at,
                is_archived=bool(row.is_archived),
            )
            for row in rows.all()
        ]


async def _load_items(since: datetime, until: datetime | None) -> list[RebuildItem]:
    conditions = [Item.fetched_at >= since]
    if until is not None:
        conditions.append(Item.fetched_at < until)
    return await _select_items(*conditions)


async def _load_context(since: datetime, check_days: int) -> list[RebuildItem]:
    """Items fetched in the check window before ``since`` (all earlier if no window)."""
    conditions = [Item.fetched_at < since]
    if check_days > 0:
        conditions.append(Item.fetched_at >= since - timedelta(days=check_days))
    return await _select_items(*conditions)


async def _load_outside_chain(
    items: list[RebuildItem], context: list[RebuildItem]
) -> dict[int, StoredLink]:
    """Stored state of the items outside range and context that chains lead through."""
    known = {it.id for it in items} | {it.id for it in context}
    outside: dict[int, StoredLink] = {}
    frontier = {
        it.similar_to_id for it in (*items, *context) if it.similar_to_id is not None
    } - known

    async with async_session_maker() as db:
        for _ in range(_MAX_CHAIN_HOPS):
            if not frontier:
                break
            wanted = sorted(frontier)
            frontier = set()
            for chunk in _chunks(wanted, _IN_CHUNK):
                rows = await db.execute(
                    select(
                        Item.id, Item.similar_to_id, Item.priority, Item.is_archived,
                        Item.url, Item.content_hash, Item.channel_id,
                    ).where(Item.id.in_(chunk))
                )
                for row in rows.all():
                    outside[row.id] = StoredLink(
                        row.similar_to_id, _priority_value(row.priority), bool(row.is_archived),
                        row.url, row.content_hash, row.channel_id,
                    )
                    if row.similar_to_id is not None and row.similar_to_id not in known:
                        frontier.add(row.similar_to_id)
            frontier -= outside.keys()

    return outside


async def _outside_rows_at_copies(db, plan: RebuildPlan) -> list[tuple[int, int, int | None]]:
    """Rows outside the range that point at an in-range item which the plan makes a copy.

    Returns ``(row id, in-range target id, target's current similar_to_id)``.
    """
    copies = sorted(item_id for item_id, top in plan.new_top.items() if top is not None)
    target = aliased(Item)
    found: list[tuple[int, int, int | None]] = []
    for chunk in _chunks(copies, _IN_CHUNK):
        rows = await db.execute(
            select(Item.id, Item.similar_to_id, target.similar_to_id)
            .join(target, target.id == Item.similar_to_id)
            .where(Item.similar_to_id.in_(chunk))
        )
        found.extend(
            (row[0], row[1], row[2])
            for row in rows.all()
            if row[0] not in plan.new_top and row[0] not in plan.context_unlinked
        )
    return found


def _metadata_expr(
    new_target: int | None, link: LinkInfo | None, rebuilt_at: str, recheck: bool = False
):
    """Metadata update for one changed row.

    ``recheck``: also drop the dedup worker's done-marker (unlinked rows only).
    """
    stamp = {"dedup_rebuilt_at": rebuilt_at}
    if new_target is None:
        remove = ["duplicate_method", "duplicate_score"]
        return json_merge_remove(
            Item.metadata_, stamp, remove + PHASE2_MARKER_KEYS if recheck else remove
        )
    if link is None or link.method == METHOD_KEPT:
        # Link kept without a new check: the stored method/score still apply.
        return json_merge(Item.metadata_, stamp)
    patch = {**stamp, "duplicate_method": link.method}
    if link.score is None:
        return json_merge_remove(Item.metadata_, patch, ["duplicate_score"])
    return json_merge(Item.metadata_, {**patch, "duplicate_score": link.score})


def _unchanged_since_load(item: RebuildItem):
    if item.similar_to_id is None:
        return Item.similar_to_id.is_(None)
    return Item.similar_to_id == item.similar_to_id


def _is_top_now(item_id: int):
    """EXISTS condition: ``item_id`` exists and is a top item right now."""
    top = aliased(Item)
    return select(top.id).where(top.id == item_id, top.similar_to_id.is_(None)).exists()


async def _lock_group_writes(db) -> None:
    """Keep other writers' rebalance_group out for the current transaction."""
    await db.execute(select(func.pg_advisory_xact_lock(GROUP_REBALANCE_LOCK_KEY)))


async def _clear_new_tops(
    db,
    changes: list[tuple[RebuildItem, int | None]],
    plan: RebuildPlan,
    rebuilt_at: str,
    touched: set[int],
) -> tuple[int, int, int]:
    """Apply pass 1: every item that becomes a top item loses its link.

    Done (and committed) before any copy is linked, so a swap A <-> X never
    exists as "both are copies": until the copies follow, the feed shows too
    many items, never too few. Includes the context items whose stored link
    did not hold.

    Returns (in-range rows written, conflicts, context items unlinked).
    """
    applied = conflicts = context_unlinked = 0
    # (id, condition "link as loaded", drop the worker's done-marker, in range)
    new_tops = [
        (item.id, _unchanged_since_load(item), item.id in plan.recheck, True)
        for item, new_target in changes
        if new_target is None
    ]
    new_tops += [
        (context_id, Item.similar_to_id == unlink.stored_link, unlink.recheck, False)
        for context_id, unlink in plan.context_unlinked.items()
    ]
    for batch in _chunks(new_tops, APPLY_BATCH_SIZE):
        await _lock_group_writes(db)
        for item_id, unchanged, recheck, is_in_range in batch:
            result = await db.execute(
                update(Item)
                .where(Item.id == item_id, unchanged)
                .values(
                    similar_to_id=None,
                    metadata_=_metadata_expr(None, None, rebuilt_at, recheck=recheck),
                )
            )
            if not result.rowcount:
                conflicts += 1
                continue
            touched.add(item_id)
            if is_in_range:
                applied += 1
            else:
                context_unlinked += 1
        await db.commit()
    return applied, conflicts, context_unlinked


async def _clear_recheck_markers(db, plan: RebuildPlan, changed_ids: set[int]) -> None:
    """Hand top items that could not be compared back to the dedup worker.

    Rows that were unlinked lost the marker in pass 1 already; these are the
    ones that had no link to begin with.
    """
    marker = json_extract_path(Item.metadata_, PHASE2_MARKER_KEYS[0])
    for chunk in _chunks(sorted(plan.recheck - changed_ids), _IN_CHUNK):
        await db.execute(
            update(Item)
            .where(Item.id.in_(chunk), Item.similar_to_id.is_(None), marker.isnot(None))
            .values(metadata_=json_merge_remove(Item.metadata_, {}, PHASE2_MARKER_KEYS))
        )
        await db.commit()


async def _link_copies(
    db,
    changes: list[tuple[RebuildItem, int | None]],
    plan: RebuildPlan,
    rebuilt_at: str,
    touched: set[int],
) -> tuple[int, int]:
    """Apply pass 2: link the copies.

    A row is written only if its link is still the one the plan was made from
    and its target is a top item at that moment; everything else is a conflict
    and left alone (the rebalance pass and a second run pick it up).
    """
    applied = conflicts = 0
    copies = [(item, new_target) for item, new_target in changes if new_target is not None]
    for batch in _chunks(copies, APPLY_BATCH_SIZE):
        await _lock_group_writes(db)
        for item, new_target in batch:
            result = await db.execute(
                update(Item)
                .where(Item.id == item.id, _unchanged_since_load(item), _is_top_now(new_target))
                .values(
                    similar_to_id=new_target,
                    metadata_=_metadata_expr(new_target, plan.link_info.get(item.id), rebuilt_at),
                )
            )
            if result.rowcount:
                applied += 1
                touched.add(new_target)
            else:
                conflicts += 1
        await db.commit()
    return applied, conflicts


async def _repoint_outside_rows(db, plan: RebuildPlan, touched: set[int]) -> int:
    """Rows outside the range that point at a new copy follow it to its top.

    Only where the in-range item really is under its planned top now. Their
    metadata is left alone.
    """
    repointed = 0
    rows = await _outside_rows_at_copies(db, plan)
    await db.commit()
    for batch in _chunks(rows, APPLY_BATCH_SIZE):
        await _lock_group_writes(db)
        for row_id, target_id, target_link in batch:
            new_top = plan.new_top[target_id]
            if target_link != new_top or new_top == row_id:
                continue
            result = await db.execute(
                update(Item)
                .where(Item.id == row_id, Item.similar_to_id == target_id, _is_top_now(new_top))
                .values(similar_to_id=new_top)
            )
            if result.rowcount:
                repointed += 1
                touched.add(new_top)
        await db.commit()
    return repointed


async def _rebalance_touched(db, top_ids: Iterable[int]) -> int:
    """Re-pick the top of every touched group from live data.

    Priorities may have changed while the plan was made (LLM / classifier
    workers keep running), and groups whose top lies outside the range were
    left for this pass. Returns the number of groups that changed.
    """
    changed = 0
    for count, top_id in enumerate(sorted(set(top_ids)), start=1):
        try:
            async with db.begin_nested():
                result = await rebalance_group(db, top_id)
            if result is not None:
                changed += 1
        except Exception as e:
            logger.warning(f"Dedup rebuild: could not rebalance group of item {top_id}: {e}")
        if count % REBALANCE_COMMIT_EVERY == 0:
            await db.commit()  # also releases the advisory lock
    await db.commit()
    return changed


async def _apply_changes(
    changes: list[tuple[RebuildItem, int | None]], plan: RebuildPlan
) -> dict:
    """Write the plan: new tops first, then copies, then the rebalance pass."""
    rebuilt_at = utcnow().isoformat()
    # Every group of the plan, not only the rewritten ones: a priority that
    # changed while planning can unbalance a group whose links stay as they are.
    touched: set[int] = set(plan.rebalance_tops)
    touched.update(top for top in plan.new_top.values() if top is not None)

    async with async_session_maker() as db:
        cleared, conflicts_tops, context_unlinked = await _clear_new_tops(
            db, changes, plan, rebuilt_at, touched
        )
        await _clear_recheck_markers(db, plan, {item.id for item, _ in changes})
        linked, conflicts_copies = await _link_copies(db, changes, plan, rebuilt_at, touched)
        outside_repointed = await _repoint_outside_rows(db, plan, touched)
        rebalanced = await _rebalance_touched(db, touched)

    return {
        "applied": cleared + linked,
        "apply_conflicts": conflicts_tops + conflicts_copies,
        "context_unlinked": context_unlinked,
        "outside_repointed": outside_repointed,
        "rebalanced_after_apply": rebalanced,
    }


async def _count_relevant_hidden(since: datetime, until: datetime | None) -> int:
    """Relevant items of the range that sit under an irrelevant item right now."""
    top = aliased(Item)
    conditions = [
        Item.fetched_at >= since,
        Item.priority != Priority.NONE,
        top.priority == Priority.NONE,
    ]
    if until is not None:
        conditions.append(Item.fetched_at < until)
    async with async_session_maker() as db:
        result = await db.execute(
            select(func.count())
            .select_from(Item)
            .join(top, top.id == Item.similar_to_id)
            .where(*conditions)
        )
        return int(result.scalar() or 0)


async def rebuild_groups(
    since: datetime,
    until: datetime | None,
    dry_run: bool,
    classifier: Any,
    progress_cb: ProgressCallback | None = None,
) -> dict:
    """Rebuild the duplicate groups of the items fetched in [since, until).

    Returns the summary (see ``summarize``) plus ``dry_run``, ``since``,
    ``until``, ``context_items``, ``outside_links_to_copies`` and, for a real
    run, ``applied`` / ``apply_conflicts`` / ``outside_repointed`` /
    ``rebalanced_after_apply`` (``context_unlinked`` and
    ``relevant_hidden_after`` are then counted from what was written).
    """
    since = _naive_utc(since)
    until = _naive_utc(until)

    items = await _load_items(since, until)
    context = await _load_context(since, _check_days())
    outside = await _load_outside_chain(items, context)
    logger.info(
        f"Dedup rebuild ({'dry run' if dry_run else 'APPLY'}): {len(items)} items "
        f"fetched in [{since}, {until or 'now'}), {len(context)} context items"
    )
    if progress_cb is not None:
        outcome = progress_cb(0, len(items))
        if inspect.isawaitable(outcome):
            await outcome

    plan = await plan_groups(
        items, classifier, context=context, outside=outside, progress_cb=progress_cb
    )

    stored_outside = dict(outside)
    for ctx in context:
        stored_outside[ctx.id] = StoredLink(ctx.similar_to_id, ctx.priority, ctx.is_archived)
    outcome = summarize(items, plan, stored_outside)

    summary = outcome["summary"]
    summary["dry_run"] = dry_run
    summary["since"] = since.isoformat()
    summary["until"] = until.isoformat() if until else None
    summary["context_items"] = len(context)
    async with async_session_maker() as db:
        summary["outside_links_to_copies"] = len(await _outside_rows_at_copies(db, plan))

    if not dry_run:
        summary.update(await _apply_changes(outcome["changes"], plan))
        summary["relevant_hidden_after"] = await _count_relevant_hidden(since, until)

    logger.info(
        "Dedup rebuild finished: "
        + ", ".join(f"{key}={value}" for key, value in summary.items() if key != "samples")
    )
    return summary


# ---------------------------------------------------------------------------
# Single background run + status
# ---------------------------------------------------------------------------

STATUS_NAME = "dedup_rebuild"  # key in the cross-worker status store
# Session-level advisory lock held on a connection of its own for the whole
# run: only one rebuild across all worker processes. PostgreSQL drops it when
# the connection goes away, so a crashed process cannot leave it behind.
RUN_LOCK_KEY: int = 0x4C4947_5242_4C44  # "LIGRBLD"
# A "running" status that was not refreshed for this long belongs to a process
# that is gone (restart, crash).
STALE_RUNNING_SECONDS = 900
_PUBLISH_INTERVAL_SECONDS = 2.0


class RebuildAlreadyRunning(Exception):
    """A rebuild is already in progress."""


def _idle_status() -> dict:
    return {
        "state": "idle",
        "dry_run": None,
        "since": None,
        "until": None,
        "started_at": None,
        "finished_at": None,
        "progress": {"processed": 0, "total": 0},
        "result": None,
        "error": None,
    }


_status: dict = _idle_status()
_task: asyncio.Task | None = None
_last_publish = 0.0


async def _acquire_run_lock():
    """Take the run lock on a dedicated connection; None if another run holds it."""
    conn = await async_session_maker.kw["bind"].connect()
    try:
        # Autocommit: the connection stays idle (not "idle in transaction")
        # while it holds the lock.
        await conn.execution_options(isolation_level="AUTOCOMMIT")
        acquired = (
            await conn.execute(select(func.pg_try_advisory_lock(RUN_LOCK_KEY)))
        ).scalar()
    except BaseException:
        await conn.close()
        raise
    if not acquired:
        await conn.close()
        return None
    return conn


async def _release_run_lock(conn) -> None:
    try:
        await conn.execute(select(func.pg_advisory_unlock(RUN_LOCK_KEY)))
    except Exception as e:
        # Never hand a connection that may still hold the lock back to the pool.
        logger.warning(f"Could not release dedup rebuild lock, dropping the connection: {e}")
        try:
            await conn.invalidate()
        except Exception:
            pass
    try:
        await conn.close()
    except Exception:
        pass


async def _publish(force: bool = False) -> None:
    """Mirror the status to the cross-worker store (throttled)."""
    global _last_publish
    now = time.monotonic()
    if not force and now - _last_publish < _PUBLISH_INTERVAL_SECONDS:
        return
    _last_publish = now
    try:
        from services.worker_status import write_stats
        await write_stats(STATUS_NAME, dict(_status))
    except Exception as e:
        logger.debug(f"Could not publish dedup rebuild status: {e}")


async def _read_shared_status() -> dict | None:
    try:
        from services.worker_status import read_stats
        shared = await read_stats(STATUS_NAME)
    except Exception as e:
        logger.debug(f"Could not read dedup rebuild status: {e}")
        return None
    if not shared or "state" not in shared:
        return None

    synced_at = shared.pop("synced_at", None)
    if shared["state"] == "running":
        try:
            age = (utcnow() - datetime.fromisoformat(synced_at)).total_seconds()
        except (TypeError, ValueError):
            age = STALE_RUNNING_SECONDS + 1
        if age > STALE_RUNNING_SECONDS:
            shared["state"] = "failed"
            shared["error"] = "Run was interrupted (backend restarted?); nothing more was written."
    return shared


async def get_rebuild_status() -> dict:
    """Status of the current or last run, whichever worker process ran it."""
    if _task is not None and not _task.done():
        return dict(_status)

    shared = await _read_shared_status()
    if shared is None:
        return dict(_status)
    # Another process ran a newer rebuild: report that one.
    if (shared.get("started_at") or "") > (_status.get("started_at") or ""):
        return {**_idle_status(), **shared}
    return dict(_status)


async def _run(
    since: datetime, until: datetime | None, dry_run: bool, classifier: Any, lock_conn: Any
) -> None:
    async def on_progress(processed: int, total: int) -> None:
        _status["progress"] = {"processed": processed, "total": total}
        await _publish()

    try:
        result = await rebuild_groups(since, until, dry_run, classifier, on_progress)
        _status["result"] = result
        _status["state"] = "done"
    except asyncio.CancelledError:
        _status["state"] = "failed"
        _status["error"] = "cancelled"
        raise
    except Exception as e:
        logger.error(f"Dedup rebuild failed: {e}", exc_info=True)
        _status["state"] = "failed"
        _status["error"] = f"{type(e).__name__}: {e}"
    finally:
        _status["finished_at"] = utcnow().isoformat()
        close = getattr(classifier, "close", None)
        if close is not None:
            try:
                await close()
            except Exception:
                pass
        try:
            await asyncio.shield(_release_run_lock(lock_conn))
        except Exception:
            pass
        try:
            await asyncio.shield(_publish(force=True))
        except Exception:
            pass


async def start_rebuild(
    since: datetime, until: datetime | None, dry_run: bool, classifier: Any
) -> dict:
    """Start the background run. Raises RebuildAlreadyRunning if one is active."""
    global _status, _task

    if _task is not None and not _task.done():
        raise RebuildAlreadyRunning()

    since = _naive_utc(since)
    until = _naive_utc(until)

    # Atomic across requests and worker processes: whoever gets the lock runs.
    lock_conn = await _acquire_run_lock()
    if lock_conn is None:
        raise RebuildAlreadyRunning()

    # No await from here to create_task.
    _status = _idle_status()
    _status.update(
        state="running",
        dry_run=dry_run,
        since=since.isoformat(),
        until=until.isoformat() if until else None,
        started_at=utcnow().isoformat(),
    )
    _task = asyncio.create_task(_run(since, until, dry_run, classifier, lock_conn))
    await _publish(force=True)
    return dict(_status)
