# API Reference

Base URL: `http://localhost:8000/api`

## Items

### List Items
```http
GET /items
```

**Query Parameters**:
| Parameter | Type | Description |
|-----------|------|-------------|
| page | int | Page number (default: 1) |
| page_size | int | Items per page (default: 50, max: 100) |
| priority | string | Filter by priority (high/medium/low/none) |
| source_id | int | Filter by source |
| channel_id | int | Filter by channel |
| is_read | bool | Filter by read status |
| is_archived | bool | Filter by archived status |
| search | string | Full-text search |
| sort_by | string | Sort field (published_at, created_at, priority_score) |
| sort_order | string | Sort direction (asc/desc) |

**Response**:
```json
{
  "items": [...],
  "total": 1000,
  "page": 1,
  "page_size": 50
}
```

### Get Item
```http
GET /items/{item_id}
```

### Update Item
```http
PATCH /items/{item_id}
```

**Body**:
```json
{
  "priority": "high",
  "assigned_aks": ["AK1", "AK3"],
  "is_starred": true
}
```

### Mark as Read
```http
POST /items/{item_id}/read
```

### Archive Item
```http
POST /items/{item_id}/archive
```

### Reprocess with LLM
```http
POST /items/{item_id}/reprocess
```

Queues item for LLM re-analysis.

### Refetch Content
```http
POST /items/{item_id}/refetch
```

Re-fetches content from source URL.

### Mark All Read
```http
POST /items/mark-all-read
```

### Batch Reprocess
```http
POST /items/reprocess
```

**Body**:
```json
{
  "item_ids": [1, 2, 3]
}
```

### Get Item History
```http
GET /items/{item_id}/history
```

Returns audit log for item.

### LLM Retry Queue
```http
GET /items/retry-queue
POST /items/retry-queue/process
```

Items that failed LLM processing.

## Sources

### List Sources
```http
GET /sources
```

### Get Sources with Errors
```http
GET /sources/errors
```

### Get Source
```http
GET /sources/{source_id}
```

### Create Source
```http
POST /sources
```

**Body**:
```json
{
  "name": "Organization Name",
  "slug": "org-slug",
  "description": "Description",
  "website": "https://example.com"
}
```

### Update Source
```http
PATCH /sources/{source_id}
```

### Delete Source
```http
DELETE /sources/{source_id}
```

### Enable/Disable Source
```http
POST /sources/{source_id}/enable
POST /sources/{source_id}/disable
```

### Fetch All Channels
```http
POST /sources/{source_id}/fetch-all
```

**Query Parameters**:
| Parameter | Type | Description |
|-----------|------|-------------|
| training_mode | bool | Skip LLM processing |

### Fetch All Sources
```http
POST /sources/fetch-all
```

Triggers fetch for all enabled sources.

## Channels

### Create Channel
```http
POST /sources/{source_id}/channels
```

**Body**:
```json
{
  "name": "Channel Name",
  "connector_type": "rss",
  "config": {
    "url": "https://example.com/feed.xml",
    "follow_links": true
  },
  "fetch_interval_minutes": 60
}
```

### Get Channel
```http
GET /channels/{channel_id}
```

### Update Channel
```http
PATCH /channels/{channel_id}
```

### Delete Channel
```http
DELETE /channels/{channel_id}
```

### Fetch Channel
```http
POST /channels/{channel_id}/fetch
```

### Enable/Disable Channel
```http
POST /channels/{channel_id}/enable
POST /channels/{channel_id}/disable
```

## Connectors

### List Connectors
```http
GET /connectors
```

Returns all available connector types with their config schemas.

### Get Connector
```http
GET /connectors/{connector_type}
```

### Validate Config
```http
POST /connectors/{connector_type}/validate
```

**Body**: Connector-specific config JSON

## Rules

### List Rules
```http
GET /rules
```

### Get Rule
```http
GET /rules/{rule_id}
```

### Create Rule
```http
POST /rules
```

**Body**:
```json
{
  "name": "Rule Name",
  "rule_type": "keyword",
  "pattern": "search pattern",
  "target_priority": "high",
  "target_ak": "AK1",
  "enabled": true
}
```

### Update Rule
```http
PATCH /rules/{rule_id}
```

### Delete Rule
```http
DELETE /rules/{rule_id}
```

### Test Rule
```http
POST /rules/{rule_id}/test
```

Tests rule against recent items.

### Reorder Rules
```http
POST /rules/reorder
```

**Body**:
```json
[
  {"id": 1, "order": 0},
  {"id": 2, "order": 1}
]
```

## LLM

### Get Status
```http
GET /llm/status
```

**Response**:
```json
{
  "enabled": true,
  "model": "qwen3:14b-q8_0",
  "available": true,
  "queue_size": 15
}
```

### List Models
```http
GET /llm/models
```

Returns available Ollama models.

### Get Settings
```http
GET /llm/settings
```

### Select Model
```http
PUT /llm/model
```

**Body**:
```json
{
  "model": "qwen3:14b-q8_0"
}
```

### Enable/Disable LLM
```http
POST /llm/enable
POST /llm/disable
```

### Test Prompt
```http
POST /llm/prompt
```

**Body**:
```json
{
  "prompt": "Test prompt",
  "system": "System message"
}
```

### Worker Control
```http
GET /llm/worker/status
POST /llm/worker/pause
POST /llm/worker/resume
```

## Classifier

### Worker Status
```http
GET /classifier/worker/status
```

### Worker Control
```http
POST /classifier/worker/pause
POST /classifier/worker/resume
```

### Unclassified Count
```http
GET /classifier/unclassified/count
```

## Scheduler

### Get Status
```http
GET /scheduler/status
```

**Response**:
```json
{
  "running": true,
  "interval_minutes": 5,
  "next_run": "2024-01-15T10:30:00Z"
}
```

### Start/Stop
```http
POST /scheduler/start
POST /scheduler/stop
```

### Set Interval
```http
PUT /scheduler/interval
```

**Body**:
```json
{
  "interval_minutes": 10
}
```

## Admin

### Health Check
```http
GET /admin/health
```

**Response**:
```json
{
  "status": "ok",
  "database": "connected",
  "scheduler_running": true,
  "llm_enabled": true,
  "llm_available": true,
  "classifier_available": true,
  "item_count": 3152,
  "unprocessed_count": 15
}
```

### System Stats
```http
GET /admin/stats
```

Comprehensive system statistics.

### Database Stats
```http
GET /admin/db-stats
```

### Logs
```http
GET /admin/logs
```

**Query Parameters**:
| Parameter | Type | Description |
|-----------|------|-------------|
| level | string | Filter by level (INFO/WARNING/ERROR) |
| limit | int | Max lines (default: 100) |

### Delete Items
```http
DELETE /admin/items
```

**Query Parameters**: Filter items to delete

### Delete Old Items
```http
DELETE /admin/items/old
```

**Query Parameters**:
| Parameter | Type | Description |
|-----------|------|-------------|
| days | int | Delete items older than N days |

### Delete by Source
```http
DELETE /admin/items/source/{source_id}
```

### Delete Low Priority
```http
DELETE /admin/items/low-priority
```

### Re-analyze Items
```http
POST /admin/reanalyze-items
```

Queues items for LLM re-processing.

### Classify Items
```http
POST /admin/classify-items
```

Runs ML classifier on items.

### Worker Control
```http
POST /admin/scheduler/start
POST /admin/scheduler/stop
POST /admin/llm-worker/start
POST /admin/llm-worker/stop
POST /admin/llm-worker/pause
POST /admin/llm-worker/resume
POST /admin/classifier-worker/start
POST /admin/classifier-worker/stop
POST /admin/classifier-worker/pause
POST /admin/classifier-worker/resume
```

### Housekeeping
```http
GET /admin/housekeeping
PUT /admin/housekeeping
POST /admin/housekeeping/preview
POST /admin/housekeeping/cleanup
```

Configure and run data cleanup.

### Storage Stats
```http
GET /admin/storage
```

Returns storage usage for PostgreSQL and vector stores.

## Stats

### Overview Stats
```http
GET /stats
```

### By Source
```http
GET /stats/by-source
```

### By Channel
```http
GET /stats/by-channel
```

### By Connector
```http
GET /stats/by-connector
```

### By Priority
```http
GET /stats/by-priority
```

## Email

### Send Briefing
```http
POST /send-briefing
```

### Preview Briefing
```http
POST /preview-briefing
```

### Test Email
```http
POST /email/test
```

## Proxies

### Get Next Proxy
```http
GET /proxies/next
```

### Proxy Status
```http
GET /proxies/status
```

### Refresh Proxies
```http
POST /proxies/refresh
```

## Config (Import/Export)

### Export Config
```http
GET /admin/config/export
```

Exports sources, channels, and rules.

### Validate Config
```http
POST /admin/config/validate
```

### Import Config
```http
POST /admin/config/import
```

## Error Responses

All endpoints return errors in this format:

```json
{
  "detail": "Error message"
}
```

**Status Codes**:
| Code | Meaning |
|------|---------|
| 400 | Bad Request - Invalid parameters |
| 404 | Not Found - Resource doesn't exist |
| 422 | Validation Error - Invalid body |
| 500 | Server Error - Internal error |

## OpenAPI Documentation

Interactive API docs available at:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
- OpenAPI JSON: `http://localhost:8000/openapi.json`
