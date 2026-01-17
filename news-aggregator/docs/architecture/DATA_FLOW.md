# Data Flow

## Item Processing Pipeline

```
Source (RSS/X/etc.)
        │
        ▼
┌───────────────┐
│   Connector   │  Fetches raw items from source
│    fetch()    │
└───────┬───────┘
        │
        ▼
┌───────────────┐
│   Pipeline    │  Normalizes and deduplicates
│  process()    │
└───────┬───────┘
        │
        ├──────────────────┐
        ▼                  ▼
┌───────────────┐  ┌───────────────┐
│   Classifier  │  │   Database    │
│    Worker     │  │    Insert     │
└───────┬───────┘  └───────────────┘
        │
        ▼
┌───────────────┐
│  LLM Worker   │  Detailed analysis
│   (async)     │
└───────┬───────┘
        │
        ▼
┌───────────────┐
│   Database    │  Update with analysis
│    Update     │
└───────────────┘
```

## Pipeline Stages

### 1. Fetch Stage
**File**: `backend/services/scheduler.py`

The scheduler triggers connectors to fetch from sources:

```python
# Parallel fetch by source type
for source_type, channels in by_type.items():
    async with semaphore:
        items = await connector.fetch(config)
```

Each connector returns `RawItem` objects with:
- `external_id` - Unique ID from source
- `title` - Item title
- `content` - Full content
- `url` - Source URL
- `published_at` - Publication date
- `metadata` - Connector-specific data

### 2. Normalization Stage
**File**: `backend/services/pipeline.py`

Raw items are normalized:

```python
normalized = NormalizedItem(
    external_id=raw.external_id,
    title=raw.title.strip(),
    content=clean_content(raw.content),
    url=normalize_url(raw.url),
    ...
)
```

### 3. Deduplication Stage
**File**: `backend/services/pipeline.py`

Three-level deduplication:

1. **Exact match** - Same `external_id` and `channel_id`
2. **Content hash** - MD5 of normalized title+content
3. **Semantic** - Embedding similarity > 0.75

```python
# Check for duplicates
duplicates = await relevance_filter.find_duplicates(
    title=item.title,
    content=item.content,
    threshold=0.75
)
```

### 4. Pre-Classification Stage
**File**: `backend/services/pipeline.py`

Fast ML classification before database insert:

```python
should_process, result = await relevance_filter.should_process(
    title=item.title,
    content=item.content,
    source=channel.source.name
)
```

Returns:
- `relevant` - Boolean relevance
- `confidence` - 0.0-1.0 score
- `priority` - high/medium/low/none
- `assigned_aks` - List of AK codes

### 5. Database Insert
**File**: `backend/services/pipeline.py`

Items saved with initial classification:

```python
item = Item(
    channel_id=channel.id,
    external_id=normalized.external_id,
    title=normalized.title,
    content=normalized.content,
    priority=classifier_priority,
    priority_score=int(confidence * 100),
    needs_llm_processing=True,  # Queue for LLM
    ...
)
```

### 6. Vector Indexing
**File**: `backend/services/relevance_filter.py`

Items indexed for search and duplicate detection:

```python
await relevance_filter.index_item(
    item_id=item.id,
    title=item.title,
    content=item.content
)
```

### 7. LLM Analysis (Async)
**File**: `backend/services/llm_worker.py`

Background worker processes queued items:

```python
analysis = await processor.analyze(item)
# Returns: summary, detailed_analysis, priority, reasoning
```

Updates item with:
- `summary` - Generated summary
- `detailed_analysis` - Extended analysis
- `priority` - LLM-assigned priority
- `needs_llm_processing = False`

## Classification Flow

```
New Item
    │
    ▼
┌─────────────────┐
│ Classifier API  │
│  /classify      │
└────────┬────────┘
         │
    ┌────┴────┐
    │         │
    ▼         ▼
Relevant?   Priority?
    │         │
    └────┬────┘
         │
         ▼
┌─────────────────┐
│ Confidence Check│
└────────┬────────┘
         │
    ┌────┴────┬────────────┐
    │         │            │
    ▼         ▼            ▼
 >0.75     0.25-0.75     <0.25
High Conf  Medium Conf   Low Conf
    │         │            │
    ▼         ▼            ▼
Use Result  Queue LLM   Skip/Low Pri
```

## Link Following

For social media posts with links:

```
Post with URL
      │
      ▼
┌─────────────┐
│ Extract URL │
└──────┬──────┘
       │
       ▼
┌─────────────┐
│Article Fetch│
│(trafilatura)│
└──────┬──────┘
       │
       ▼
┌─────────────┐
│Append to    │
│Post Content │
└─────────────┘
```

**File**: `backend/services/article_extractor.py`

## Refetch Flow

Manual refetch updates existing items:

```
Refetch Request
      │
      ▼
┌─────────────────┐
│ Load Item       │
└────────┬────────┘
         │
    ┌────┴────┐
    │         │
    ▼         ▼
   RSS     Social
    │         │
    ▼         ▼
Fetch URL  Extract Links
    │         │
    └────┬────┘
         │
         ▼
┌─────────────────┐
│ Update Content  │
│ Re-classify     │
└─────────────────┘
```
