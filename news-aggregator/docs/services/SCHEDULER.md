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
SCHEDULER_ENABLED=true       # Auto-start on boot
SCHEDULER_INTERVAL=5         # Minutes between fetch cycles
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
GET /api/admin/health
```

Returns scheduler status in `scheduler_running` field.

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
