"""Background job scheduler using APScheduler."""

import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from config import settings
from database import async_session_maker
from models import Channel, Source

logger = logging.getLogger(__name__)

# Track if a fetch is currently running to avoid overlapping fetches
_fetch_in_progress = False

scheduler = AsyncIOScheduler()


async def fetch_all_channels(training_mode: bool = False) -> dict:
    """Fetch items from all enabled channels across all sources.

    Args:
        training_mode: If True, disables filtering for training data collection.

    Returns:
        Dict with fetch statistics.
    """
    mode_str = " (TRAINING MODE)" if training_mode else ""
    logger.info(f"Starting scheduled fetch for all channels{mode_str}")

    total_items = 0
    errors = 0
    channels_fetched = 0

    async with async_session_maker() as db:
        # Get all enabled channels where the parent source is also enabled
        query = (
            select(Channel)
            .join(Source)
            .where(
                Channel.enabled == True,  # noqa: E712
                Source.enabled == True,  # noqa: E712
            )
        )
        result = await db.execute(query)
        channels = result.scalars().all()

        for channel in channels:
            try:
                count = await fetch_channel(channel.id, training_mode=training_mode)
                total_items += count
                channels_fetched += 1
            except Exception as e:
                logger.error(f"Error fetching channel {channel.id}: {e}")
                errors += 1

    logger.info(f"Completed fetch for {channels_fetched} channels{mode_str}: {total_items} items, {errors} errors")
    return {
        "channels_fetched": channels_fetched,
        "items_collected": total_items,
        "errors": errors,
        "training_mode": training_mode,
    }


async def fetch_channel(channel_id: int, training_mode: bool = False) -> int:
    """Fetch items from a single channel.

    Args:
        channel_id: ID of channel to fetch
        training_mode: If True, disables filtering for training data collection

    Returns:
        Number of new items fetched.
    """
    from connectors import ConnectorRegistry
    from services.pipeline import Pipeline
    from services.processor import ItemProcessor, create_processor_from_settings

    mode_str = " (training)" if training_mode else ""
    logger.info(f"Fetching channel {channel_id}{mode_str}")

    # Try to create LLM processor (optional, skip in training mode for speed)
    processor: ItemProcessor | None = None
    if not training_mode:
        try:
            processor = await create_processor_from_settings()
            logger.debug("LLM processor initialized")
        except Exception as e:
            logger.warning(f"LLM processor not available: {e}")

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

        try:
            # Get connector and fetch items
            connector_class = ConnectorRegistry.get(channel.connector_type)
            if connector_class is None:
                raise ValueError(f"Unknown connector type: {channel.connector_type}")

            connector = connector_class()
            # Build config dict and convert to Pydantic model
            config_dict = {"url": channel.config.get("url", ""), **channel.config}
            config_model = connector_class.config_schema(**config_dict)
            raw_items = await connector.fetch(config_model)

            logger.info(f"Connector returned {len(raw_items)} raw items from channel {channel_id}")

            # Process through pipeline (with optional LLM processor)
            pipeline = Pipeline(db, processor=processor, training_mode=training_mode)
            new_items = await pipeline.process(raw_items, channel)

            channel.last_fetch_at = datetime.utcnow()
            channel.last_error = None
            await db.commit()

            logger.info(f"Fetched {len(new_items)} new items from channel {channel_id}{mode_str}")
            return len(new_items)

        except Exception as e:
            logger.error(f"Error fetching channel {channel_id}: {e}")
            channel.last_error = str(e)
            await db.commit()
            raise


async def fetch_due_channels() -> dict:
    """Fetch channels that are past their individual fetch interval.

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
        fetched = 0
        errors = 0

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
                    due_channels.append((channel, None))
                else:
                    due_time = channel.last_fetch_at + timedelta(minutes=channel.fetch_interval_minutes)
                    if due_time < now:
                        due_channels.append((channel, channel.last_fetch_at))

            # Sort by last_fetch_at (oldest first, NULL first)
            due_channels.sort(key=lambda x: x[1] or datetime.min)

            if due_channels:
                logger.info(f"Found {len(due_channels)} channels due for fetching")

            for channel, _ in due_channels:
                try:
                    await fetch_channel(channel.id)
                    fetched += 1
                except Exception as e:
                    logger.error(f"Error fetching channel {channel.id} ({channel.source.name}/{channel.connector_type}): {e}")
                    errors += 1

        if fetched > 0 or errors > 0:
            logger.info(f"Fetched {fetched} due channels, {errors} errors")

        return {
            "due_channels": len(due_channels),
            "fetched": fetched,
            "errors": errors,
        }
    finally:
        _fetch_in_progress = False


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
