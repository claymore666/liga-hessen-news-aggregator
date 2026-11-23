"""Duplicate-group rules shared by the pipeline, the dedup worker and the LLM worker.

A duplicate group is a *star*: exactly one top item (``similar_to_id IS NULL``)
that the feed shows, with every copy pointing directly at it. This module holds
the rules for deciding whether two items belong together and the helpers that
keep groups in that shape.

Leaf module: it must not import ``services.pipeline`` (the pipeline imports it).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models import Item

logger = logging.getLogger(__name__)

# Semantic duplicate thresholds (cosine similarity from the classifier service).
#   score >= CONFIRMED          -> same story, link automatically
#   MAYBE <= score < CONFIRMED  -> same story only if the titles are similar,
#                                  otherwise a candidate for LLM confirmation
DUPLICATE_THRESHOLD_CONFIRMED: float = float(
    os.environ.get("DUPLICATE_THRESHOLD_CONFIRMED", "0.88")
)
DUPLICATE_THRESHOLD_MAYBE: float = float(
    os.environ.get("DUPLICATE_THRESHOLD_MAYBE", "0.75")
)

PRIORITY_RANK: dict[str, int] = {"high": 3, "medium": 2, "low": 1, "none": 0}

# Titles must share at least this many words. Without it, rubric headlines such
# as "Duma-Wahl: Auf einen Blick" / "Wildtiere: Auf einen Blick" count as the
# same story because the short rubric dominates the overlap ratio.
_MIN_SHARED_TITLE_WORDS = 4
# Overlap relative to the longer title, so a short title is not swallowed by a
# long one that merely contains its words.
_MIN_OVERLAP_OF_LONGER_TITLE = 0.5

# Upper bound when walking similar_to_id chains (legacy chains were ~30 deep).
_MAX_CHAIN_HOPS = 50

# One fixed key for pg_advisory_xact_lock: every rebalance takes it first, so
# group rewrites never interleave (two writers could otherwise leave a
# copy -> copy chain behind). Held until the surrounding transaction ends.
GROUP_REBALANCE_LOCK_KEY: int = 0x4C4947_41445550  # "LIGADUP"
# Keep IN (...) lists well below the driver's bind-parameter limit.
_IN_CHUNK = 1000


def titles_similar(title1: str, title2: str, threshold: float = 0.7) -> bool:
    """Check if two titles are similar enough to be the same story.

    Word overlap on lowercase tokens longer than two characters. All three
    conditions must hold:

    - at least 4 shared words,
    - shared / smaller title >= ``threshold``,
    - shared / longer title >= 0.5.

    Example that matches:
    - "Vielen Kitas fehlen Fachkräfte - Mittelhessen"
    - "Kinderbetreuung: Vielen Kitas fehlen Fachkräfte | DPA"
    """
    words1 = {w for w in (title1 or "").lower().split() if len(w) > 2}
    words2 = {w for w in (title2 or "").lower().split() if len(w) > 2}

    if not words1 or not words2:
        return False

    shared = len(words1 & words2)
    if shared < _MIN_SHARED_TITLE_WORDS:
        return False

    smaller = min(len(words1), len(words2))
    larger = max(len(words1), len(words2))
    return shared / smaller >= threshold and shared / larger >= _MIN_OVERLAP_OF_LONGER_TITLE


@dataclass
class LinkDecision:
    """Outcome of comparing a new item against its semantic matches."""

    # Matched item id to link to. The caller must still resolve it to the top
    # item of its group (see resolve_primary) - never link copy -> copy.
    link_id: int | None = None
    # Similarity score of the linked match (None when nothing was linked).
    score: float | None = None
    # "semantic" (score >= CONFIRMED) or
    # "semantic_title" (MAYBE <= score < CONFIRMED and titles similar)
    method: str | None = None
    # {"candidate_id", "candidate_title", "similarity_score"} for LLM review
    # when nothing was linked but the best score is >= MAYBE.
    candidate: dict | None = None


def _match_id(match: dict) -> int | None:
    try:
        return int(match["id"])
    except (KeyError, TypeError, ValueError):
        return None


def decide_semantic_link(title: str, matches: list[dict]) -> LinkDecision:
    """Decide what to do with the semantic matches of an item.

    Args:
        title: Title of the item being checked (boilerplate-stripped).
        matches: ``[{"id": str|int, "title": str, "score": float, ...}]`` in any
            order.

    Matches are tried by descending score and the first one that satisfies a
    link rule wins. If none does, the best match with score >= MAYBE becomes the
    LLM-confirmation candidate. Otherwise the decision is empty.
    """
    usable = [
        (float(m.get("score") or 0.0), mid, m)
        for m in matches or []
        if (mid := _match_id(m)) is not None
    ]
    usable.sort(key=lambda entry: entry[0], reverse=True)

    title_lower = (title or "").lower()
    for score, match_id, match in usable:
        if score < DUPLICATE_THRESHOLD_MAYBE:
            break
        if score >= DUPLICATE_THRESHOLD_CONFIRMED:
            return LinkDecision(link_id=match_id, score=score, method="semantic")
        if titles_similar(title_lower, (match.get("title") or "").lower()):
            return LinkDecision(link_id=match_id, score=score, method="semantic_title")

    if usable and usable[0][0] >= DUPLICATE_THRESHOLD_MAYBE:
        score, match_id, match = usable[0]
        return LinkDecision(
            candidate={
                "candidate_id": match_id,
                "candidate_title": match.get("title", ""),
                "similarity_score": score,
            }
        )

    return LinkDecision()


def _priority_value(priority) -> str:
    """Priority as its plain string value (accepts the enum or a string)."""
    return str(getattr(priority, "value", priority) or "none")


def pick_top(members: Sequence[tuple], current_top_id: int | None) -> int:
    """Choose which member of a duplicate group the feed should show.

    Args:
        members: ``(item_id, priority value)`` or
            ``(item_id, priority value, is_archived)`` for every item in the
            group. A missing archived flag counts as not archived.
        current_top_id: The item currently on top, if any.

    Ranking: non-archived members first (the default feed hides archived
    items, so an archived top would hide its copies), then the highest
    PRIORITY_RANK. On a tie the current top stays if it is among the best (no
    pointless reshuffling), otherwise the lowest id wins.
    """
    if not members:
        raise ValueError("pick_top needs at least one member")

    ranked = []
    for member in members:
        item_id, priority = member[0], member[1]
        archived = bool(member[2]) if len(member) > 2 else False
        ranked.append(((not archived, PRIORITY_RANK.get(_priority_value(priority), 0)), item_id))

    best_rank = max(rank for rank, _ in ranked)
    best_ids = [item_id for rank, item_id in ranked if rank == best_rank]

    if current_top_id is not None and current_top_id in best_ids:
        return current_top_id
    return min(best_ids)


async def resolve_primary(db: AsyncSession, item_id: int) -> int | None:
    """Follow ``similar_to_id`` from ``item_id`` up to the top item of its group.

    Returns None if ``item_id`` does not exist. Cycle-safe: walks at most
    ``_MAX_CHAIN_HOPS`` links and returns the lowest id seen when the chain
    loops back on itself.
    """
    seen: list[int] = []
    current = item_id

    for _ in range(_MAX_CHAIN_HOPS + 1):
        row = (
            await db.execute(select(Item.id, Item.similar_to_id).where(Item.id == current))
        ).first()
        if row is None:
            # Start item missing -> None. A dangling parent cannot normally
            # happen (FK is ON DELETE SET NULL); treat the last real item as top.
            return seen[-1] if seen else None

        seen.append(current)
        parent = row.similar_to_id
        if parent is None:
            return current
        if parent in seen:
            logger.warning(f"similar_to_id cycle detected at item {item_id}: {seen}")
            return min(seen)
        current = parent

    # Chain deeper than the hop limit: return the furthest ancestor reached.
    logger.warning(
        f"similar_to_id chain from item {item_id} exceeds {_MAX_CHAIN_HOPS} hops; "
        f"stopping at item {seen[-1]}"
    )
    return seen[-1]


async def rebalance_group(db: AsyncSession, item_id: int) -> int | None:
    """Put the best item of ``item_id``'s duplicate group on top, as a star.

    The group is the top item of ``item_id`` plus every item whose chain leads
    to it (legacy copy -> copy chains included). ``pick_top`` chooses the top
    (non-archived first, then priority), then the group is rewritten so the top
    has ``similar_to_id = NULL`` and every other member points directly at it.

    This is what makes a relevant copy visible when it was nested under an
    irrelevant top item.

    Serialised by a transaction-scoped advisory lock that is taken before the
    group is read, so (under READ COMMITTED) the reads see everything another
    rebalance committed. The lock is released when the caller's transaction
    ends - commit soon after calling this, and commit your own writes *before*
    calling it so no row locks are held while waiting.

    Returns the top id if anything changed, else None. Flushes, does not commit.
    """
    await db.execute(select(func.pg_advisory_xact_lock(GROUP_REBALANCE_LOCK_KEY)))

    current_top_id = await resolve_primary(db, item_id)
    if current_top_id is None:
        return None

    top_row = (
        await db.execute(
            select(Item.id, Item.priority, Item.similar_to_id, Item.is_archived).where(
                Item.id == current_top_id
            )
        )
    ).first()
    if top_row is None:
        return None

    # id -> (priority value, current similar_to_id, is_archived)
    members: dict[int, tuple[str, int | None, bool]] = {
        top_row.id: (
            _priority_value(top_row.priority),
            top_row.similar_to_id,
            bool(top_row.is_archived),
        )
    }

    # Collect descendants level by level so legacy chains are picked up too.
    frontier = [top_row.id]
    while frontier:
        next_frontier: list[int] = []
        for start in range(0, len(frontier), _IN_CHUNK):
            chunk = frontier[start:start + _IN_CHUNK]
            rows = await db.execute(
                select(Item.id, Item.priority, Item.similar_to_id, Item.is_archived).where(
                    Item.similar_to_id.in_(chunk)
                )
            )
            for row in rows:
                if row.id in members:
                    continue  # already collected (cycle or self-reference)
                members[row.id] = (
                    _priority_value(row.priority),
                    row.similar_to_id,
                    bool(row.is_archived),
                )
                next_frontier.append(row.id)
        frontier = next_frontier

    new_top_id = pick_top(
        [(member_id, priority, archived) for member_id, (priority, _, archived) in members.items()],
        current_top_id,
    )

    to_relink = [
        member_id
        for member_id, (_, parent, _) in members.items()
        if member_id != new_top_id and parent != new_top_id
    ]
    top_needs_clearing = members[new_top_id][1] is not None

    if not to_relink and not top_needs_clearing:
        return None

    if top_needs_clearing:
        await db.execute(
            update(Item).where(Item.id == new_top_id).values(similar_to_id=None)
        )
    for start in range(0, len(to_relink), _IN_CHUNK):
        chunk = to_relink[start:start + _IN_CHUNK]
        await db.execute(
            update(Item).where(Item.id.in_(chunk)).values(similar_to_id=new_top_id)
        )
    await db.flush()

    if new_top_id != current_top_id:
        logger.info(
            f"Duplicate group rebalanced: item {new_top_id} replaces {current_top_id} "
            f"as top of {len(members)} items"
        )
    else:
        logger.info(
            f"Duplicate group flattened: {len(to_relink)} item(s) relinked to top {new_top_id}"
        )
    return new_top_id


async def rebalance_groups_guarded(
    db: AsyncSession, item_ids: Iterable[int], *, context: str = ""
) -> int:
    """Rebalance the duplicate group of each item; never raises.

    For callers that just changed an item's priority or linked a copy: the
    group's top may no longer be its best member.

    The caller must have committed its own write first. Each rebalance runs in
    a savepoint and is committed on its own (which releases the advisory lock),
    so a failure is logged and undone without touching anything else in the
    session. Returns the number of groups that changed.
    """
    where = f" ({context})" if context else ""
    changed = 0
    for item_id in dict.fromkeys(i for i in item_ids if i is not None):
        new_top_id = None
        try:
            async with db.begin_nested():
                new_top_id = await rebalance_group(db, item_id)
        except Exception as err:
            # Only the savepoint is rolled back; objects loaded in the session
            # stay usable for the caller.
            new_top_id = None
            logger.warning(f"Failed to rebalance duplicate group of item {item_id}{where}: {err}")
        try:
            await db.commit()  # also releases the advisory lock
        except Exception as err:
            logger.warning(f"Failed to commit rebalance of item {item_id}{where}: {err}")
            try:
                await db.rollback()
            except Exception:
                pass
            continue
        if new_top_id is not None:
            changed += 1
    return changed
