# Documentation Index

This documentation is organized in layers from high-level architecture to specific implementation details.

## Architecture
- [OVERVIEW.md](architecture/OVERVIEW.md) - System architecture and components
- [DATA_FLOW.md](architecture/DATA_FLOW.md) - Data processing pipeline
- [DATABASE.md](architecture/DATABASE.md) - Database schema and models

## Services
- [SCHEDULER.md](services/SCHEDULER.md) - Parallel fetching scheduler
- [LLM_PIPELINE.md](services/LLM_PIPELINE.md) - LLM analysis pipeline
- [CLASSIFIER.md](services/CLASSIFIER.md) - ML classifier integration

## Connectors
- [OVERVIEW.md](connectors/OVERVIEW.md) - Connector system overview
- [ADDING_CONNECTORS.md](connectors/ADDING_CONNECTORS.md) - How to add new connectors

## API
- [ENDPOINTS.md](api/ENDPOINTS.md) - REST API reference

## Operations
- [TROUBLESHOOTING.md](operations/TROUBLESHOOTING.md) - Common issues and fixes
- [MONITORING.md](operations/MONITORING.md) - Health checks and metrics

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
| `CLASSIFIER_API_URL` | Classifier endpoint | http://localhost:8082 |
| `POSTGRES_HOST` | Database host | localhost |
| `POSTGRES_DB` | Database name | liga_news |
