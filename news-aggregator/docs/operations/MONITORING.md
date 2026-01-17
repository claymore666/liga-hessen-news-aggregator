# Monitoring Guide

## Health Endpoints

### Backend Health

```http
GET /api/admin/health
```

Returns:
```json
{
  "status": "ok",
  "database": "connected",
  "scheduler_running": true,
  "llm_enabled": true,
  "llm_available": true,
  "llm_provider": "ollama",
  "llm_model": "qwen3:14b-q8_0",
  "classifier_available": true,
  "item_count": 3152,
  "unprocessed_count": 15
}
```

**Key fields**:
- `scheduler_running` - Fetch scheduler active
- `llm_available` - Ollama responding
- `classifier_available` - Classifier API responding
- `unprocessed_count` - Items in LLM queue

### Classifier Health

```http
GET http://localhost:8082/health
```

Returns:
```json
{
  "status": "ok",
  "model": "nomic-v2",
  "gpu": true,
  "gpu_name": "NVIDIA GeForce RTX 3090",
  "search_index_items": 3152,
  "duplicate_index_items": 3152
}
```

**Key fields**:
- `gpu` - GPU acceleration active
- `search_index_items` - Items indexed for search
- `duplicate_index_items` - Items indexed for duplicate detection

### Storage Stats

```http
GET /api/admin/storage
```

Returns:
```json
{
  "postgresql_size_bytes": 125829120,
  "postgresql_size_human": "120.0 MB",
  "postgresql_items": 3152,
  "search_index_size_bytes": 56326714,
  "search_index_size_human": "53.7 MB",
  "search_index_items": 3152,
  "duplicate_index_size_bytes": 67015808,
  "duplicate_index_size_human": "63.9 MB",
  "duplicate_index_items": 3152,
  "total_size_bytes": 249171642,
  "total_size_human": "237.6 MB"
}
```

## Scheduler Monitoring

### Scheduler Status

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
    {"id": "fetch_job", "next_run": "2024-01-15T10:30:00Z"},
    {"id": "cleanup_job", "next_run": "2024-01-16T03:00:00Z"}
  ]
}
```

### Channel Status

```http
GET /api/channels
```

Check for channels with errors:
```bash
curl http://localhost:8000/api/channels | jq '.[] | select(.last_error != null) | {name, last_error, last_fetched_at}'
```

## LLM Monitoring

### LLM Status

```http
GET /api/llm/status
```

Returns:
```json
{
  "enabled": true,
  "model": "qwen3:14b-q8_0",
  "available": true,
  "queue_size": 15,
  "processed_today": 142
}
```

### Worker Statistics

```http
GET /api/llm/worker/stats
```

Returns:
```json
{
  "running": true,
  "fresh_processed": 50,
  "backlog_processed": 100,
  "errors": 2,
  "last_processed_at": "2024-01-15T10:30:00Z"
}
```

## GPU Monitoring

### NVIDIA SMI

```bash
# Quick status
nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu --format=csv

# Continuous monitoring
watch -n 1 nvidia-smi
```

### GPU Memory by Process

```bash
nvidia-smi --query-compute-apps=pid,used_memory --format=csv
```

## Database Monitoring

### Item Statistics

```sql
-- Total items by priority
SELECT priority, COUNT(*) FROM items GROUP BY priority;

-- Items per day (last 7 days)
SELECT DATE(published_at), COUNT(*)
FROM items
WHERE published_at > NOW() - INTERVAL '7 days'
GROUP BY DATE(published_at)
ORDER BY 1 DESC;

-- Unprocessed items
SELECT COUNT(*) FROM items WHERE needs_llm_processing = true;

-- Items by channel
SELECT c.name, COUNT(i.id)
FROM channels c
LEFT JOIN items i ON c.id = i.channel_id
GROUP BY c.id
ORDER BY 2 DESC;
```

### Channel Health

```sql
-- Channels with recent errors
SELECT name, connector_type, last_error, last_fetched_at
FROM channels
WHERE last_error IS NOT NULL
ORDER BY last_fetched_at DESC;

-- Stale channels (not fetched in 24h)
SELECT name, connector_type, last_fetched_at
FROM channels
WHERE enabled = true
  AND (last_fetched_at IS NULL OR last_fetched_at < NOW() - INTERVAL '24 hours');
```

### Database Size

```sql
-- Table sizes
SELECT
  relname AS table,
  pg_size_pretty(pg_total_relation_size(relid)) AS total_size
FROM pg_catalog.pg_statio_user_tables
ORDER BY pg_total_relation_size(relid) DESC;
```

## Log Monitoring

### Docker Compose Logs

```bash
# All services
docker compose logs -f

# Specific service with timestamps
docker compose logs -f --timestamps backend

# Last 100 lines
docker compose logs --tail 100 backend

# Since specific time
docker compose logs --since "2024-01-15T10:00:00" backend
```

### Error Filtering

```bash
# All errors
docker compose logs | grep -i error

# Backend errors only
docker compose logs backend 2>&1 | grep -i error

# Exclude certain patterns
docker compose logs | grep -i error | grep -v "expected_error_pattern"
```

### Activity Monitoring

```bash
# Watch fetch activity
docker compose logs -f backend | grep -E "Fetching|fetched|items"

# Watch classification
docker compose logs -f backend | grep -i classif

# Watch LLM processing
docker compose logs -f backend | grep -E "LLM|llm|analysis"
```

## Alerting Patterns

### Simple Health Check Script

```bash
#!/bin/bash
# health-check.sh

BACKEND_URL="http://localhost:8000"
CLASSIFIER_URL="http://localhost:8082"

# Check backend
if ! curl -sf "$BACKEND_URL/api/admin/health" > /dev/null; then
    echo "ALERT: Backend not responding"
    exit 1
fi

# Check classifier
if ! curl -sf "$CLASSIFIER_URL/health" > /dev/null; then
    echo "ALERT: Classifier not responding"
    exit 1
fi

# Check scheduler
if ! curl -sf "$BACKEND_URL/api/scheduler/status" | jq -e '.running == true' > /dev/null; then
    echo "ALERT: Scheduler not running"
    exit 1
fi

# Check LLM queue size
QUEUE_SIZE=$(curl -sf "$BACKEND_URL/api/llm/status" | jq '.queue_size')
if [ "$QUEUE_SIZE" -gt 100 ]; then
    echo "ALERT: LLM queue backlog ($QUEUE_SIZE items)"
    exit 1
fi

echo "OK: All systems healthy"
exit 0
```

### Cron Monitoring

```bash
# Add to crontab
# Check health every 5 minutes
*/5 * * * * /path/to/health-check.sh >> /var/log/news-aggregator-health.log 2>&1
```

## Dashboard Metrics

### Key Metrics to Track

| Metric | Source | Alert Threshold |
|--------|--------|-----------------|
| Backend up | `/api/admin/health` | Down |
| Classifier up | `:8082/health` | Down |
| Scheduler running | `/api/scheduler/status` | Not running |
| LLM queue size | `/api/llm/status` | > 100 items |
| GPU utilization | `nvidia-smi` | > 95% sustained |
| Database size | PostgreSQL | > 10 GB |
| Error rate | Logs | > 10/hour |

### Prometheus-Style Metrics (If Added)

Example metrics that could be exposed:

```
# Items
news_aggregator_items_total{priority="high"} 150
news_aggregator_items_total{priority="medium"} 450
news_aggregator_items_total{priority="low"} 800
news_aggregator_items_unprocessed 15

# Channels
news_aggregator_channels_total 45
news_aggregator_channels_with_errors 2

# Processing
news_aggregator_llm_processed_total 5000
news_aggregator_classifier_requests_total 10000

# Storage
news_aggregator_storage_bytes{type="postgresql"} 125829120
news_aggregator_storage_bytes{type="vector"} 56326714
```

## Debugging Commands

### Quick System Overview

```bash
# One-liner system check
echo "=== Docker ===" && docker compose ps && \
echo "=== Backend ===" && curl -s http://localhost:8000/api/admin/health | jq && \
echo "=== Classifier ===" && curl -s http://localhost:8082/health | jq && \
echo "=== GPU ===" && nvidia-smi --query-gpu=name,memory.used --format=csv
```

### Recent Activity Summary

```bash
# Items fetched in last hour
docker compose logs --since 1h backend | grep -c "Stored new item"

# Errors in last hour
docker compose logs --since 1h | grep -ci error

# LLM items processed in last hour
docker compose logs --since 1h backend | grep -c "LLM analysis complete"
```
