# Processing Analytics System

Records every processing step for items to enable debugging, model comparison, and training data collection.

## Purpose

- **Reproducibility**: Trace how any item ended up with its current priority/classification
- **Edge Case Detection**: Find items where classifier and/or LLM were uncertain
- **Model Comparison**: Compare classifier vs LLM decisions for disagreement analysis
- **Performance Monitoring**: Track processing times and error rates by model
- **Training Data**: Identify items suitable for model improvement

## Database Schema

### Table: `item_processing_logs`

| Column | Type | Description |
|--------|------|-------------|
| `id` | SERIAL | Primary key |
| `item_id` | INTEGER | FK to items (nullable for pre-creation logs) |
| `processing_run_id` | UUID | Links all steps in one processing run |
| `step_type` | VARCHAR(50) | Type of processing step |
| `step_order` | INTEGER | Order within the processing run |
| `started_at` | TIMESTAMP | Step start time |
| `completed_at` | TIMESTAMP | Step completion time |
| `duration_ms` | INTEGER | Processing duration |
| `model_name` | VARCHAR(100) | Model used (e.g., qwen3:14b-q8_0) |
| `model_version` | VARCHAR(50) | Model version |
| `model_provider` | VARCHAR(50) | Provider (ollama, classifier) |
| `confidence_score` | FLOAT | Confidence from the step (0-1) |
| `priority_input` | VARCHAR(20) | Priority before step |
| `priority_output` | VARCHAR(20) | Priority after step |
| `priority_changed` | BOOLEAN | Whether priority changed |
| `ak_suggestions` | JSON | List of suggested AK codes |
| `ak_primary` | VARCHAR(10) | Primary AK suggestion |
| `ak_confidence` | FLOAT | Confidence in AK suggestion |
| `relevant` | BOOLEAN | Whether item is relevant |
| `relevance_score` | FLOAT | Relevance score (0-1) |
| `success` | BOOLEAN | Step completed successfully |
| `skipped` | BOOLEAN | Step was skipped |
| `skip_reason` | VARCHAR(100) | Reason for skipping |
| `error_message` | TEXT | Error message if failed |
| `input_data` | JSON | Full input data |
| `output_data` | JSON | Full output data |
| `created_at` | TIMESTAMP | Log creation time |

### Indexes

| Index | Columns | Notes |
|-------|---------|-------|
| `ix_processing_logs_item_id` | item_id | Fast lookup by item |
| `ix_processing_logs_run_id` | processing_run_id | Group by processing run |
| `ix_processing_logs_step_type` | step_type | Filter by step type |
| `ix_processing_logs_created_at` | created_at | Time-based queries |
| `ix_processing_logs_low_confidence` | step_type, confidence_score | Partial: confidence < 0.5 |
| `ix_processing_logs_priority_changed` | step_type, priority_changed | Partial: changed = true |

## Step Types

| Step Type | Description | Model |
|-----------|-------------|-------|
| `fetch` | Initial item fetch from source | N/A |
| `pre_filter` | Classifier relevance check during pipeline | nomic-embed-text-v2 |
| `duplicate_check` | Semantic duplicate detection | paraphrase-mpnet |
| `rule_match` | Keyword/regex rule matching | N/A |
| `classifier_override` | Background classifier processing | nomic-embed-text-v2 |
| `llm_analysis` | Full LLM analysis | qwen3:14b-q8_0 |
| `reprocess` | Manual or scheduled reprocessing | varies |

## API Endpoints

### Summary Analytics
```
GET /api/analytics/summary?days=7
```

Returns aggregate statistics:
- Total logs by step type
- Low confidence count
- Priority changed count
- Error count
- Average processing time

### Low Confidence Items
```
GET /api/analytics/low-confidence?min_confidence=0.25&max_confidence=0.5&limit=50
```

Find items where the classifier was uncertain. Good candidates for manual review or training data.

### Classifier vs LLM Disagreements
```
GET /api/analytics/disagreements?limit=50
```

Find items where classifier and LLM disagree on priority or AK assignment. Useful for model alignment analysis.

### Item Processing History
```
GET /api/analytics/item/{id}/history
```

Get the full processing history for a specific item. Shows all steps in order, allowing reconstruction of how the item reached its current state.

### Model Performance
```
GET /api/analytics/model-performance?days=7
```

Get performance statistics by model:
- Total processed
- Average duration
- Error rate
- Priority change frequency
- Average confidence

### Recent Errors
```
GET /api/analytics/recent-errors?limit=50
```

Get recent processing failures for debugging.

## Example Queries

### Find Edge Cases (Low Confidence)
```sql
SELECT i.id, i.title, p.confidence_score, p.priority_output
FROM item_processing_logs p
JOIN items i ON p.item_id = i.id
WHERE p.step_type = 'pre_filter'
  AND p.confidence_score BETWEEN 0.25 AND 0.5
ORDER BY p.confidence_score ASC;
```

### Find Classifier vs LLM Disagreements
```sql
WITH classifier AS (
    SELECT DISTINCT ON (item_id)
           item_id, priority_output as clf_priority, ak_primary as clf_ak
    FROM item_processing_logs
    WHERE step_type IN ('pre_filter', 'classifier_override')
    ORDER BY item_id, created_at DESC
),
llm AS (
    SELECT DISTINCT ON (item_id)
           item_id, priority_output as llm_priority, ak_primary as llm_ak
    FROM item_processing_logs
    WHERE step_type = 'llm_analysis'
    ORDER BY item_id, created_at DESC
)
SELECT i.id, i.title, c.clf_priority, l.llm_priority, c.clf_ak, l.llm_ak
FROM classifier c
JOIN llm l ON c.item_id = l.item_id
JOIN items i ON c.item_id = i.id
WHERE c.clf_priority != l.llm_priority OR c.clf_ak != l.llm_ak;
```

### Reproduce Item Processing History
```sql
SELECT step_order, step_type, model_name, confidence_score,
       priority_input, priority_output, ak_suggestions, output_data
FROM item_processing_logs
WHERE item_id = 12345
ORDER BY step_order;
```

### Processing Time by Model
```sql
SELECT model_name, model_provider,
       COUNT(*) as total,
       AVG(duration_ms) as avg_ms,
       PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_ms) as p95_ms
FROM item_processing_logs
WHERE duration_ms IS NOT NULL
  AND created_at > NOW() - INTERVAL '7 days'
GROUP BY model_name, model_provider
ORDER BY total DESC;
```

## Integration Points

### Pipeline (pipeline.py)
- Logs `duplicate_check` step during semantic duplicate detection
- Logs `pre_filter` step after classifier processing
- Stores logger reference on item for post-flush logging

### LLM Worker (llm_worker.py)
- Logs `llm_analysis` step after each LLM processing
- Includes timing, priority changes, and full analysis output

### Classifier Worker (classifier_worker.py)
- Logs `classifier_override` step for background classification
- Records priority changes from classifier processing

## Data Retention

Processing logs can grow large. Consider implementing retention policies:

```sql
-- Delete logs older than 30 days
DELETE FROM item_processing_logs
WHERE created_at < NOW() - INTERVAL '30 days';

-- Keep only latest run per item (optional)
DELETE FROM item_processing_logs p
WHERE EXISTS (
    SELECT 1 FROM item_processing_logs p2
    WHERE p2.item_id = p.item_id
      AND p2.processing_run_id > p.processing_run_id
);
```

## Migration

Run the migration to create the table:

```bash
python migrations/add_processing_logs.py
```

The table is also created automatically on startup via SQLAlchemy's `create_all`.
