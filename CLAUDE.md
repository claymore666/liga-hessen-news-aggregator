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

## Deployment (gpu1)

The system runs on gpu1 via Docker Compose:

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
