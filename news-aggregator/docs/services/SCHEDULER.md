# Scheduler Service

## Overview

The scheduler manages periodic fetching of news channels. It runs as a background task within the FastAPI application.

**File**: `backend/services/scheduler.py`

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    APScheduler                          │
│  ┌─────────────────┐  ┌─────────────────────────────┐  │
│  │  fetch_job      │  │  cleanup_job                │  │
│  │  (interval)     │  │  (daily)                    │  │
│  └────────┬────────┘  └─────────────────────────────┘  │
└───────────┼─────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────┐
│              fetch_due_channels()                        │
│  ┌─────────────────────────────────────────────────┐    │
│  │ Group channels by source_type                    │    │
│  │ (rss, x_scraper, mastodon, etc.)                │    │
│  └─────────────────────────────────────────────────┘    │
│                         │                               │
│    ┌────────────────────┼────────────────────┐         │
│    ▼                    ▼                    ▼         │
│ ┌──────────┐      ┌──────────┐        ┌──────────┐    │
│ │RSS Group │      │X Group   │        │Social    │    │
│ │Sem: 10   │      │Sem: 2    │        │Sem: 5    │    │
│ └──────────┘      └──────────┘        └──────────┘    │
└─────────────────────────────────────────────────────────┘
```

## Concurrency Limits

Each source type has its own semaphore to prevent overwhelming external services:

```python
SOURCE_TYPE_LIMITS = {
    "x_scraper": 2,          # Browser-based, slow (~36s)
    "instagram_scraper": 2,  # Browser-based
    "linkedin": 2,           # Scraping, rate limited
    "rss": 10,               # Lightweight HTTP
    "mastodon": 5,           # API-based
    "twitter": 5,            # RSS-based
    "bluesky": 5,            # API-based
    "telegram": 5,           # API-based
    "html": 5,               # HTTP scraping
    "pdf": 3,                # Heavy processing
    "google_alerts": 5,      # RSS-based
    "instagram": 5,          # Proxy services
}
```

## Fetch Scheduling

### Per-Channel Intervals

Each channel has its own `fetch_interval_minutes`:

```python
# Check if channel is due for fetch
now = datetime.utcnow()
if channel.last_fetched_at:
    next_fetch = channel.last_fetched_at + timedelta(
        minutes=channel.fetch_interval_minutes
    )
    if now < next_fetch:
        continue  # Not due yet
```

### Fetch Process

```python
async def fetch_channel(channel_id: int) -> int:
    """Fetch a single channel and process items."""

    # 1. Get channel with connector
    channel = await db.get(Channel, channel_id)
    connector = ConnectorRegistry.get(channel.connector_type)

    # 2. Fetch raw items
    raw_items = await connector.fetch(channel.config)

    # 3. Process through pipeline
    new_count = await pipeline.process_items(raw_items, channel)

    # 4. Update last_fetched_at
    channel.last_fetched_at = datetime.utcnow()
    channel.last_error = None

    return new_count
```

## API Endpoints

### Start Scheduler
```http
POST /api/scheduler/start
```

### Stop Scheduler
```http
POST /api/scheduler/stop
```

### Get Status
```http
GET /api/scheduler/status
```
Returns:
```json
{
  "running": true,
  "interval_minutes": 5,
  "next_run": "2024-01-15T10:30:00Z",
  "jobs": [
    {"id": "fetch_job", "next_run": "..."},
    {"id": "cleanup_job", "next_run": "..."}
  ]
}
```

### Set Interval
```http
PUT /api/scheduler/interval
{
  "interval_minutes": 10
}
```

### Manual Fetch
```http
POST /api/sources/{source_id}/fetch-all
POST /api/channels/{channel_id}/fetch
```

## Error Handling

Failed fetches are logged and stored:

```python
except Exception as e:
    logger.error(f"Error fetching channel {channel_id}: {e}")
    channel.last_error = str(e)
    await db.commit()
```

Errors don't stop the scheduler - other channels continue.

### Timeouts and Abandoned Tasks

Every channel fetch runs under a per-connector timeout (`CHANNEL_FETCH_TIMEOUTS`):

| Connector | Timeout | Why |
|-----------|---------|-----|
| `x_scraper`, `instagram_scraper` | 300 s | Browser-based, follows links |
| `linkedin` | 180 s | Browser-based |
| `rss` | 180 s | Heavy feeds extract every article |
| `html`, `pdf` | 120 s | JS rendering / large downloads |
| `mastodon`, `bluesky`, `telegram` | 90 s | Light API fetch, but `follow_links` extracts every linked article (httpx → Wayback → Playwright fallback). At 20 s these channels timed out on every cycle from 2026-09-14 on. |
| `google_alerts`, others | 30 s / default | Plain feeds |

The Playwright fallback of the article extractor only waits 5 s for a
browser-pool slot (`get_browser(slot_timeout=5.0)`); when the pool is busy with
x_scrapers it gives up instead of blocking the whole channel fetch.

Since #187 the timeout is enforced with an explicit
task plus `asyncio.wait`, **not** `asyncio.wait_for()`: `wait_for()` cancels the
task and then waits for it without any bound, so a cleanup that never finishes
(a Playwright `page.close()` Chromium never answers) blocked `fetch_due_channels`
and therefore all ingestion for two days in September 2026.

The sequence on timeout is now:

1. The channel is recorded as failed (circuit breaker) and the task is cancelled.
2. The scheduler waits up to `FETCH_CLEANUP_GRACE_SECONDS` (default 30) for the
   task's cleanup to finish.
3. If it still has not finished, the task is **abandoned** (left running detached,
   logged as `Cleanup of channel … did not finish within 30s grace period`) and
   the cycle continues.
4. At the end of a cycle with abandoned tasks the scheduler calls
   `browser_pool.hard_reset()`, which kills the Playwright driver so every
   pending call fails, the abandoned tasks finish, and the next cycle starts with
   a clean pool.

Abandoned tasks are counted in the cycle stats (`abandoned_tasks_total`,
`abandoned_tasks_pending`, `hard_resets`).

## Training Mode

Special mode for collecting training data:

```python
POST /api/sources/{source_id}/fetch-all?training_mode=true
```

In training mode:
- Items are stored without LLM processing
- `needs_llm_processing = False`
- Used for labeling before classifier training

## Housekeeping

Daily cleanup job removes old items based on retention settings:

```python
async def cleanup_old_items():
    """Remove items older than retention period."""
    config = await get_housekeeping_config()

    if not config["autopurge_enabled"]:
        return

    # Delete by priority with different retention
    for priority, days in config["retention_days"].items():
        cutoff = datetime.utcnow() - timedelta(days=days)
        await db.execute(
            delete(Item)
            .where(Item.priority == priority)
            .where(Item.published_at < cutoff)
        )
```

## Configuration

### Environment Variables

```bash
SCHEDULER_ENABLED=true                 # Auto-start on boot
SCHEDULER_INTERVAL=5                   # Minutes between fetch cycles
FETCH_CLEANUP_GRACE_SECONDS=30         # Cleanup budget after a channel timeout (#187)
SCHEDULER_FRESHNESS_MAX_MINUTES=15     # /health → 503 when no fetch activity for this long; 0 disables
```

### Database Settings

Settings stored in `settings` table:
- `scheduler_interval` - Fetch cycle interval
- `housekeeping` - Cleanup configuration

## Monitoring

### Logs

```bash
docker compose logs backend | grep -i scheduler
```

### Health Check

```http
GET /health
GET /api/admin/health
GET /api/admin/stats
```

`scheduler_running` only says that APScheduler is up; it stayed `true` during the
#187 hang. The **ingestion freshness** check is the signal that matters: the
leader worker records `last_activity_at` whenever a fetch cycle starts, a channel
fetch finishes, or a cycle completes, and publishes it (with the rest of the
cycle stats and the browser pool health) to Redis after every cycle and every 30 s.

- `GET /health` (used by the Docker healthcheck) returns **503** with
  `{"status": "unhealthy", "reason": "ingestion_stale", ...}` when the scheduler
  is enabled and the last activity is older than `SCHEDULER_FRESHNESS_MAX_MINUTES`.
  The container then shows as `unhealthy` in `docker ps`. Note that
  `restart: unless-stopped` does not restart unhealthy containers by itself.
- `GET /api/admin/health` reports `status: degraded`, plus `ingestion`
  (`stale`, `age_seconds`, `last_activity_at`), `scheduler_cycle` and
  `browser_pool`.
- `GET /api/admin/stats` exposes the same under `scheduler.cycle` and
  `scheduler.ingestion`.

Unknown age (nothing recorded yet, e.g. right after a fresh deploy) is never
reported as stale. The default of 15 minutes leaves room for the longest single
channel fetch (300 s timeout + 30 s grace).

## Common Issues

### Scheduler not starting
- Check `SCHEDULER_ENABLED` env var
- Check for startup errors in logs

### Channels not fetching
- Verify channel is enabled
- Check `last_error` field
- Verify `fetch_interval_minutes`

### Rate limiting
- Reduce `SOURCE_TYPE_LIMITS` for affected type
- Increase channel `fetch_interval_minutes`
