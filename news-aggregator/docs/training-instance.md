# Training Data Collection Instance

## Purpose

Run a separate instance that collects raw news items without LLM processing. This is useful for:

- Building training datasets for fine-tuning the `liga-relevance` model
- Testing connector changes without GPU overhead
- Parallel data collection on docker-ai while production runs on gpu1

## Quick Start

```bash
# On docker-ai (192.168.0.124)
cd /home/kamienc/projects/liga-hessen-news-aggregator/news-aggregator

# Pull latest changes
git pull origin main

# Start training instance
docker compose -f docker-compose.training.yml up -d

# Verify instance is running
curl http://localhost:8001/api/admin/health | jq '.instance_type, .llm_enabled'
# Expected: "training", false
```

## Configuration

The training instance uses `docker-compose.training.yml` with these key differences from production:

| Setting | Production | Training |
|---------|------------|----------|
| `LLM_ENABLED` | `true` | `false` |
| `INSTANCE_TYPE` | `production` | `training` |
| Port | 8000 | 8001 |
| Volume | `liga-news-data` | `liga-news-training-data` |
| Container | `liga-news-backend` | `liga-news-backend-training` |

## How It Works

When `LLM_ENABLED=false`:

1. Items are fetched normally from all configured sources
2. LLM analysis is skipped completely
3. Items are saved with:
   - `summary = null`
   - `detailed_analysis = null`
   - `priority = null` (or default)
   - `relevance_score = null`
4. All raw content is preserved for later classification

## Syncing Configuration

Use the config export/import API to sync sources between instances:

```bash
# Export config from gpu1 (production)
curl http://gpu1:8000/api/admin/config/export > config.json

# Import to docker-ai (training)
curl -X POST http://localhost:8001/api/admin/config/import \
  -H "Content-Type: application/json" \
  -d @config.json
```

## Fetching Data

Trigger fetches manually or let the scheduler run:

```bash
# Fetch all sources
curl -X POST http://localhost:8001/api/scheduler/fetch-all

# Fetch specific source
curl -X POST http://localhost:8001/api/sources/1/fetch-all

# Check fetch status
curl http://localhost:8001/api/scheduler/status
```

## Exporting Training Data

Use the `db_backup.py` script to export items for ML training:

```bash
# Export all items as JSONL
docker exec liga-news-backend-training \
  python scripts/db_backup.py items -o /app/data/items.jsonl

# Export items from last 7 days
docker exec liga-news-backend-training \
  python scripts/db_backup.py items --since 2024-01-01 -o /app/data/recent.jsonl

# Export in training format for fine-tuning
docker exec liga-news-backend-training \
  python scripts/db_backup.py training -o /app/data/training.jsonl

# Copy to host
docker cp liga-news-backend-training:/app/data/training.jsonl ./
```

## Monitoring

```bash
# View logs
docker logs -f liga-news-backend-training

# Check database stats
curl http://localhost:8001/api/admin/db-stats

# Health check
curl http://localhost:8001/api/admin/health
```

## Running Alongside Production

The training instance can run alongside production on the same host:

- Production: port 8000, volume `liga-news-data`
- Training: port 8001, volume `liga-news-training-data`

```bash
# Start both
docker compose up -d                              # Production
docker compose -f docker-compose.training.yml up -d  # Training
```

## Troubleshooting

### Items still getting LLM analysis

Check that `LLM_ENABLED=false` is set:
```bash
curl http://localhost:8001/api/admin/health | jq '.llm_enabled'
```

If it shows `true`, restart the container:
```bash
docker compose -f docker-compose.training.yml down
docker compose -f docker-compose.training.yml up -d
```

### Database permission issues

The training volume is separate from production. If you need to share data, use the backup/restore scripts instead of sharing volumes.
