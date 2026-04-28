# LLM Processing Pipeline

## Overview

The LLM pipeline provides detailed news analysis using a local Ollama model. It runs as an async background worker processing items queued for analysis.

**Files**:
- `backend/services/llm_worker.py` - Background worker with priority queue
- `backend/services/processor.py` - LLM interaction and response parsing
- `backend/services/llm/service.py` - Multi-provider LLM service with fallback
- `backend/services/llm/ollama.py` - Ollama provider (routes to proxy on docker-ai)
- `backend/services/llm/openrouter.py` - OpenRouter provider (fallback)
- `backend/services/llm/base.py` - Base provider interface and `RateLimitError`

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                      LLM Worker                           │
│  Priority queue: fresh items first, then backlog          │
│  Rate-limit aware: breaks batch + backs off on 429        │
└────────────────────────┬─────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────┐
│                    ItemProcessor                           │
│  1. analyze_from_data_with_messages() — main analysis     │
│  2. extract_topics() — follow-up chat turn                │
│  3. confirm_duplicate() — edge-case dedup (if needed)     │
└────────────────────────┬─────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────┐
│              LLMService (multi-provider fallback)          │
│  Tries providers in order. Raises RateLimitError if all   │
│  return 429.                                              │
└────────────────────────┬─────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────┐
│                  Ollama Proxy (docker-ai)                  │
│  Routes gpt-oss-120b to Cerebras (primary) + Groq         │
│  (fallback). Handles most upstream rate limits silently.   │
└──────────────────────────────────────────────────────────┘
```

## Processing Flow

### 1. Classification Dependency

**Important**: LLM worker only processes items that have been classified first.

Items must have `pre_filter` metadata (set by classifier) before LLM processes them.
This ensures no compute is wasted on items the classifier would mark as irrelevant.

```
Fetch → Classifier Worker → LLM Worker
         (fast, ~3/sec)      (slow, ~5sec each)
```

### 2. Item Queuing

Items are queued for LLM when:
- Item has been classified (has `pre_filter` metadata)
- Classifier confidence >= 0.25 (not certainly irrelevant)
- `needs_llm_processing = True`

Items with classifier confidence < 0.25 are marked `needs_llm_processing=False` and skip LLM entirely.

### 3. Worker Loop

```python
class LLMWorker:
    async def run(self):
        while True:
            # Process fresh items first
            fresh_count = await self._process_fresh_items()

            # Then backlog if idle
            if fresh_count == 0:
                await self._process_backlog()

            # Sleep between cycles
            await asyncio.sleep(self.idle_sleep)
```

### 3. Analysis

```python
async def analyze(item: Item) -> dict:
    prompt = f"""Titel: {item.title}
Inhalt: {item.content[:6000]}
Quelle: {source_name}
Datum: {date_str}"""

    response = await llm.generate(prompt, ANALYSIS_SYSTEM_PROMPT)
    return parse_json_response(response)
```

### 4. Response Parsing

Expected LLM response format:
```json
{
  "summary": "Brief summary in German",
  "detailed_analysis": "Extended analysis",
  "relevant": true,
  "relevance_score": 0.85,
  "priority": "high",
  "assigned_aks": ["AK1", "AK3"],
  "tags": ["Sozialpolitik", "Hessen"],
  "reasoning": "Why this is relevant..."
}
```

### 5. Item Update

```python
item.summary = analysis.get("summary")
item.detailed_analysis = analysis.get("detailed_analysis")
item.priority = map_priority(analysis.get("priority"))
item.priority_score = int(analysis.get("relevance_score", 0) * 100)
item.assigned_aks = analysis.get("assigned_aks", [])
item.needs_llm_processing = False
```

## Configuration

### Environment Variables

```bash
OLLAMA_BASE_URL=http://ollamaproxy:11434  # Ollama proxy backplane (on ollamaproxy_default network)
OLLAMA_MODEL=gpt-oss-120b                 # Routed to Cerebras/Groq via proxy
```

The backend is dual-homed (`lan-shared` macvlan + `news-aggregator_default` bridge),
so its default route exits via the LAN. To reach the proxy directly by Docker DNS,
the backend container also joins the external `ollamaproxy_default` network in
`docker-compose.prod.yml`. Do **not** use `http://172.17.0.1:11434` — that path
is unreachable from the macvlan-attached backend.

### System Prompt

Prompts are stored in the database with model-specific versioning (table `llm_prompts`).
Runtime prompt changes without redeployment. Each processed item tracks which prompt
version was used in its metadata.

- **API**: `GET/POST /api/prompts/` — list, create, activate prompts
- **Fallback**: If no DB prompt exists, uses hardcoded `ANALYSIS_SYSTEM_PROMPT` from `processor.py`
- **Current**: v9 prompt for `gpt-oss-120b` (see `docs/services/PROMPT_TUNING.md`)

## API Endpoints

### Worker Status (via admin stats)
```bash
curl http://localhost:8000/api/admin/stats | jq '.llm_worker'
```

### Worker Controls
```bash
# Pause/resume
curl -X POST http://localhost:8000/api/admin/llm-worker/pause
curl -X POST http://localhost:8000/api/admin/llm-worker/resume
```

### Manual Reprocess
```bash
curl -X POST http://localhost:8000/api/items/{item_id}/reprocess
```

### Retry Queue
```bash
# Check pending retries
curl http://localhost:8000/api/items/retry-queue | jq .

# Trigger retry processing
curl -X POST "http://localhost:8000/api/items/retry-queue/process?batch_size=10"
```

### Prompt Management
```bash
# List available prompts
curl http://localhost:8000/api/prompts/ | jq .

# Get active prompt for model
curl http://localhost:8000/api/prompts/gpt-oss-120b/active | jq .
```

## Priority Mapping

LLM priorities are mapped to system priorities:

| LLM Priority | System Priority | Min Score |
|--------------|-----------------|-----------|
| critical | HIGH | 95 |
| high | HIGH | 90 |
| medium | MEDIUM | 70 |
| low | LOW | 40 |
| (irrelevant) | NONE | ≤20 |

## Error Handling

### Rate Limit Handling (429)

The LLM runs via an Ollama proxy on docker-ai that routes to cloud providers
(Cerebras, Groq). When both upstream providers are rate-limited simultaneously,
the proxy returns 429 to the backend.

**Detection**: `LLMService.complete()`/`chat()` catches `httpx.HTTPStatusError`
and tracks whether all provider failures are 429s. If so, it raises
`RateLimitError` (from `services.llm.base`) instead of a generic `RuntimeError`.

**Worker behavior on `RateLimitError`**:
1. **Per-item**: Immediately breaks out of the current batch (no point trying
   more items when all providers are exhausted)
2. **Outer loop**: Dedicated handler applies exponential backoff starting at
   60s (60s → 120s → 240s → 300s cap). Uses `Retry-After` header if present.
3. **Interruptible**: Backoff sleep is interrupted by the wake event if new
   items arrive (providers may have recovered by then).
4. **Topic extraction**: If rate-limited during the follow-up topic extraction
   call, the item is saved with topic="Sonstiges" rather than losing the
   already-completed analysis.

### General Error Handling

Per-item failures are caught individually — one item failing doesn't prevent
the rest of the batch from processing. Failed items keep
`needs_llm_processing=True` and are retried as backlog.

The outer worker loop tracks consecutive errors. After 10 consecutive errors,
the worker enters a degraded state (still retries with exponential backoff:
10s → 20s → 40s → ... → 300s cap). Consecutive error counter resets on any
successful processing.

## Performance

### Processing

- Cloud LLM via Ollama proxy — no local GPU needed for inference
- Processing: ~2s per item (cloud), avg ~2s observed in prod
- Three-phase DB connection pattern: quick read → LLM (no DB held) → quick write
- Fresh items processed immediately, backlog opportunistically

## Monitoring

### Logs

```bash
docker compose logs backend | grep -i "llm\|worker"
```

### Health Check

LLM availability in health endpoint:
```http
GET /api/admin/health
```

Returns `llm_available` and `llm_provider` fields.

## Troubleshooting

See [TROUBLESHOOTING.md](../operations/TROUBLESHOOTING.md) for:
- LLM not processing (worker paused, proxy unreachable, rate limiting)
- Rate limiting details and monitoring
- Worker degraded state recovery
