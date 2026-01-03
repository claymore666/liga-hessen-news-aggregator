"""Background job scheduler using APScheduler."""

import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from config import settings
from database import async_session_maker
from models import Source

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def fetch_all_sources() -> None:
    """Fetch items from all enabled sources."""
    logger.info("Starting scheduled fetch for all sources")

    async with async_session_maker() as db:
        query = select(Source).where(Source.enabled == True)  # noqa: E712
        result = await db.execute(query)
        sources = result.scalars().all()

        for source in sources:
            try:
                await fetch_source(source.id)
            except Exception as e:
                logger.error(f"Error fetching source {source.id}: {e}")
                source.last_error = str(e)

        await db.commit()

    logger.info(f"Completed fetch for {len(sources)} sources")


async def fetch_source(source_id: int) -> int:
    """Fetch items from a single source.

    Returns:
        Number of new items fetched.
    """
    from connectors import ConnectorRegistry
    from services.pipeline import Pipeline

    logger.info(f"Fetching source {source_id}")

    async with async_session_maker() as db:
        query = select(Source).where(Source.id == source_id)
        result = await db.execute(query)
        source = result.scalar_one_or_none()

        if source is None:
            logger.warning(f"Source {source_id} not found")
            return 0

        if not source.enabled:
            logger.info(f"Source {source_id} is disabled, skipping")
            return 0

        try:
            # Get connector and fetch items
            connector_class = ConnectorRegistry.get(source.connector_type)
            if connector_class is None:
                raise ValueError(f"Unknown connector type: {source.connector_type}")

            connector = connector_class()
            # Build config dict and convert to Pydantic model
            config_dict = {"url": source.config.get("url", ""), **source.config}
            config_model = connector_class.config_schema(**config_dict)
            raw_items = await connector.fetch(config_model)

            logger.info(f"Connector returned {len(raw_items)} raw items from source {source_id}")

            # Process through pipeline
            pipeline = Pipeline(db)
            new_items = await pipeline.process(raw_items, source)

            source.last_fetch_at = datetime.utcnow()
            source.last_error = None
            await db.commit()

            logger.info(f"Fetched {len(new_items)} new items from source {source_id}")
            return len(new_items)

        except Exception as e:
            logger.error(f"Error fetching source {source_id}: {e}")
            source.last_error = str(e)
            await db.commit()
            raise


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
    # Main fetch job
    scheduler.add_job(
        fetch_all_sources,
        trigger=IntervalTrigger(minutes=settings.fetch_interval_minutes),
        id="fetch_all_sources",
        name="Fetch all enabled sources",
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


async def trigger_source_fetch(source_id: int) -> None:
    """Manually trigger a fetch for a specific source."""
    scheduler.add_job(
        fetch_source,
        args=[source_id],
        id=f"manual_fetch_{source_id}",
        replace_existing=True,
    )
