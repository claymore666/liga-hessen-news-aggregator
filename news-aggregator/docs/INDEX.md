# Documentation Index

This documentation is organized in layers from high-level architecture to specific implementation details.

## Architecture
- [OVERVIEW.md](architecture/OVERVIEW.md) - System architecture and components
- [DATA_FLOW.md](architecture/DATA_FLOW.md) - Data processing pipeline
- [DATABASE.md](architecture/DATABASE.md) - Database schema and models
- [DEDUPLICATION.md](architecture/DEDUPLICATION.md) - Duplicate detection system
- [PROCESSING_ANALYTICS.md](architecture/PROCESSING_ANALYTICS.md) - Processing logs and model comparison

## Services
- [SCHEDULER.md](services/SCHEDULER.md) - Parallel fetching scheduler
- [LLM_PIPELINE.md](services/LLM_PIPELINE.md) - LLM analysis pipeline
- [CLASSIFIER.md](services/CLASSIFIER.md) - ML classifier integration
- [PROMPT_TUNING.md](services/PROMPT_TUNING.md) - LLM prompt evolution, quality metrics, and model benchmarks
- [TOPIC_TAXONOMY.md](services/TOPIC_TAXONOMY.md) - Topic classification system and how to add new topics
- [BROWSER_POOL.md](services/BROWSER_POOL.md) - Shared Playwright instance management
- [ARTICLE_EXTRACTOR.md](services/ARTICLE_EXTRACTOR.md) - Content extraction with SPA fallback

## Connectors
- [OVERVIEW.md](connectors/OVERVIEW.md) - Connector system overview
- [ADDING_CONNECTORS.md](connectors/ADDING_CONNECTORS.md) - How to add new connectors
- [X_SCRAPER_POC.md](connectors/X_SCRAPER_POC.md) - X.com HTTP scraping PoC results

## API
- [ENDPOINTS.md](api/ENDPOINTS.md) - REST API reference

## Operations
- [TROUBLESHOOTING.md](operations/TROUBLESHOOTING.md) - Common issues and fixes
- [MONITORING.md](operations/MONITORING.md) - Health checks and metrics
- [GPU1_POWER_MANAGEMENT.md](operations/GPU1_POWER_MANAGEMENT.md) - Wake-on-LAN for LLM processing
- [CPU_ASSESSMENT.md](operations/CPU_ASSESSMENT.md) - CPU usage analysis and optimizations
- [MOTD.md](operations/MOTD.md) - Message of the day for users
- [CLOUD_LLM_COST_ANALYSIS.md](CLOUD_LLM_COST_ANALYSIS.md) - LLM hosting cost comparison

## Quick Reference

### Key Files
| File | Purpose |
|------|---------|
| `backend/models.py` | SQLAlchemy database models |
| `backend/config.py` | Application settings |
| `backend/services/scheduler.py` | Fetch scheduling |
| `backend/services/pipeline.py` | Item processing |
| `backend/connectors/` | Source-specific fetchers |

### Environment Variables
| Variable | Description | Default |
|----------|-------------|---------|
| `OLLAMA_BASE_URL` | LLM API endpoint | http://localhost:11434 |
| `OLLAMA_MODEL` | LLM model name | qwen3:14b-q8_0 |
| `CLASSIFIER_URL` | Classifier endpoint | http://gpu1:8082 |
| `BROWSER_POOL_MAX` | Max concurrent Playwright browsers | 2 |
| `SCHEDULER_ENABLED` | Enable fetch scheduler | true (prod) / false (QA) |
| `LLM_WORKER_ENABLED` | Enable LLM processing worker | true (prod) / false (QA) |
| `CLASSIFIER_WORKER_ENABLED` | Enable classifier worker | true (prod) / false (QA) |
| `GPU1_ACTIVE_HOURS_START` | Hour when gpu1 WoL allowed | 7 |
| `GPU1_ACTIVE_HOURS_END` | Hour when gpu1 WoL stops | 16 |
