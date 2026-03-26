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

**Symptoms**: Items not being classified, `/api/admin/stats` shows `classifier_worker.service_available: false`

**Check**:
```bash
# Worker status (shows service_available flag)
curl -s http://localhost:8000/api/admin/stats | jq '.classifier_worker'

# Classifier container health
docker compose logs classifier | tail -20
```

**Common causes**:

1. **Embedding service down (gpu1 off)**
   ```
   Classifier service unavailable: Classifier returned 500
   ```
   The classifier needs embeddings from Ollama on gpu1. When gpu1 is sleeping,
   the classifier API returns 500 because the embedding endpoint is unreachable.

   The worker automatically backs off to **5-minute retries** without inflating
   the error counter. It recovers automatically when gpu1 comes back online.

   **Diagnosis**:
   ```bash
   docker compose logs backend | grep "service unavailable\|still unavailable" | tail -5
   ```

   **No action needed** — the worker will recover on its own. To force gpu1 awake:
   ```bash
   curl -X POST http://localhost:8000/api/admin/gpu1/force-process
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
curl http://localhost:8000/api/admin/stats | jq '.llm_worker'
```

**Common causes**:

1. **Worker paused or stopped**
   ```json
   {"paused": true}
   ```
   Fix: Resume via API
   ```bash
   curl -X POST http://localhost:8000/api/admin/llm-worker/resume
   ```

2. **Ollama proxy not reachable**
   ```
   Connection refused
   ```
   The backend connects to the Ollama proxy at `http://172.17.0.1:11434` (Docker
   host network). Check the proxy is running on docker-ai.

3. **Rate limiting (429)**
   ```
   All LLM providers rate-limited
   ```
   The Ollama proxy routes to Cerebras and Groq. When both are exhausted, the
   worker gets 429s. The worker now detects this and backs off (60s → 120s →
   240s → 300s). Check proxy logs to see upstream rate limit status.

   **Diagnosis**:
   ```bash
   docker compose logs backend | grep -i "rate-limit\|429" | tail -20
   ```

4. **Worker in error state**
   ```json
   {"stopped_due_to_errors": true}
   ```
   After 10+ consecutive errors, the worker enters error state (retries every
   5 min). It auto-recovers when processing succeeds. To force-resume:
   ```bash
   curl -X POST http://localhost:8000/api/admin/llm-worker/resume
   ```

### LLM Rate Limiting (429 Errors)

**Symptoms**: `llm_worker.stats.errors` increasing, log shows "Rate-limited" or "429 Too Many Requests"

**How it works**: The Ollama proxy on docker-ai routes to Cerebras (primary) and
Groq (fallback). Most upstream rate limits are handled silently by fallback
(~3,200 upstream events → only ~780 pass through to clients in a typical 24h period).

When **both** providers are exhausted simultaneously, the backend receives 429.

**Worker behavior** (since 2026-03-11):
- Detects `RateLimitError` and immediately stops the current batch
- Backs off: 60s → 120s → 240s → 300s (uses `Retry-After` header if present)
- Backoff is interruptible — new items arriving can trigger a retry sooner
- Failed items keep `needs_llm_processing=True` and are retried as backlog

**No items are lost**: All items eventually get processed once rate limits clear.

**Monitor**:
```bash
# Check error rate
curl -s http://localhost:8000/api/admin/stats | jq '.llm_worker.stats.errors'

# Check for pending items
docker exec liga-news-db psql -U liga -d liga_news -c \
  "SELECT COUNT(*) FROM items WHERE needs_llm_processing = true;"

# Watch rate-limit events in logs
docker compose logs -f backend 2>&1 | grep -i "rate-limit"
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

```
BrowserType.launch: Failed to launch: Error: spawn ... EAGAIN
```
- **Cause**: Container PID limit exhausted (cgroup accounting)
- Each Chromium browser spawns ~100 PIDs (main + zygote + GPU + network + renderer + audio)
- With 4 concurrent browsers = ~400 PIDs during operation
- **Fix**: Restart backend container to reset cgroup accounting
  ```bash
  docker compose -f docker-compose.prod.yml restart backend
  ```
- **Prevention**: `pids_limit: 1000` in docker-compose.prod.yml provides headroom

```
net::ERR_TUNNEL_CONNECTION_FAILED
```
- **Cause**: Proxy doesn't support HTTPS CONNECT tunneling
- Most free HTTP proxies only support plain HTTP, not HTTPS tunneling
- X.com requires HTTPS, so these proxies fail to establish the tunnel
- **Solution**: The proxy manager now tests HTTPS tunnel capability and maintains a separate pool of HTTPS-capable proxies
- X scraper requests HTTPS proxies via `checkout_proxy(prefer_https=True)`
- Falls back to direct connection if no HTTPS proxies available
- Check HTTPS proxy status: `curl http://localhost:8000/api/admin/proxies | jq '.https_count'`

```
Target page, context or browser has been closed
```
- **Cause**: Browser pool driver crashed or restarted during operation
- Cascade failure: one crash affects all pending requests
- Usually recovers automatically on next fetch cycle
- If persistent, restart backend container

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

### Browser Pool / Playwright Issues

**Symptoms**: X/Twitter or Instagram scraping fails, article extraction SPA fallback not working

**Check**:
```bash
docker compose logs backend | grep -i playwright
docker compose logs backend | grep -i "browser pool"
```

**Common causes**:

1. **Playwright not installed or outdated**
   ```
   playwright._impl._errors.Error: Executable doesn't exist
   ```
   Fix: Rebuild backend container (Dockerfile installs Playwright to `/opt/playwright`).
   The `PLAYWRIGHT_BROWSERS_PATH=/opt/playwright` env var ensures the browser is
   accessible regardless of user (root or appuser).
   ```bash
   docker compose build backend
   docker compose up -d backend
   ```

2. **Driver process crashed (generation restart)**
   ```
   Restarting Playwright driver (generation N, M errors)
   ```
   Usually self-recovers. If persistent:
   ```bash
   docker compose restart backend
   ```

3. **Semaphore exhausted (all browser slots in use)**
   ```
   Timeout waiting for browser slot
   ```
   Default max is 4 concurrent browsers. If multiple scrapers + SPA fallback run simultaneously, requests queue up. Check for stuck browsers:
   ```bash
   docker compose exec backend ps aux | grep chromium
   ```

4. **SPA fallback not triggering for expected sites**
   The Playwright SPA fallback in `article_extractor.py` only activates when trafilatura fails to detect an article AND the page contains SPA markers (e.g., `<div id="app">`, `noscript` tags mentioning JavaScript). Check:
   ```bash
   docker compose logs backend | grep "SPA fallback"
   ```

### VectorDB Index Out of Sync (DB ↔ ChromaDB Drift)

**Symptoms**: Log shows `VECTORDB SYNC CHECK: DB says N items indexed, ChromaDB has M items`

This happens when items have the `vectordb_indexed` metadata flag set in PostgreSQL but are not actually in ChromaDB (e.g., after a ChromaDB volume reset, container rebuild, or data migration).

**Diagnose**:
```bash
# Check the gap
docker exec liga-news-db psql -U liga -d liga_news -t -c \
  "SELECT COUNT(*) FROM items WHERE metadata::text LIKE '%vectordb_indexed%';"
# Compare with ChromaDB count from classifier health endpoint
```

**Fix** — reset stale flags so the dedup worker re-indexes automatically:
```bash
# 1. Export ChromaDB IDs to a file
docker exec liga-news-backend python3 -c "
import urllib.request, json
r = urllib.request.urlopen('http://classifier:8082/ids')
d = json.loads(r.read())
print(','.join(d.get('ids', [])))
" > /tmp/chromadb_ids.txt

# 2. Create SQL to reset flags for items NOT in ChromaDB
IDS=$(cat /tmp/chromadb_ids.txt)
cat > /tmp/reset_flags.sql << SQLEOF
UPDATE items
SET metadata = (metadata::jsonb - 'vectordb_indexed') - 'vectordb_indexed_at'
WHERE id NOT IN ($IDS);
SQLEOF

# 3. Run it
docker cp /tmp/reset_flags.sql liga-news-db:/tmp/reset_flags.sql
docker exec liga-news-db psql -U liga -d liga_news -f /tmp/reset_flags.sql

# 4. Monitor re-indexing (dedup worker processes ~50 items/3s automatically)
docker logs -f liga-news-backend 2>&1 | grep "Indexed.*items"
```

Re-indexing 12,000+ items takes ~12 minutes. The dedup worker picks up unflagged items automatically in batches of 50.

### Duplicate Detection Issues

**Symptoms**: Similar articles not being detected as duplicates

**Check**:
```bash
# From backend container (classifier not exposed on docker-ai host)
docker exec liga-news-backend python3 -c "
import urllib.request, json
r = urllib.request.urlopen('http://classifier:8082/health')
print(json.loads(r.read()).get('duplicate_index_items', 0))
"
```

**Common causes**:

1. **Duplicate index empty or small**
   Fix: Sync from search index
   ```bash
   docker exec liga-news-backend python3 -c "
   import urllib.request
   r = urllib.request.urlopen('http://classifier:8082/sync-duplicate-store',b'')
   print(r.read().decode())
   "
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

### Reset and Reload Items (with Vector Store Cleanup)

When you need to delete items and have them re-fetched (e.g., to apply new metadata extraction):

**Problem**: Simply deleting items from the database leaves orphaned embeddings in ChromaDB. When items are re-fetched, duplicate detection finds matches to non-existent items, causing foreign key violations.

**Solution**: Delete from both database AND vector store.

#### Step 1: Identify Items to Delete

```bash
# Example: Find Google Alerts items from last 24 hours
docker compose exec -T backend python -c "
import asyncio
from sqlalchemy import select, and_
from datetime import datetime, timedelta
from database import async_session_maker
from models import Item, Channel, ConnectorType

async def find_items():
    async with async_session_maker() as session:
        cutoff = datetime.now() - timedelta(hours=24)
        # Adjust query as needed
        result = await session.execute(
            select(Item.id)
            .join(Channel)
            .where(
                and_(
                    Channel.connector_type == ConnectorType.RSS,
                    Item.fetched_at >= cutoff
                )
            )
        )
        for row in result.all():
            print(row[0])

asyncio.run(find_items())
" > /tmp/items_to_delete.txt

echo \"Items to delete: \$(wc -l < /tmp/items_to_delete.txt)\"
```

#### Step 2: Delete from Database

```bash
docker compose exec -T backend python -c "
import asyncio
from sqlalchemy import delete
from database import async_session_maker
from models import Item

async def delete_items():
    ids = [int(x.strip()) for x in open('/tmp/items_to_delete.txt') if x.strip()]
    async with async_session_maker() as session:
        await session.execute(delete(Item).where(Item.id.in_(ids)))
        await session.commit()
        print(f'Deleted {len(ids)} items from database')

asyncio.run(delete_items())
"
```

#### Step 3: Delete from Vector Store

```bash
# Get all vector store IDs
curl -s http://localhost:8082/ids | jq -r '.ids[]' | sort > /tmp/vector_ids.txt

# Get all database IDs
docker compose exec -T backend python -c "
import asyncio
from sqlalchemy import select
from database import async_session_maker
from models import Item

async def get_ids():
    async with async_session_maker() as session:
        result = await session.execute(select(Item.id))
        for row in result.all():
            print(row[0])

asyncio.run(get_ids())
" | sort > /tmp/db_ids.txt

# Find orphans (in vector store but not in database)
comm -23 /tmp/vector_ids.txt /tmp/db_ids.txt > /tmp/orphan_ids.txt
echo "Orphaned vectors: $(wc -l < /tmp/orphan_ids.txt)"

# Delete orphans from vector store
ORPHAN_IDS=$(cat /tmp/orphan_ids.txt | grep -v '^$' | jq -R . | jq -s .)
curl -s -X POST http://localhost:8082/delete \
  -H "Content-Type: application/json" \
  -d "{\"ids\": $ORPHAN_IDS}" | jq .
```

#### Step 4: Re-fetch Items

```bash
# Trigger fetch for specific channels
curl -X POST http://localhost:8000/api/channels/{channel_id}/fetch

# Or fetch all sources
curl -X POST http://localhost:8000/api/sources/fetch-all
```

#### Quick One-Liner (for specific channel)

```bash
# Delete last 24h items from channel, clean vectors, refetch
CHANNEL_ID=123
docker compose exec -T backend python -c "
import asyncio
from sqlalchemy import select, delete, and_
from datetime import datetime, timedelta
from database import async_session_maker
from models import Item

async def reset():
    async with async_session_maker() as session:
        cutoff = datetime.now() - timedelta(hours=24)
        result = await session.execute(
            select(Item.id).where(and_(
                Item.channel_id == $CHANNEL_ID,
                Item.fetched_at >= cutoff
            ))
        )
        ids = [str(row[0]) for row in result.all()]
        if ids:
            await session.execute(delete(Item).where(Item.id.in_([int(i) for i in ids])))
            await session.commit()
            print(f'Deleted {len(ids)} items')
            # Print IDs for vector cleanup
            for i in ids: print(f'ID:{i}')

asyncio.run(reset())
" | tee /tmp/deleted.txt

# Extract IDs and delete from vector store
grep '^ID:' /tmp/deleted.txt | cut -d: -f2 | jq -R . | jq -s . | \
  xargs -I {} curl -s -X POST http://localhost:8082/delete \
    -H "Content-Type: application/json" -d '{"ids": {}}'

# Refetch
curl -X POST http://localhost:8000/api/channels/$CHANNEL_ID/fetch
```

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

### Vectordb Items Stuck (awaiting_vectordb)

**Symptoms**: `/api/admin/stats` shows `awaiting_vectordb > 0` that doesn't decrease.

**Check**:
```bash
docker compose logs backend | grep -i "batch index\|vectordb\|unindex"
docker compose logs classifier | grep -i "error\|500"
```

**Common causes**:

1. **Classifier not reachable from backend** (network issue after rebuild)
   ```
   Classifier unavailable for indexing
   ```
   Fix: Restart classifier to rejoin Docker network:
   ```bash
   docker compose restart classifier
   ```

2. **Ollama embed returns 500** (model context exceeded or model swapped out)
   ```
   Duplicate store batch indexing failed: 500 Internal Server Error
   ```
   Check the Ollama proxy logs for the underlying error:
   ```bash
   docker logs ollamaproxy-ollamaproxy-1 --since 5m | grep embed
   ```
   If `"the input length exceeds the context length"`: check the model's `num_ctx`:
   ```bash
   curl -s http://gpu1:11434/api/show -d '{"model": "paraphrase-multilingual:278m-mpnet-base-v2-fp16"}' | \
     python3 -c "import json,sys; print(json.load(sys.stdin).get('parameters',''))"
   ```
   The `paraphrase-multilingual` model needs `num_ctx: 512` (default is 128, actual limit is 512).
   Fix:
   ```bash
   curl http://gpu1:11434/api/create -d '{"model": "paraphrase-multilingual:278m-mpnet-base-v2-fp16", "from": "paraphrase-multilingual:278m-mpnet-base-v2-fp16", "parameters": {"num_ctx": 512}}'
   ```

3. **Items already in ChromaDB but not flagged in DB**
   ```
   Batch index returned 0 for N items
   ```
   This was a bug (fixed 2026-03-16): the dedup worker now marks items as indexed
   even when they already exist in ChromaDB.

### Monitor LLM Processing

```bash
docker compose logs -f backend | grep -i llm
```
