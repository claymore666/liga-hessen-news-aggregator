# Troubleshooting Guide

## Quick Diagnostics

### Health Check All Services

```bash
# All services status
docker compose ps

# Backend health
curl http://localhost:8000/api/admin/health | jq

# Classifier health
curl http://localhost:8082/health | jq

# Ollama (LLM)
curl http://localhost:11434/api/tags | jq
```

### Log Commands

```bash
# All logs
docker compose logs -f

# Specific service
docker compose logs -f backend
docker compose logs -f frontend
docker compose logs -f classifier
```

## Common Issues

### Backend Won't Start

**Symptoms**: Container exits, API not responding

**Check**:
```bash
docker compose logs backend | tail -50
```

**Common causes**:

1. **Database connection failed**
   ```
   sqlalchemy.exc.OperationalError: connection refused
   ```
   Fix: Ensure PostgreSQL is running
   ```bash
   docker compose up -d db
   docker compose logs db
   ```

2. **Migration error**
   ```
   alembic.util.exc.CommandError
   ```
   Fix: Run migrations manually
   ```bash
   docker compose exec backend alembic upgrade head
   ```

3. **Port already in use**
   ```
   bind: address already in use
   ```
   Fix: Check what's using the port
   ```bash
   ss -tlnp | grep 8000
   ```

### Classifier Not Available

**Symptoms**: Items not being classified, `/api/admin/health` shows `classifier_available: false`

**Check**:
```bash
curl http://localhost:8082/health
docker compose logs classifier
```

**Common causes**:

1. **GPU not detected**
   ```
   "gpu": false
   ```
   Fix: Check NVIDIA runtime
   ```bash
   nvidia-smi
   docker run --rm --gpus all nvidia/cuda:12.0-base nvidia-smi
   ```

2. **Model files missing**
   ```
   FileNotFoundError: classifier model not found
   ```
   Fix: Retrain classifier
   ```bash
   cd relevance-tuner
   python train_embedding_classifier.py
   docker compose restart classifier
   ```

3. **ChromaDB corruption**
   ```
   chromadb.errors.InvalidDimensionException
   ```
   Fix: Reset vector stores
   ```bash
   rm -rf data/vectordb data/duplicatedb
   docker compose restart classifier
   # Re-index items via API or wait for scheduler
   ```

### LLM Not Processing

**Symptoms**: Items stuck with `needs_llm_processing=true`

**Check**:
```bash
curl http://localhost:8000/api/llm/status | jq
curl http://localhost:11434/api/tags | jq
```

**Common causes**:

1. **LLM disabled**
   ```json
   {"enabled": false}
   ```
   Fix: Enable via API
   ```bash
   curl -X PUT http://localhost:8000/api/llm/toggle \
     -H "Content-Type: application/json" \
     -d '{"enabled": true}'
   ```

2. **Ollama not running**
   ```
   Connection refused
   ```
   Fix: Start Ollama
   ```bash
   systemctl start ollama
   # or
   ollama serve &
   ```

3. **Model not pulled**
   ```
   model 'qwen3:14b-q8_0' not found
   ```
   Fix: Pull the model
   ```bash
   ollama pull qwen3:14b-q8_0
   ```

### Scheduler Not Running

**Symptoms**: No new items being fetched

**Check**:
```bash
curl http://localhost:8000/api/scheduler/status | jq
```

**Common causes**:

1. **Scheduler stopped**
   ```json
   {"running": false}
   ```
   Fix: Start scheduler
   ```bash
   curl -X POST http://localhost:8000/api/scheduler/start
   ```

2. **All channels disabled**
   Check channels in database or UI

3. **Channel errors**
   ```bash
   # Check for channels with errors
   curl http://localhost:8000/api/channels | jq '.[] | select(.last_error != null)'
   ```

### Channel Fetch Failures

**Symptoms**: Channel shows error, no new items

**Check channel status**:
```bash
curl http://localhost:8000/api/channels/{id} | jq
```

**By connector type**:

#### RSS Feeds
```
Failed to parse RSS
```
- Check URL is valid RSS/Atom
- Test with: `curl -s "URL" | head -20`

#### X/Twitter Scraper
```
TimeoutError: Browser timeout
```
- Account may be rate limited
- Try increasing timeout in config
- Check Playwright browsers installed

#### Mastodon
```
401 Unauthorized
```
- Access token expired
- Regenerate token in Mastodon settings

#### Instagram
```
rate limited
```
- Wait 24h before retrying
- Consider using instagram_scraper instead

### Duplicate Detection Issues

**Symptoms**: Similar articles not being detected as duplicates

**Check**:
```bash
curl http://localhost:8082/health | jq '.duplicate_index_items'
```

**Common causes**:

1. **Duplicate index empty or small**
   Fix: Sync from search index
   ```bash
   curl -X POST http://localhost:8082/sync-duplicate-store
   ```

2. **Threshold too high**
   Default is 0.75, try lowering:
   ```bash
   curl -X POST http://localhost:8082/find-duplicates \
     -H "Content-Type: application/json" \
     -d '{"title": "...", "content": "...", "threshold": 0.6}'
   ```

### Frontend Issues

**Symptoms**: UI not loading, blank page

**Check**:
```bash
docker compose logs frontend
curl http://localhost:3000
```

**Common causes**:

1. **Build failed**
   ```
   vite build failed
   ```
   Fix: Rebuild frontend
   ```bash
   docker compose build frontend
   docker compose up -d frontend
   ```

2. **API connection failed**
   Check browser console for CORS or network errors
   Verify `VITE_API_URL` in frontend config

### Database Issues

**Symptoms**: Slow queries, connection errors

**Check**:
```bash
docker compose exec db psql -U postgres -d news_aggregator -c "SELECT count(*) FROM items;"
```

**Common causes**:

1. **Too many items**
   Run housekeeping cleanup or adjust retention

2. **Missing indexes**
   ```bash
   docker compose exec backend alembic upgrade head
   ```

3. **Connection pool exhausted**
   Restart backend to reset connections
   ```bash
   docker compose restart backend
   ```

## Recovery Procedures

### Full Reset

**Warning**: Deletes all data

```bash
docker compose down -v
rm -rf data/
docker compose up -d
```

### Reset Vector Stores Only

```bash
rm -rf data/vectordb data/duplicatedb
docker compose restart classifier
# Items will be re-indexed on next fetch
```

### Reset LLM Processing Queue

```sql
-- Mark all items as processed
UPDATE items SET needs_llm_processing = false;
```

### Rebuild Single Service

```bash
docker compose build backend
docker compose up -d backend
```

## Performance Tuning

### Slow Item Fetching

1. Check semaphore limits in `scheduler.py`
2. Reduce concurrent fetches for slow connectors
3. Increase `fetch_interval_minutes` for problematic channels

### High Memory Usage

1. Reduce LLM batch size
2. Lower ChromaDB cache size
3. Enable autopurge for old items

### Slow Classification

1. Verify GPU is being used
2. Reduce embedding batch size
3. Check for memory pressure with `nvidia-smi`

## Logs Analysis

### Find Errors in Last Hour

```bash
docker compose logs --since 1h | grep -i error
```

### Count Errors by Type

```bash
docker compose logs backend | grep -i error | cut -d: -f4 | sort | uniq -c | sort -rn
```

### Watch Classification Activity

```bash
docker compose logs -f classifier | grep -i classify
```

### Monitor LLM Processing

```bash
docker compose logs -f backend | grep -i llm
```
