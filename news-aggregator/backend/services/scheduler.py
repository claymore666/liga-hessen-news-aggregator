"""Background job scheduler using APScheduler."""

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from config import settings
from database import async_session_maker
from models import Channel, Source

logger = logging.getLogger(__name__)

# Per-source-type concurrency limits for parallel fetching
# For proxy-using connectors (x_scraper, instagram_scraper), the actual limit
# is dynamically capped by available proxies via get_effective_limit()
SOURCE_TYPE_LIMITS = {
    "x_scraper": 4,  # Heavy browser + rate limits, uses proxies
    "instagram_scraper": 4,  # Heavy browser, uses proxies
    "instagram": 3,
    "mastodon": 5,
    "twitter": 5,
    "rss": 10,  # Lightweight
    "html": 5,
    "bluesky": 5,
    "telegram": 3,
    "pdf": 3,
    "google_alerts": 5,
    "linkedin": 2,
}

# Connector types that use proxy reservation
PROXY_USING_CONNECTORS = {"x_scraper", "instagram_scraper", "linkedin"}


def get_effective_limit(source_type: str) -> int:
    """Get effective concurrency limit for a source type.

    For proxy-using connectors, the limit is capped by available proxies
    to prevent multiple scrapers from using the same proxy.

    Args:
        source_type: The connector type

    Returns:
        Effective concurrency limit
    """
    base_limit = SOURCE_TYPE_LIMITS.get(source_type, 3)

    if source_type in PROXY_USING_CONNECTORS:
        try:
            from services.proxy_manager import proxy_manager
            available = proxy_manager.available_count(source_type)
            # Cap at available proxies, but always allow at least 1 (direct connection)
            effective = max(1, min(base_limit, available)) if available > 0 else 1
            if effective < base_limit:
                logger.debug(f"Dynamic limit for {source_type}: {effective} "
                           f"(base={base_limit}, proxies={available})")
            return effective
        except Exception as e:
            logger.warning(f"Failed to get proxy count for {source_type}: {e}")

    return base_limit

# Track if a fetch is currently running to avoid overlapping fetches
_fetch_in_progress = False

scheduler = AsyncIOScheduler()


async def fetch_all_channels(training_mode: bool = False) -> dict:
    """Fetch items from all enabled channels across all sources.

    Fetches channels in parallel, grouped by source type. Each source type
    has its own concurrency limit to prevent overwhelming external services.

    Args:
        training_mode: If True, disables filtering for training data collection.

    Returns:
        Dict with fetch statistics.
    """
    mode_str = " (TRAINING MODE)" if training_mode else ""
    logger.info(f"Starting scheduled fetch for all channels{mode_str}")

    async with async_session_maker() as db:
        # Get all enabled channels where the parent source is also enabled
        query = (
            select(Channel)
            .join(Source)
            .options(selectinload(Channel.source))
            .where(
                Channel.enabled == True,  # noqa: E712
                Source.enabled == True,  # noqa: E712
            )
        )
        result = await db.execute(query)
        channels = result.scalars().all()

        if not channels:
            logger.info("No enabled channels to fetch")
            return {
                "channels_fetched": 0,
                "items_collected": 0,
                "errors": 0,
                "training_mode": training_mode,
            }

        # Group channels by source type
        by_type: dict[str, list[Channel]] = defaultdict(list)
        for channel in channels:
            by_type[channel.connector_type].append(channel)

        logger.info(
            f"Fetching {len(channels)} channels across {len(by_type)} source types: "
            f"{', '.join(f'{k}({len(v)})' for k, v in by_type.items())}"
        )

        # Create semaphores per source type (dynamic limits for proxy-using connectors)
        semaphores = {
            source_type: asyncio.Semaphore(get_effective_limit(source_type))
            for source_type in by_type.keys()
        }

        # Fetch all source types in parallel
        tasks = [
            _fetch_source_type_group(source_type, type_channels, semaphores[source_type], training_mode)
            for source_type, type_channels in by_type.items()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect results
        total_fetched = 0
        total_errors = 0
        for source_type, result in zip(by_type.keys(), results):
            if isinstance(result, Exception):
                logger.error(f"Error in source type group {source_type}: {result}")
                total_errors += len(by_type[source_type])
            else:
                fetched, errors = result
                total_fetched += fetched
                total_errors += errors

    logger.info(f"Completed fetch for {total_fetched} channels{mode_str}, {total_errors} errors")
    return {
        "channels_fetched": total_fetched,
        "items_collected": 0,  # Note: individual item counts not tracked in parallel mode
        "errors": total_errors,
        "training_mode": training_mode,
    }


async def fetch_channel(channel_id: int, training_mode: bool = False) -> int:
    """Fetch items from a single channel.

    This function separates network I/O (can run in parallel) from database
    writes (serialized via db_write_lock) to enable efficient parallel fetching.

    Args:
        channel_id: ID of channel to fetch
        training_mode: If True, disables filtering for training data collection

    Returns:
        Number of new items fetched.
    """
    from connectors import ConnectorRegistry
    from database import db_write_lock
    from services.pipeline import Pipeline
    from services.processor import ItemProcessor, create_processor_from_settings
    from services.relevance_filter import create_relevance_filter

    mode_str = " (training)" if training_mode else ""
    logger.info(f"Fetching channel {channel_id}{mode_str}")

    # Try to create relevance pre-filter (optional, skip in training mode)
    relevance_filter = None
    if not training_mode and settings.classifier_enabled:
        try:
            relevance_filter = await create_relevance_filter()
            if relevance_filter:
                logger.debug("Relevance pre-filter initialized")
        except Exception as e:
            logger.warning(f"Relevance pre-filter not available: {e}")

    # Try to create LLM processor (optional, skip in training mode for speed)
    processor: ItemProcessor | None = None
    if not training_mode:
        try:
            processor = await create_processor_from_settings()
            logger.debug("LLM processor initialized")
        except Exception as e:
            logger.warning(f"LLM processor not available: {e}")

    # Phase 1: Read channel info (quick read, can be parallel)
    async with async_session_maker() as db:
        query = (
            select(Channel)
            .options(selectinload(Channel.source))
            .where(Channel.id == channel_id)
        )
        result = await db.execute(query)
        channel = result.scalar_one_or_none()

        if channel is None:
            logger.warning(f"Channel {channel_id} not found")
            return 0

        if not channel.enabled:
            logger.info(f"Channel {channel_id} is disabled, skipping")
            return 0

        if not channel.source.enabled:
            logger.info(f"Parent source {channel.source_id} is disabled, skipping channel {channel_id}")
            return 0

        # Extract needed info before closing session
        connector_type = channel.connector_type
        channel_config = dict(channel.config)
        source_name = channel.source.name

    # Phase 2: Network I/O - fetch items (runs in parallel with other channels)
    try:
        connector_class = ConnectorRegistry.get(connector_type)
        if connector_class is None:
            raise ValueError(f"Unknown connector type: {connector_type}")

        connector = connector_class()
        config_dict = {"url": channel_config.get("url", ""), **channel_config}
        config_model = connector_class.config_schema(**config_dict)
        raw_items = await connector.fetch(config_model)

        logger.info(f"Connector returned {len(raw_items)} raw items from channel {channel_id}")

    except Exception as e:
        # Phase 2 error: Update channel error status (needs db lock)
        async with db_write_lock:
            async with async_session_maker() as db:
                channel = await db.get(Channel, channel_id)
                if channel:
                    channel.last_error = str(e)
                    await db.commit()
        logger.error(f"Error fetching channel {channel_id}: {e}")
        raise

    # Phase 2.5: Pre-filter items BEFORE entering database session
    # This avoids async context conflicts between httpx and SQLAlchemy
    pre_filter_results: dict[str, dict] = {}
    if relevance_filter and not training_mode and raw_items:
        logger.debug(f"Pre-filtering {len(raw_items)} items for channel {channel_id}")
        for raw_item in raw_items:
            try:
                should_process, result = await relevance_filter.should_process(
                    title=raw_item.title,
                    content=raw_item.content,
                    source=source_name,
                )
                if result:
                    pre_filter_results[raw_item.external_id] = {
                        "should_process": should_process,
                        "result": result,
                    }
            except Exception as e:
                logger.warning(f"Pre-filter failed for item '{raw_item.title[:40]}': {e}")
        logger.debug(f"Pre-filtered {len(pre_filter_results)}/{len(raw_items)} items")

    # Phase 3: Database writes - process and store items (serialized)
    async with db_write_lock:
        async with async_session_maker() as db:
            # Re-fetch channel within this session (with source eager-loaded for indexing)
            result = await db.execute(
                select(Channel).options(selectinload(Channel.source)).where(Channel.id == channel_id)
            )
            channel = result.scalar_one_or_none()
            if channel is None:
                return 0

            try:
                # Process through pipeline (includes database writes)
                # Pass pre-computed filter results to avoid async context issues
                pipeline = Pipeline(
                    db,
                    processor=processor,
                    relevance_filter=relevance_filter,
                    training_mode=training_mode,
                    pre_filter_results=pre_filter_results,
                )
                new_items = await pipeline.process(raw_items, channel)

                channel.last_fetch_at = datetime.utcnow()
                channel.last_error = None
                await db.commit()

                logger.info(f"Fetched {len(new_items)} new items from channel {channel_id}{mode_str}")
                return len(new_items)

            except Exception as e:
                logger.error(f"Error processing channel {channel_id}: {e}")
                await db.rollback()
                # Try to update error status
                try:
                    channel = await db.get(Channel, channel_id)
                    if channel:
                        channel.last_error = str(e)
                        await db.commit()
                except Exception as store_err:
                    # Don't let error storage failure mask original error
                    logger.debug(f"Could not store error for channel {channel_id}: {store_err}")
                raise


async def _fetch_source_type_group(
    source_type: str,
    channels: list[Channel],
    semaphore: asyncio.Semaphore,
    training_mode: bool = False,
) -> tuple[int, int]:
    """Fetch all channels of a given source type with concurrency limit.

    Args:
        source_type: The connector type (e.g., 'rss', 'x_scraper')
        channels: List of channels to fetch
        semaphore: Semaphore to limit concurrent fetches
        training_mode: If True, disables filtering for training data collection

    Returns:
        Tuple of (fetched_count, error_count)
    """
    fetched = 0
    errors = 0
    results_lock = asyncio.Lock()

    async def fetch_with_limit(channel: Channel) -> None:
        nonlocal fetched, errors
        async with semaphore:
            try:
                await fetch_channel(channel.id, training_mode=training_mode)
                async with results_lock:
                    fetched += 1
            except Exception as e:
                logger.error(
                    f"Error fetching channel {channel.id} "
                    f"({channel.source.name}/{channel.connector_type}): {e}"
                )
                async with results_lock:
                    errors += 1

    # Run all channels of this type concurrently (within semaphore limit)
    await asyncio.gather(*[fetch_with_limit(ch) for ch in channels], return_exceptions=True)
    return fetched, errors


async def fetch_due_channels() -> dict:
    """Fetch channels that are past their individual fetch interval.

    Fetches channels in parallel, grouped by source type. Each source type
    has its own concurrency limit to prevent overwhelming external services.

    Checks every enabled channel and fetches those where:
    now - last_fetch_at > fetch_interval_minutes

    Also checks that the parent source is enabled.

    Returns:
        Dict with fetch statistics.
    """
    global _fetch_in_progress

    if _fetch_in_progress:
        logger.debug("Fetch already in progress, skipping")
        return {"skipped": True, "reason": "fetch_in_progress"}

    _fetch_in_progress = True
    try:
        now = datetime.utcnow()

        async with async_session_maker() as db:
            # Find enabled channels where parent source is also enabled
            query = (
                select(Channel)
                .join(Source)
                .options(selectinload(Channel.source))
                .where(
                    Channel.enabled == True,  # noqa: E712
                    Source.enabled == True,  # noqa: E712
                )
            )
            result = await db.execute(query)
            all_channels = result.scalars().all()

            # Filter in Python since SQLite doesn't support interval arithmetic well
            due_channels = []
            for channel in all_channels:
                if channel.last_fetch_at is None:
                    due_channels.append(channel)
                else:
                    due_time = channel.last_fetch_at + timedelta(minutes=channel.fetch_interval_minutes)
                    if due_time < now:
                        due_channels.append(channel)

            if not due_channels:
                return {"due_channels": 0, "fetched": 0, "errors": 0}

            # Group channels by source type
            by_type: dict[str, list[Channel]] = defaultdict(list)
            for channel in due_channels:
                by_type[channel.connector_type].append(channel)

            logger.info(
                f"Found {len(due_channels)} channels due for fetching "
                f"across {len(by_type)} source types: "
                f"{', '.join(f'{k}({len(v)})' for k, v in by_type.items())}"
            )

            # Create semaphores per source type (dynamic limits for proxy-using connectors)
            semaphores = {
                source_type: asyncio.Semaphore(get_effective_limit(source_type))
                for source_type in by_type.keys()
            }

            # Fetch all source types in parallel
            tasks = [
                _fetch_source_type_group(source_type, channels, semaphores[source_type])
                for source_type, channels in by_type.items()
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Collect results
            total_fetched = 0
            total_errors = 0
            for source_type, result in zip(by_type.keys(), results):
                if isinstance(result, Exception):
                    logger.error(f"Error in source type group {source_type}: {result}")
                    total_errors += len(by_type[source_type])
                else:
                    fetched, errors = result
                    total_fetched += fetched
                    total_errors += errors
                    if fetched > 0 or errors > 0:
                        logger.info(f"  {source_type}: {fetched} fetched, {errors} errors")

        if total_fetched > 0 or total_errors > 0:
            logger.info(f"Parallel fetch complete: {total_fetched} channels, {total_errors} errors")

        return {
            "due_channels": len(due_channels),
            "fetched": total_fetched,
            "errors": total_errors,
        }
    finally:
        _fetch_in_progress = False


async def retry_llm_processing(batch_size: int = 10) -> dict:
    """Retry LLM processing for items that were fetched during GPU unavailability.

    Prioritizes items by retry_priority:
    1. "high" - classifier marked as likely relevant
    2. "unknown" - no classifier result available
    3. "edge_case" - classifier was uncertain

    Args:
        batch_size: Maximum number of items to process per run

    Returns:
        Dict with processing statistics.
    """
    from sqlalchemy import case
    from sqlalchemy.orm import selectinload

    from models import Item
    from services.processor import create_processor_from_settings

    # Try to create LLM processor - if unavailable, skip this run
    try:
        processor = await create_processor_from_settings()
        if not processor:
            logger.debug("LLM processor not available for retry, skipping")
            return {"skipped": True, "reason": "llm_unavailable"}
    except Exception as e:
        logger.debug(f"LLM processor not available for retry: {e}")
        return {"skipped": True, "reason": str(e)}

    processed = 0
    errors = 0

    async with async_session_maker() as db:
        # Query items needing LLM processing, ordered by priority
        # Priority order: high > unknown > edge_case > low
        # "low" items are certainly irrelevant (confidence < 0.25)
        from database import json_extract_path

        retry_priority = json_extract_path(Item.metadata_, "retry_priority")
        priority_order = case(
            (retry_priority == "high", 1),
            (retry_priority == "unknown", 2),
            (retry_priority == "edge_case", 3),
            (retry_priority == "low", 4),
            else_=5,
        )
        # Skip "low" priority items (certainly irrelevant) by default
        # They can still be processed manually if needed
        query = (
            select(Item)
            .options(selectinload(Item.channel).selectinload(Channel.source))
            .where(
                Item.needs_llm_processing == True,  # noqa: E712
                retry_priority != "low",  # Skip certainly irrelevant items
            )
            .order_by(priority_order, Item.fetched_at.desc())
            .limit(batch_size)
        )
        result = await db.execute(query)
        items = result.scalars().all()

        if not items:
            return {"processed": 0, "errors": 0, "remaining": 0}

        logger.info(f"Retrying LLM processing for {len(items)} items")

        for item in items:
            try:
                source_name = item.channel.source.name if item.channel.source else "Unbekannt"
                analysis = await processor.analyze(item, source_name=source_name)

                # Update item with LLM results
                if analysis.get("summary"):
                    item.summary = analysis["summary"]
                if analysis.get("detailed_analysis"):
                    item.detailed_analysis = analysis["detailed_analysis"]

                # Use LLM priority directly (no remapping)
                llm_priority = analysis.get("priority") or analysis.get("priority_suggestion")
                if analysis.get("relevant") is False:
                    llm_priority = None

                from models import Priority
                if llm_priority == "high":
                    item.priority = Priority.HIGH
                    item.priority_score = max(item.priority_score, 90)
                elif llm_priority == "medium":
                    item.priority = Priority.MEDIUM
                    item.priority_score = max(item.priority_score, 70)
                elif llm_priority == "low":
                    item.priority = Priority.LOW
                    item.priority_score = max(item.priority_score, 40)
                else:
                    item.priority = Priority.NONE
                    item.priority_score = min(item.priority_score, 20)

                # Store analysis metadata
                item.metadata_["llm_analysis"] = {
                    "relevance_score": analysis.get("relevance_score", 0.5),
                    "priority_suggestion": llm_priority,
                    "assigned_ak": analysis.get("assigned_ak"),
                    "tags": analysis.get("tags", []),
                    "reasoning": analysis.get("reasoning"),
                    "retried_at": datetime.utcnow().isoformat(),
                }

                # Clear retry flag
                item.needs_llm_processing = False
                processed += 1

                logger.info(f"LLM retry success: {item.title[:40]} -> {llm_priority}")

            except Exception as e:
                logger.warning(f"LLM retry failed for item {item.id}: {e}")
                errors += 1

        await db.commit()

        # Count remaining items
        count_query = select(Item).where(Item.needs_llm_processing == True)  # noqa: E712
        remaining_result = await db.execute(count_query)
        remaining = len(remaining_result.scalars().all())

    if processed > 0:
        logger.info(f"LLM retry complete: {processed} processed, {errors} errors, {remaining} remaining")

    return {"processed": processed, "errors": errors, "remaining": remaining}


async def cleanup_old_items() -> dict:
    """Remove items based on per-priority retention settings.

    Uses housekeeping configuration from database with different retention
    periods per priority level.

    Returns:
        Dict with deletion statistics.
    """
    from sqlalchemy import delete, and_

    from models import Item, Priority, Setting

    logger.info("Starting cleanup of old items")

    async with async_session_maker() as db:
        # Load housekeeping config from database
        from api.admin import DEFAULT_HOUSEKEEPING_CONFIG

        result = await db.execute(
            select(Setting).where(Setting.key == "housekeeping")
        )
        setting = result.scalar_one_or_none()

        if setting and setting.value:
            config = dict(DEFAULT_HOUSEKEEPING_CONFIG)
            config.update(setting.value)
        else:
            config = dict(DEFAULT_HOUSEKEEPING_CONFIG)

        # Check if autopurge is enabled
        if not config.get("autopurge_enabled", False):
            logger.info("Autopurge disabled, skipping cleanup")
            return {"deleted": 0, "skipped": True, "reason": "autopurge_disabled"}

        exclude_starred = config.get("exclude_starred", True)
        now = datetime.utcnow()
        total_deleted = 0
        by_priority: dict[str, int] = {}

        # Delete per-priority with different retention periods
        for priority, days_key in [
            (Priority.HIGH, "retention_days_high"),
            (Priority.MEDIUM, "retention_days_medium"),
            (Priority.LOW, "retention_days_low"),
            (Priority.NONE, "retention_days_none"),
        ]:
            retention_days = config.get(days_key, 30)
            cutoff = now - timedelta(days=retention_days)

            # Build conditions
            conditions = [
                Item.priority == priority,
                Item.fetched_at < cutoff,
            ]
            if exclude_starred:
                conditions.append(Item.is_starred == False)  # noqa: E712

            stmt = delete(Item).where(and_(*conditions))
            result = await db.execute(stmt)
            count = result.rowcount

            if count > 0:
                by_priority[priority.value] = count
                total_deleted += count
                logger.info(
                    f"Deleted {count} {priority.value} priority items "
                    f"older than {retention_days} days"
                )

        await db.commit()

        logger.info(f"Cleanup completed: deleted {total_deleted} items ({by_priority})")
        return {
            "deleted": total_deleted,
            "by_priority": by_priority,
            "skipped": False,
        }


async def cleanup_old_events() -> int:
    """Remove item events older than 180 days.

    Returns:
        Number of events deleted.
    """
    from sqlalchemy import delete

    from models import ItemEvent

    logger.info("Starting cleanup of old item events")

    # 180 days retention for audit trail
    cutoff = datetime.utcnow() - timedelta(days=180)

    async with async_session_maker() as db:
        stmt = delete(ItemEvent).where(ItemEvent.timestamp < cutoff)
        result = await db.execute(stmt)
        await db.commit()

        deleted = result.rowcount
        if deleted > 0:
            logger.info(f"Deleted {deleted} old item events")
        return deleted


def start_scheduler() -> None:
    """Start the background scheduler."""
    from services.proxy_manager import proxy_manager

    # Smart fetch job - checks every minute for channels that are due
    scheduler.add_job(
        fetch_due_channels,
        trigger=IntervalTrigger(minutes=1),
        id="fetch_due_channels",
        name="Fetch channels that are due",
        replace_existing=True,
    )

    # Cleanup job (daily at 3 AM)
    scheduler.add_job(
        cleanup_old_items,
        trigger="cron",
        hour=3,
        minute=0,
        id="cleanup_old_items",
        name="Clean up old items",
        replace_existing=True,
    )

    # Cleanup old item events (daily at 3:15 AM, 180 days retention)
    scheduler.add_job(
        cleanup_old_events,
        trigger="cron",
        hour=3,
        minute=15,
        id="cleanup_old_events",
        name="Clean up old item events",
        replace_existing=True,
    )

    # Proxy refresh job (every 30 minutes)
    scheduler.add_job(
        proxy_manager.refresh_proxy_list,
        trigger=IntervalTrigger(minutes=30),
        id="refresh_proxies",
        name="Refresh proxy list",
        replace_existing=True,
    )

    # NOTE: LLM retry processing is now handled by the LLM worker (llm_worker.py)
    # which runs continuously and processes items with priority ordering.
    # The old 5-minute interval job has been removed for efficiency.

    scheduler.start()
    logger.info("Scheduler started")


def stop_scheduler() -> None:
    """Stop the background scheduler."""
    scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped")


def get_job_status() -> list[dict]:
    """Get status of all scheduled jobs."""
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
            "trigger": str(job.trigger),
        })
    return jobs


async def trigger_channel_fetch(channel_id: int) -> None:
    """Manually trigger a fetch for a specific channel."""
    scheduler.add_job(
        fetch_channel,
        args=[channel_id],
        id=f"manual_fetch_channel_{channel_id}",
        replace_existing=True,
    )


# Legacy aliases for backward compatibility during transition
fetch_source = fetch_channel
fetch_all_sources = fetch_all_channels
fetch_due_sources = fetch_due_channels
