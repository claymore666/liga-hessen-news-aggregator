# LLM Processing Pipeline

## Overview

The LLM pipeline provides detailed news analysis using a local Ollama model. It runs as an async background worker processing items queued for analysis.

**Files**:
- `backend/services/llm_worker.py` - Background worker
- `backend/services/processor.py` - LLM interaction
- `backend/services/llm.py` - Ollama client

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     LLM Worker                          │
│  ┌──────────────────────────────────────────────────┐  │
│  │              Main Loop                            │  │
│  │  1. Check for fresh items (needs_llm=true)       │  │
│  │  2. Process batch                                 │  │
│  │  3. Sleep if idle                                 │  │
│  └──────────────────────────────────────────────────┘  │
└─────────────────────────┬───────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│                   ItemProcessor                          │
│  ┌──────────────────────────────────────────────────┐  │
│  │  analyze(item)                                    │  │
│  │  - Build prompt with title, content, source      │  │
│  │  - Call LLM                                       │  │
│  │  - Parse JSON response                            │  │
│  │  - Return analysis dict                           │  │
│  └──────────────────────────────────────────────────┘  │
└─────────────────────────┬───────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│                   Ollama Provider                        │
│  ┌──────────────────────────────────────────────────┐  │
│  │  generate(prompt, system_prompt)                  │  │
│  │  - HTTP POST to Ollama API                        │  │
│  │  - Stream response                                │  │
│  │  - Return generated text                          │  │
│  └──────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
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
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen3:14b-q8_0
LLM_ENABLED=true
```

### Runtime Settings

Toggle via API or database:
```http
PUT /api/llm/toggle
{"enabled": false}
```

## System Prompt

The system prompt defines the analysis criteria:

```
Du bist ein Sozialpolitik-Experte und klassifizierst
Nachrichtenartikel für die Liga der Freien Wohlfahrtspflege Hessen.

DIE LIGA: Dachverband der 6 Wohlfahrtsverbände in Hessen
(AWO, Caritas, Diakonie, DRK, Paritätischer, Jüdische Gemeinden)
mit 7.300 Einrichtungen, 113.000 Beschäftigten.

ARBEITSKREISE:
- AK1: Grundsatz/Sozialpolitik
- AK2: Eingliederungshilfe
- AK3: Jugendhilfe
...
```

## API Endpoints

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

### Toggle LLM
```http
PUT /api/llm/toggle
{"enabled": true}
```

### Change Model
```http
PUT /api/llm/model
{"model": "qwen3:32b"}
```

### Manual Reprocess
```http
POST /api/items/{item_id}/reprocess
```

### Batch Reprocess
```http
POST /api/llm/reprocess
{"item_ids": [1, 2, 3]}
```

## Worker Statistics

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

## Priority Mapping

LLM priorities are mapped to system priorities:

| LLM Priority | System Priority | Description |
|--------------|-----------------|-------------|
| critical | HIGH | Immediate attention |
| high | MEDIUM | Important |
| medium | LOW | Informational |
| low | NONE | Not relevant |

## Error Handling

### Retry Logic

```python
for attempt in range(3):
    try:
        analysis = await processor.analyze(item)
        break
    except Exception as e:
        logger.warning(f"Attempt {attempt + 1} failed: {e}")
        await asyncio.sleep(1.0 * (attempt + 1))
```

### Fallback

If LLM fails completely:
```python
return {
    "summary": "",
    "relevant": False,
    "priority": "low",
    "reasoning": "LLM analysis unavailable"
}
```

## Performance

### Batch Processing

Items processed in batches for efficiency:
```python
BATCH_SIZE = 10
IDLE_SLEEP = 5.0  # seconds
```

### Resource Usage

- Model: ~8GB VRAM (qwen3:14b-q8_0)
- Processing: ~2-5 seconds per item
- Memory: Minimal (streaming responses)

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

### LLM Not Available
1. Check Ollama is running: `curl http://localhost:11434/api/tags`
2. Verify model exists: `ollama list`
3. Check OLLAMA_BASE_URL env var

### Slow Processing
1. Check GPU usage: `nvidia-smi`
2. Consider smaller model
3. Reduce batch size

### JSON Parse Errors
- LLM sometimes returns malformed JSON
- Fallback extraction with regex
- Consider prompt tuning
