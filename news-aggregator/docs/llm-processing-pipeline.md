# LLM Processing Pipeline

## Overview

Items flow through a two-stage relevance filtering system before appearing in the feed:

```
Fetch → Classifier (fast) → LLM Worker (slow) → Feed
```

## Stage 1: Classifier Pre-Filter

When items are fetched, the classifier runs first and assigns a `retry_priority` in metadata:

| retry_priority | Meaning | LLM Processing |
|----------------|---------|----------------|
| `high` | Classifier confident item is relevant | Yes |
| `edge_case` | Classifier uncertain | Yes |
| `unknown` | No classifier result | Yes |
| `low` | Classifier confident item is NOT relevant | Skipped |

The classifier is fast (~10ms per item) and filters out obvious noise before expensive LLM calls.

## Stage 2: LLM Worker

The LLM worker runs continuously with two queues:

### Fresh Queue (Priority 1)
- Items just fetched go here immediately
- Processed within seconds of arrival
- In-memory queue, highest priority

### Backlog Queue (Priority 2)
- Items with `needs_llm_processing=True` and `retry_priority != "low"`
- Processed when fresh queue is empty
- Ordered by: high → unknown → edge_case
- Batch size: 50 items per query

### Processing Flow

```
1. Check fresh queue → process if items present
2. If fresh queue empty → query backlog from database
3. For each item:
   - Call LLM for analysis (summary, priority, AK assignment)
   - Update item with results
   - Set needs_llm_processing=False
4. Sleep 30s if no work, then repeat
```

## Item States

| Field | Purpose |
|-------|---------|
| `needs_llm_processing` | Boolean flag: item awaits LLM analysis |
| `metadata.retry_priority` | Classifier's confidence (high/edge_case/low/unknown) |
| `priority` | Final priority after LLM (high/medium/low/none) |
| `priority_score` | Numeric score 0-100 for sorting |

## Timing

- Classifier: ~10ms per item
- LLM (Ollama qwen3:14b): ~15-20s per item
- Fresh items: processed within 1 minute of fetch
- Backlog: processes ~180 items/hour when idle

## Monitoring

```bash
# Check LLM worker status
docker compose logs backend | grep -E "LLM (fresh|backlog)"

# Count items awaiting processing
curl -s "http://localhost:8000/api/items?needs_llm_processing=true&page_size=1" | jq .total
```
