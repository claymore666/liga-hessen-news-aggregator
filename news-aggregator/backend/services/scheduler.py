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
SOURCE_TYPE_LIMITS = {
    "x_scraper": 2,  # Heavy browser + rate limits
    "instagram_scraper": 2,
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

        # Create semaphores per source type
        semaphores = {
            source_type: asyncio.Semaphore(SOURCE_TYPE_LIMITS.get(source_type, 3))
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

    # Phase 3: Database writes - process and store items (serialized)
    async with db_write_lock:
        async with async_session_maker() as db:
            # Re-fetch channel within this session
            channel = await db.get(Channel, channel_id)
            if channel is None:
                return 0

            try:
                # Process through pipeline (includes database writes)
                pipeline = Pipeline(db, processor=processor, relevance_filter=relevance_filter, training_mode=training_mode)
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
                except Exception:
                    pass
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

            # Create semaphores per source type
            semaphores = {
                source_type: asyncio.Semaphore(SOURCE_TYPE_LIMITS.get(source_type, 3))
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
        # Priority order: high > unknown > edge_case
        # Use SQLite json_extract function for compatibility
        from sqlalchemy import func

        # Priority order: high > unknown > edge_case > low
        # "low" items are certainly irrelevant (confidence < 0.25)
        retry_priority = func.json_extract(Item.metadata_, "$.retry_priority")
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

                # Update priority based on LLM
                llm_priority = analysis.get("priority") or analysis.get("priority_suggestion")
                if analysis.get("relevant") is False:
                    llm_priority = "low"

                from models import Priority
                if llm_priority == "critical":
                    item.priority = Priority.HIGH
                    item.priority_score = max(item.priority_score, 90)
                elif llm_priority == "high":
                    item.priority = Priority.MEDIUM
                    item.priority_score = max(item.priority_score, 70)
                elif llm_priority == "medium":
                    item.priority = Priority.LOW
                elif llm_priority:
                    item.priority = Priority.NONE
                    item.priority_score = min(item.priority_score, 40)

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


async def cleanup_old_items() -> int:
    """Remove items older than configured retention period.

    Returns:
        Number of items deleted.
    """
    from datetime import timedelta

    from sqlalchemy import delete

    from models import Item

    logger.info("Starting cleanup of old items")

    cutoff = datetime.utcnow() - timedelta(days=settings.cleanup_days)

    async with async_session_maker() as db:
        # Don't delete starred items
        stmt = delete(Item).where(
            Item.fetched_at < cutoff,
            Item.is_starred == False,  # noqa: E712
        )
        result = await db.execute(stmt)
        await db.commit()

        deleted = result.rowcount
        logger.info(f"Deleted {deleted} old items")
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

    # Proxy refresh job (every 30 minutes)
    scheduler.add_job(
        proxy_manager.refresh_proxy_list,
        trigger=IntervalTrigger(minutes=30),
        id="refresh_proxies",
        name="Refresh proxy list",
        replace_existing=True,
    )

    # LLM retry job (every 5 minutes) - processes items that missed LLM during GPU downtime
    scheduler.add_job(
        retry_llm_processing,
        trigger=IntervalTrigger(minutes=5),
        id="retry_llm_processing",
        name="Retry LLM processing for missed items",
        replace_existing=True,
    )

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
