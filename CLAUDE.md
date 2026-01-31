# Liga Hessen News Aggregator

A news aggregation system for Liga der Freien Wohlfahrtspflege Hessen that fetches, classifies, and displays relevant social policy news.

## Quick Links

| Topic | Document | Description |
|-------|----------|-------------|
| **Architecture** | [docs/architecture/OVERVIEW.md](news-aggregator/docs/architecture/OVERVIEW.md) | System components and data flow |
| **Services** | [docs/services/](news-aggregator/docs/services/) | Scheduler, LLM, Classifier details |
| **Connectors** | [docs/connectors/OVERVIEW.md](news-aggregator/docs/connectors/OVERVIEW.md) | News source connectors |
| **Operations** | [docs/operations/](news-aggregator/docs/operations/) | Troubleshooting and monitoring |
| **Classifier** | [relevance-tuner/CLAUDE.md](relevance-tuner/CLAUDE.md) | ML classifier training and API |
| **Analytics** | [docs/architecture/PROCESSING_ANALYTICS.md](news-aggregator/docs/architecture/PROCESSING_ANALYTICS.md) | Processing logs and model comparison |

## Environments

| Environment | Host | Compose File | Workers | Purpose |
|-------------|------|--------------|---------|---------|
| **Production** | docker-ai | `docker-compose.prod.yml` | Enabled | Live news aggregation |
| **QA/Dev** | gpu1 | `docker-compose.yml` | Disabled | Testing, development |
| **Training** | gpu1 | `docker-compose.training.yml` | N/A | Classifier retraining |

## Deployment

### Production (docker-ai)

```bash
ssh docker-ai
cd /home/kamienc/projects/liga-hessen-news-aggregator
git pull origin dev
cd news-aggregator
docker compose -f docker-compose.prod.yml up -d --build
```

### QA/Development (gpu1)

```bash
cd /home/kamienc/claude.ai/ligahessen/news-aggregator
git pull origin dev
docker compose up -d --build
```

### Quick Commands

```bash
# Container status
docker compose ps

# View logs
docker compose logs -f backend
docker compose logs -f frontend

# Restart without rebuild
docker compose restart

# Rebuild single service
docker compose up -d --build backend
```

## Project Structure

```
ligahessen/
├── news-aggregator/          # Main application
│   ├── backend/              # FastAPI backend
│   │   ├── api/              # REST endpoints
│   │   ├── services/         # Business logic
│   │   ├── connectors/       # News source connectors
│   │   └── models.py         # SQLAlchemy models
│   ├── frontend/             # Vue.js frontend
│   └── docs/                 # Detailed documentation
├── relevance-tuner/          # ML classifier
│   └── services/classifier-api/  # Classification API
└── CLAUDE.md                 # This file
```

## Key Components

### Backend Services
- **Scheduler** (`services/scheduler.py`) - Parallel channel fetching
- **Pipeline** (`services/pipeline.py`) - Item processing and deduplication
- **LLM Worker** (`services/llm_worker.py`) - Async LLM analysis
- **Classifier Worker** (`services/classifier_worker.py`) - ML classification

### Data Flow
1. **Fetch**: Scheduler triggers connectors to fetch from sources
2. **Process**: Pipeline deduplicates, classifies, and enriches items
3. **Store**: Items saved to PostgreSQL with embeddings in ChromaDB
4. **Display**: Frontend shows filtered, prioritized news feed

### Ports
| Service | Port | Description |
|---------|------|-------------|
| Backend | 8000 | FastAPI REST API |
| Frontend | 3000 | Vue.js dev server |
| Classifier | 8082 | ML classification API |
| PostgreSQL | 5432 | Database |

## Configuration

Environment variables in `.env`:
- `OLLAMA_BASE_URL` - LLM endpoint
- `OLLAMA_MODEL` - Model name (default: qwen3:14b-q8_0)
- `CLASSIFIER_API_URL` - Classifier endpoint
- `POSTGRES_*` - Database credentials

## Development Practices

### Testing and Data Fixes
- **Always use existing API endpoints** for testing, regression testing, and fixing data — not custom one-off scripts or direct DB queries
- This proves the actual code works end-to-end, not just an ad-hoc workaround
- Use the refetch endpoints, item update endpoints, or trigger scheduler fetches via API
- Only use direct DB access for investigation/diagnosis, not for applying fixes
- **If testing requires functionality that has no API endpoint**, ask the user if we should add one before resorting to scripts

### Useful API Endpoints for Testing & Operations

**Item management:**
```bash
# Get item by ID (includes duplicates, metadata, processing info)
curl -s http://localhost:8000/api/items/{id} | jq .

# Update item fields (priority, is_read, assigned_aks, summary, etc.)
curl -s -X PATCH http://localhost:8000/api/items/{id} \
  -H "Content-Type: application/json" -d '{"priority": "high"}'

# Re-fetch item (re-extract article content from URL)
curl -s -X POST http://localhost:8000/api/items/{id}/refetch

# Reprocess item through LLM
curl -s -X POST http://localhost:8000/api/items/{id}/reprocess

# List items with filters
curl -s "http://localhost:8000/api/items?page_size=10&priority=high&relevant_only=true&search=keyword"

# Items grouped by topic (with duplicates)
curl -s "http://localhost:8000/api/items/by-topic?days=7"

# Item event history
curl -s http://localhost:8000/api/items/{id}/history | jq .
```

**Worker controls:**
```bash
# Check system status (scheduler, workers, queue)
curl -s http://localhost:8000/api/admin/stats | jq .

# Pause/resume workers
curl -s -X POST http://localhost:8000/api/admin/classifier-worker/pause
curl -s -X POST http://localhost:8000/api/admin/classifier-worker/resume
curl -s -X POST http://localhost:8000/api/admin/llm-worker/pause
curl -s -X POST http://localhost:8000/api/admin/llm-worker/resume

# Trigger LLM retry processing
curl -s -X POST "http://localhost:8000/api/items/retry-queue/process?batch_size=10"

# Check retry queue stats
curl -s http://localhost:8000/api/items/retry-queue | jq .
```

**Classifier API (port 8082):**
```bash
# Health check (shows index counts)
curl -s http://localhost:8082/health | jq .

# Find duplicates for text
curl -s -X POST http://localhost:8082/find-duplicates \
  -H "Content-Type: application/json" \
  -d '{"title": "Article title", "content": "Content", "threshold": 0.75}'

# List indexed IDs
curl -s http://localhost:8082/ids | jq .count

# Delete items from vector store
curl -s -X POST http://localhost:8082/delete \
  -H "Content-Type: application/json" -d '{"ids": ["123", "456"]}'

# Sync duplicate store from search store
curl -s -X POST http://localhost:8082/sync-duplicate-store | jq .
```

**Analytics & debugging:**
```bash
# Processing analytics summary
curl -s http://localhost:8000/api/analytics/summary | jq .

# Items where classifier and LLM disagree
curl -s http://localhost:8000/api/analytics/disagreements | jq .

# Recent processing errors
curl -s http://localhost:8000/api/analytics/recent-errors | jq .

# Storage stats
curl -s http://localhost:8000/api/admin/storage | jq .
```

## Common Tasks

### Add a new source
1. Create source (organization) via API or frontend
2. Add channel with connector config
3. Test fetch manually, then enable scheduler

### Retrain classifier
See [relevance-tuner/CLAUDE.md](relevance-tuner/CLAUDE.md)

### Debug failed fetches
```bash
# Check scheduler logs
docker compose logs backend | grep -i error

# Check specific channel
curl http://localhost:8000/api/channels/{id}
```

### Update MOTD after commits

After each commit, ask the user if they want to announce it via MOTD.

**When to offer MOTD:**
- Bug fixes that users would have noticed (fewer errors, better reliability)
- New features visible in the UI
- Improvements to data quality (better deduplication, classification, etc.)

**Skip asking for:**
- Internal refactoring
- Performance optimizations users won't notice
- Developer tooling changes
- Documentation-only changes

**Writing guidelines:** See [docs/operations/MOTD.md](news-aggregator/docs/operations/MOTD.md) - target audience is a non-technical journalist.

**Set MOTD on production:**
```bash
ssh docker-ai 'curl -s -X POST http://localhost:8000/api/motd/admin \
  -H "Content-Type: application/json" \
  -d '"'"'{"message": "Your message here", "active": true}'"'"''
```

**Check current MOTD:**
```bash
ssh docker-ai "curl -s http://localhost:8000/api/motd" | jq .
```

## GPU1 Power Management Monitoring

gpu1 uses Wake-on-LAN (WoL) to wake for LLM processing during active hours (8:00-16:00).
docker-ai sends WoL packets when items need processing.

### Check gpu1 Wake/Shutdown History

```bash
# On gpu1: List all boot sessions with start/end times
journalctl --list-boots

# Filter to specific date
journalctl --list-boots | grep "2026-01-21"

# Output shows: IDX BOOT_ID START_TIME END_TIME
# Example:
#  -9 32d4b1fd... Wed 2026-01-21 08:00:24 CET Wed 2026-01-21 08:39:16 CET
#  -8 00a3a576... Wed 2026-01-21 11:33:14 CET Wed 2026-01-21 12:21:38 CET
```

### Check WoL and Power Events

```bash
# On gpu1: Check suspend-inhibitor activity (shows when system was active)
journalctl --since "2026-01-21 00:00:00" -u 'suspend-inhibitor*'

# Check for boot events
journalctl --since "2026-01-21 00:00:00" | grep 'Command line: BOOT_IMAGE'

# Check current uptime
uptime
```

### Check LLM Processing Activity (docker-ai)

```bash
# On docker-ai: Check gpu1 power manager logs
docker logs liga-news-backend 2>&1 | grep -iE 'gpu1|wol|wake|shutdown'

# Check if LLM was available (items with summaries vs pending)
docker exec liga-news-db psql -U liga -d liga_news -c "
SELECT
    DATE_TRUNC('hour', fetched_at) as hour,
    COUNT(*) as total,
    COUNT(*) FILTER (WHERE needs_llm_processing = true) as needs_llm,
    COUNT(*) FILTER (WHERE summary IS NOT NULL) as has_summary
FROM items
WHERE fetched_at >= CURRENT_DATE
GROUP BY DATE_TRUNC('hour', fetched_at)
ORDER BY hour;
"
```

### Active Hours Configuration

Configured in docker-ai backend via environment variables:
- `GPU1_ACTIVE_HOURS_START`: Start hour for WoL (default: 7)
- `GPU1_ACTIVE_HOURS_END`: End hour for WoL (default: 16)
- `GPU1_ACTIVE_WEEKDAYS_ONLY`: Only wake Mon-Fri (default: true)
- `GPU1_IDLE_TIMEOUT`: Seconds before auto-shutdown (default: 300 = 5 min)

Outside active hours (or on weekends when weekdays_only is true), items queue up with `needs_llm_processing=true` and are processed when gpu1 next wakes.

## Database

PostgreSQL 17 with async SQLAlchemy. Key tables:
- `sources` - Organizations (AWO, Caritas, etc.)
- `channels` - News feeds per source
- `items` - Individual news items
- `rules` - Keyword/semantic matching rules
- `item_processing_logs` - Processing analytics (see [PROCESSING_ANALYTICS.md](news-aggregator/docs/architecture/PROCESSING_ANALYTICS.md))

See [docs/architecture/DATABASE.md](news-aggregator/docs/architecture/DATABASE.md) for schema details.
