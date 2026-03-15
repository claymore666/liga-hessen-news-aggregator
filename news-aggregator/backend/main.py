"""Main FastAPI application entry point."""

import logging
import os
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

from config import settings
from database import init_db
from services.scheduler import start_scheduler, stop_scheduler
from services.proxy_manager import proxy_manager
from services.llm_worker import start_worker, stop_worker
from services.classifier_worker import (
    start_classifier_worker,
    stop_classifier_worker,
)
from services.dedup_worker import (
    start_dedup_worker,
    stop_dedup_worker,
)

def _write_msmtp_config() -> None:
    """Write /etc/msmtprc from settings so sendmail works inside the container."""
    try:
        with open("/etc/msmtprc", "w") as f:
            f.write(
                f"account default\n"
                f"host {settings.smtp_host}\n"
                f"port {settings.smtp_port}\n"
                f"from {settings.smtp_from}\n"
                f"auto_from off\n"
                f"syslog on\n"
            )
        os.chmod("/etc/msmtprc", 0o644)
    except PermissionError:
        logging.debug("Could not write /etc/msmtprc (not root), skipping")
    except Exception as e:
        logging.warning(f"Failed to write msmtp config: {e}")


LEADER_LOCK_FILE = "/tmp/liga-worker-leader"

# File descriptor for leader lock — kept open for the lifetime of the leader process.
# fcntl.flock auto-releases when the process dies, avoiding stale lock files.
_leader_lock_fd: int | None = None


def _try_become_leader() -> bool:
    """Try to become the worker leader using flock.

    Only the leader process runs background workers (scheduler, LLM, classifier).
    All processes serve API requests.

    Uses fcntl.flock which auto-releases on process death, preventing stale locks
    when the leader crashes (OOM, unhandled exception).
    """
    import fcntl

    global _leader_lock_fd
    fd = -1
    try:
        fd = os.open(LEADER_LOCK_FILE, os.O_CREAT | os.O_WRONLY)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.write(fd, str(os.getpid()).encode())
        os.fsync(fd)
        _leader_lock_fd = fd  # Keep fd open — lock held until process exits
        return True
    except (OSError, BlockingIOError):
        if fd >= 0:
            try:
                os.close(fd)
            except Exception:
                pass
        return False


def _release_leader() -> None:
    """Release the leader lock."""
    import fcntl

    global _leader_lock_fd
    if _leader_lock_fd is not None:
        try:
            fcntl.flock(_leader_lock_fd, fcntl.LOCK_UN)
            os.close(_leader_lock_fd)
        except Exception:
            pass
        _leader_lock_fd = None
    try:
        os.unlink(LEADER_LOCK_FILE)
    except FileNotFoundError:
        pass


async def run_migrations() -> None:
    """Run database migrations for existing databases."""
    from database import engine
    from sqlalchemy import inspect, text

    async with engine.begin() as conn:
        # Check if items table exists
        def check_table_exists(sync_conn):
            inspector = inspect(sync_conn)
            return "items" in inspector.get_table_names()

        has_items = await conn.run_sync(check_table_exists)
        if not has_items:
            return  # No items table yet, init_db will create it

        # Get existing columns
        def get_columns(sync_conn):
            inspector = inspect(sync_conn)
            return [col["name"] for col in inspector.get_columns("items")]

        columns = await conn.run_sync(get_columns)

        # Add missing columns
        migrations = [
            ("is_archived", "ALTER TABLE items ADD COLUMN is_archived BOOLEAN DEFAULT FALSE"),
            ("assigned_ak", "ALTER TABLE items ADD COLUMN assigned_ak VARCHAR(10)"),
            ("is_manually_reviewed", "ALTER TABLE items ADD COLUMN is_manually_reviewed BOOLEAN DEFAULT FALSE"),
            ("reviewed_at", "ALTER TABLE items ADD COLUMN reviewed_at TIMESTAMP"),
            ("assigned_aks", "ALTER TABLE items ADD COLUMN assigned_aks JSON DEFAULT '[]'"),
        ]

        for column_name, sql in migrations:
            if column_name not in columns:
                await conn.execute(text(sql))
                logging.info(f"Migration: Added '{column_name}' column to items table")

        # Migrate assigned_ak from metadata to column for existing items
        if "assigned_ak" not in columns:
            await conn.execute(text("""
                UPDATE items
                SET assigned_ak = metadata #>> '{llm_analysis,assigned_ak}'
                WHERE metadata #>> '{llm_analysis,assigned_ak}' IS NOT NULL
                  AND assigned_ak IS NULL
            """))

        # Migrate assigned_ak (single) to assigned_aks (array) for existing items
        if "assigned_aks" not in columns:
            logging.info("Migration: Converting assigned_ak to assigned_aks array")
            await conn.execute(text("""
                UPDATE items
                SET assigned_aks = jsonb_build_array(assigned_ak)
                WHERE assigned_ak IS NOT NULL
                  AND (assigned_aks IS NULL OR assigned_aks::text = '[]')
            """))
            logging.info("Migration: assigned_ak values converted to assigned_aks arrays")

        # Migrate priority values: critical→high, high→medium, medium→low, low→none
        # Check if any items still have old priority values ('critical' only exists in old system)
        result = await conn.execute(text(
            "SELECT COUNT(*) FROM items WHERE priority = 'critical'"
        ))
        needs_migration = result.scalar()

        if needs_migration and needs_migration > 0:
            logging.info("Migration: Converting priority values (critical→high, high→medium, medium→low, low→none)")
            # Use temp values to avoid conflicts during cascading migration
            await conn.execute(text("UPDATE items SET priority = '_high' WHERE priority = 'critical'"))
            await conn.execute(text("UPDATE items SET priority = '_medium' WHERE priority = 'high'"))
            await conn.execute(text("UPDATE items SET priority = '_low' WHERE priority = 'medium'"))
            await conn.execute(text("UPDATE items SET priority = 'none' WHERE priority = 'low'"))
            # Now rename temp values to final values
            await conn.execute(text("UPDATE items SET priority = 'high' WHERE priority = '_high'"))
            await conn.execute(text("UPDATE items SET priority = 'medium' WHERE priority = '_medium'"))
            await conn.execute(text("UPDATE items SET priority = 'low' WHERE priority = '_low'"))
            logging.info("Migration: Priority values converted successfully")

        # One-time migration: Clear pre_filter for items that were never LLM processed
        # This allows the classifier worker to reclassify them with the new priority logic
        result = await conn.execute(text(
            "SELECT value FROM settings WHERE key = 'classifier_reclassify_migration_done'"
        ))
        migration_done = result.scalar()

        if not migration_done:
            # Count items that need reclassification (no summary = not LLM processed)
            result = await conn.execute(text(
                "SELECT COUNT(*) FROM items WHERE (summary IS NULL OR summary = '')"
            ))
            items_to_reclassify = result.scalar() or 0

            if items_to_reclassify > 0:
                logging.info(f"Migration: Marking {items_to_reclassify} non-LLM-processed items for reclassification")
                # Clear pre_filter metadata so classifier worker will reprocess them
                await conn.execute(text("""
                    UPDATE items
                    SET metadata = metadata #- '{pre_filter}'
                    WHERE (summary IS NULL OR summary = '')
                      AND metadata #>> '{pre_filter}' IS NOT NULL
                """))
                logging.info("Migration: Items marked for reclassification")

            # Mark migration as complete
            await conn.execute(text("""
                INSERT INTO settings (key, value, description)
                VALUES ('classifier_reclassify_migration_done', '"true"',
                        'One-time migration to reclassify non-LLM-processed items with new priority logic')
                ON CONFLICT (key) DO UPDATE SET value = '"true"'
            """))
            logging.info("Migration: Classifier reclassify migration marked complete")

    # --- Index migrations (run outside transaction for CONCURRENTLY support) ---
    async with engine.connect() as conn:
        await conn.execution_options(isolation_level="AUTOCOMMIT")

        # Convert metadata column from JSON to JSONB if needed
        result = await conn.execute(text(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = 'items' AND column_name = 'metadata'"
        ))
        row = result.first()
        if row and row[0] == 'json':
            logging.info("Migration: Converting items.metadata from JSON to JSONB")
            await conn.execute(text(
                "ALTER TABLE items ALTER COLUMN metadata TYPE JSONB USING metadata::jsonb"
            ))
            logging.info("Migration: items.metadata converted to JSONB")

        # Enable pg_trgm extension for trigram indexes
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))

        # Create indexes (CONCURRENTLY requires AUTOCOMMIT)
        indexes = [
            ("ix_items_feed",
             "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_items_feed "
             "ON items (published_at DESC, priority) "
             "WHERE similar_to_id IS NULL AND is_archived = false"),
            ("ix_items_fetched_at",
             "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_items_fetched_at "
             "ON items (fetched_at DESC)"),
            ("ix_items_title_trgm",
             "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_items_title_trgm "
             "ON items USING GIN (title gin_trgm_ops)"),
            ("ix_items_needs_llm_queue",
             "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_items_needs_llm_queue "
             "ON items (fetched_at DESC) WHERE needs_llm_processing = true"),
            ("ix_items_unread_priority",
             "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_items_unread_priority "
             "ON items (priority) WHERE is_read = false"),
            ("ix_items_starred",
             "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_items_starred "
             "ON items (id) WHERE is_starred = true"),
            ("ix_items_metadata_gin",
             "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_items_metadata_gin "
             "ON items USING GIN (metadata jsonb_path_ops)"),
        ]

        for name, sql in indexes:
            try:
                await conn.execute(text(sql))
            except Exception as e:
                # Index may already exist or be building concurrently
                logging.warning(f"Index {name}: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan handler for startup/shutdown."""
    # Set up in-memory log buffer for web UI
    from api.admin.logs import setup_memory_logging, start_log_writer, stop_log_writer
    setup_memory_logging()

    # Initialize Redis (shared cross-worker state)
    from services.redis_client import init_redis, close_redis
    await init_redis()
    await start_log_writer()

    # Determine if this worker should run background tasks
    is_leader = _try_become_leader()
    if is_leader:
        logging.info(f"Worker {os.getpid()}: elected as leader, will run background tasks")
    else:
        logging.info(f"Worker {os.getpid()}: API-only mode (another worker is leader)")

    # Warn if admin API key is not configured
    if not settings.admin_api_key:
        logging.warning(
            "ADMIN_API_KEY is not set — admin endpoints are accessible without authentication. "
            "Set ADMIN_API_KEY in .env for production use."
        )

    # Configure msmtp (sendmail) relay from settings
    _write_msmtp_config()

    # Startup - all workers init DB, only leader runs migrations
    await init_db()
    if is_leader:
        await run_migrations()

    # Only the leader runs background workers
    if is_leader:
        from services.worker_status import write_state

        if settings.scheduler_enabled:
            start_scheduler()
            logging.info("Scheduler enabled and started")
        else:
            await write_state("scheduler", running=False)
            logging.info("Scheduler disabled via SCHEDULER_ENABLED=false")

        proxy_manager.start_background_search()

        if settings.llm_worker_enabled:
            await start_worker(
                batch_size=10,
                idle_sleep=30.0,
                backlog_batch_size=50,
            )
            logging.info("LLM worker enabled and started")
        else:
            await write_state("llm", running=False)
            logging.info("LLM worker disabled via LLM_WORKER_ENABLED=false")

        if settings.classifier_worker_enabled:
            await start_classifier_worker(
                batch_size=50,
                idle_sleep=60.0,
            )
            logging.info("Classifier worker enabled and started")
        else:
            await write_state("classifier", running=False)
            logging.info("Classifier worker disabled via CLASSIFIER_WORKER_ENABLED=false")

        if settings.dedup_worker_enabled:
            await start_dedup_worker(
                batch_size=50,
                idle_sleep=30.0,
            )
            logging.info("Dedup worker enabled and started")
        else:
            await write_state("dedup", running=False)
            logging.info("Dedup worker disabled via DEDUP_WORKER_ENABLED=false")

    yield

    # Stop log writer (all workers)
    await stop_log_writer()

    # Shutdown - only leader stops background workers
    if is_leader:
        logging.info("Leader shutting down background workers...")
        if settings.scheduler_enabled:
            stop_scheduler()
        if settings.dedup_worker_enabled:
            await stop_dedup_worker()
        if settings.classifier_worker_enabled:
            await stop_classifier_worker()
        if settings.llm_worker_enabled:
            await stop_worker()
        await proxy_manager.stop_background_search()

        from services.browser_pool import browser_pool
        await browser_pool.shutdown()

        # Mark all workers as stopped in DB
        from services.worker_status import write_state
        for name in ("scheduler", "llm", "classifier", "dedup"):
            await write_state(name, running=False)

        _release_leader()
        logging.info("Leader shutdown complete")
    else:
        logging.info(f"Worker {os.getpid()} shutdown complete")

    # Close Redis (all workers)
    await close_redis()


API_DESCRIPTION = """
## Liga Hessen News Aggregator API

This API powers the daily briefing system for the **Liga der Freien Wohlfahrtspflege Hessen** -
an umbrella organization of 6 welfare associations (AWO, Caritas, Diakonie, DRK, Paritätischer, Jüdische Gemeinden).

### Core Concepts

- **Sources**: Organizations/accounts to monitor (e.g., "Hessischer Landtag", "PRO ASYL")
- **Channels**: Specific feeds within a source (e.g., RSS feed, X/Twitter profile, LinkedIn page)
- **Items**: Individual news articles/posts fetched from channels
- **Rules**: Keyword or semantic rules for priority scoring

### Connector Types

| Type | Description |
|------|-------------|
| `rss` | RSS/Atom feeds (including Google Alerts) |
| `html` | Web scraping with CSS selectors |
| `x_scraper` | X/Twitter profiles (Playwright-based) |
| `mastodon` | Mastodon profiles |
| `bluesky` | Bluesky feeds |
| `telegram` | Public Telegram channels |
| `linkedin` | LinkedIn company pages (RSSHub or Playwright) |
| `instagram_scraper` | Instagram profiles |
| `pdf` | PDF document extraction |

### Priority Levels

- `critical`: Immediate action required (budget cuts, legislative deadlines)
- `high`: Important, needs attention within days
- `medium`: Relevant, monitor situation
- `low`: Background information

### Arbeitskreise (Working Groups)

- `AK1`: Grundsatz und Sozialpolitik (General & Social Policy)
- `AK2`: Migration und Flucht (Migration & Refugees)
- `AK3`: Gesundheit, Pflege und Senioren (Health, Care & Seniors)
- `AK4`: Eingliederungshilfe (Integration Assistance)
- `AK5`: Kinder, Jugend, Frauen und Familie (Children, Youth, Women & Family)
- `QAG`: Querschnitt (Cross-cutting: Digitalization, Climate, Housing)
"""

app = FastAPI(
    title=settings.app_name,
    description=API_DESCRIPTION,
    version="0.1.0",
    lifespan=lifespan,
    openapi_tags=[
        {"name": "items", "description": "News items (articles, posts) fetched from sources"},
        {"name": "sources", "description": "Organizations/accounts to monitor"},
        {"name": "connectors", "description": "Available connector types and their configuration"},
        {"name": "rules", "description": "Priority scoring rules (keyword and semantic)"},
        {"name": "stats", "description": "Dashboard statistics and analytics"},
        {"name": "scheduler", "description": "Background fetch scheduler control"},
        {"name": "llm", "description": "LLM processing status and reprocessing"},
        {"name": "admin", "description": "Administrative operations"},
        {"name": "email", "description": "Email digest configuration"},
        {"name": "proxies", "description": "Proxy management for scraping"},
        {"name": "analytics", "description": "Processing analytics and model performance"},
    ],
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "healthy"}


# Import and include routers
from api import items, sources, connectors, rules, stats, email, proxies, llm, admin, config, analytics, motd, prompts, digest  # noqa: E402
from api import scheduler as scheduler_api  # noqa: E402

app.include_router(items.router, prefix=settings.api_prefix, tags=["items"])
app.include_router(sources.router, prefix=settings.api_prefix, tags=["sources"])
app.include_router(connectors.router, prefix=settings.api_prefix, tags=["connectors"])
app.include_router(rules.router, prefix=settings.api_prefix, tags=["rules"])
app.include_router(stats.router, prefix=settings.api_prefix, tags=["stats"])
app.include_router(email.router, prefix=settings.api_prefix, tags=["email"])
app.include_router(proxies.router, prefix=settings.api_prefix, tags=["proxies"])
app.include_router(llm.router, prefix=settings.api_prefix, tags=["llm"])
app.include_router(admin.router, prefix=settings.api_prefix, tags=["admin"])
app.include_router(config.router, prefix=settings.api_prefix, tags=["admin"])
app.include_router(scheduler_api.router, prefix=settings.api_prefix, tags=["scheduler"])
app.include_router(analytics.router, prefix=settings.api_prefix, tags=["analytics"])
app.include_router(motd.router, prefix=settings.api_prefix, tags=["motd"])
app.include_router(prompts.router, prefix=settings.api_prefix, tags=["prompts"])
app.include_router(digest.router, prefix=settings.api_prefix, tags=["digest"])
