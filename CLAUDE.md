# Project Knowledge

## Git Branching Workflow

**CRITICAL**: Always follow this branching strategy for this project.

```
main (production-ready)
  └── dev (integration branch)
        └── milestone/X-name (feature work)
```

### Workflow Rules
1. **Never commit directly to `main` or `dev`**
2. **Always work on a milestone branch** (e.g., `milestone/1-core-backend`)
3. When milestone work is complete:
   - Create PR: `milestone/X` → `dev`
   - Review and merge
4. When ready for release:
   - Create PR: `dev` → `main`
   - Review and merge

### Branch Naming Convention
- `milestone/1-core-backend`
- `milestone/2-connector-system`
- `milestone/3-llm-integration`
- `milestone/4-vue-frontend`
- `milestone/5-deployment`

## Project Structure

- `docs/` - Architecture documentation
- `news-aggregator/` - Application code
  - `backend/` - Python/FastAPI
  - `frontend/` - Vue 3/Vite

## Tech Stack

| Layer | Technology |
|-------|------------|
| Frontend | Vue 3 + Vite + TailwindCSS + Pinia |
| Backend | Python 3.12 + FastAPI + SQLAlchemy |
| Database | SQLite |
| LLM | Ollama (gpu1) + OpenRouter (fallback) |

## Testing Requirements

**IMPORTANT**: Always create tests for each feature.

- Backend: pytest + pytest-asyncio
- Run tests before committing: `pytest tests/`
- Test files mirror source structure: `tests/test_<module>.py`
- Minimum coverage for new features

## GitHub Repository

https://github.com/claymore666/liga-hessen-news-aggregator

See `docs/INDEX.md` for full documentation index.
