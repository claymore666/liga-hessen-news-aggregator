# Liga Hessen News Aggregator - Setup Guide

## Quick Start with Docker

### Prerequisites
- Docker and Docker Compose installed
- (Optional) Ollama for local LLM inference

### 1. Clone and Configure

```bash
# Clone the repository
git clone https://github.com/claymore666/liga-hessen-news-aggregator.git
cd liga-hessen-news-aggregator/news-aggregator

# Copy environment template
cp .env.example .env

# Edit configuration (optional)
nano .env
```

### 2. Start the Application

```bash
# Build and start all services
docker compose up -d

# View logs
docker compose logs -f
```

### 3. Access the Application

- **Frontend**: http://localhost:3000
- **Backend API**: http://localhost:8000
- **API Documentation**: http://localhost:8000/docs

## Manual Development Setup

### Backend

```bash
cd backend

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Ensure PostgreSQL is running and configured in .env

# Start development server
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### Frontend

```bash
cd frontend

# Install dependencies
npm install

# Start development server
npm run dev
```

## Configuration

### LLM Providers

#### Ollama (Recommended for local inference)

1. Install Ollama: https://ollama.ai/download
2. Pull a model: `ollama pull llama3.2`
3. Configure in `.env`:
   ```
   OLLAMA_URL=http://localhost:11434
   OLLAMA_MODEL=llama3.2
   ```

#### OpenRouter (Cloud fallback)

1. Get API key: https://openrouter.ai/
2. Configure in `.env`:
   ```
   OPENROUTER_API_KEY=sk-or-...
   OPENROUTER_MODEL=mistralai/mistral-7b-instruct:free
   ```

### Adding News Sources

1. Navigate to **Quellen** (Sources) in the web interface
2. Click **Neue Quelle** (New Source)
3. Select connector type:
   - **RSS/Atom**: Standard RSS feeds
   - **HTML Scraper**: Web pages with CSS selectors
   - **Bluesky/Twitter/Mastodon**: Social media feeds
   - **PDF**: Document parsing
4. Enter URL and validate connection
5. Set fetch interval (default: 60 minutes)

### Configuring Rules

1. Navigate to **Regeln** (Rules) in the web interface
2. Click **Neue Regel** (New Rule)
3. Configure rule type:
   - **Keyword**: Comma-separated list (case-insensitive)
   - **Regex**: Python-compatible regular expression
   - **Semantic**: LLM-based question (yes/no answer)
4. Set priority boost or target priority
5. Enable/disable as needed

## Production Deployment

### Infrastructure Overview

| Server | IP | Role |
|--------|-----|------|
| docker-ai | 192.168.0.124 | Production host (backend, frontend, PostgreSQL) |
| gpu1 | 192.168.0.141 | LLM processing (Ollama) + ML classifier |

### Production Server (docker-ai)

| Property | Value |
|----------|-------|
| Host | VM 112 (docker-ai) at 192.168.0.124 |
| SSH | `ssh kamienc@192.168.0.124` |
| Project Path | `/home/kamienc/projects/liga-hessen-news-aggregator/news-aggregator/` |
| Compose File | `docker-compose.prod.yml` |
| LAN Interface | `ens18` (for macvlan network) |

### GPU Server (gpu1)

| Property | Value |
|----------|-------|
| Host | gpu1 at 192.168.0.141 |
| MAC Address | `58:47:ca:7c:18:cc` (for Wake-on-LAN) |
| Ollama | Port 11434, model `qwen3:14b-q8_0` |
| Classifier | Port 8082 |
| WoL User | `ligahessen` (passwordless shutdown) |

### Production Architecture

| Component | Container | Port | Notes |
|-----------|-----------|------|-------|
| Backend | liga-news-backend | 8000 | Python/FastAPI + WoL support |
| Frontend | liga-news-frontend | 3001 | Vue 3 via nginx |
| Database | liga-news-db | 5432 | PostgreSQL 17 |
| LLM | External (gpu1) | 11434 | Ollama (woken via WoL if sleeping) |
| Classifier | External (gpu1) | 8082 | ML embedding classifier |

### Network Configuration

The backend container uses a macvlan network to send Wake-on-LAN packets:
- Container IP: `192.168.0.200` on the LAN
- Can reach gpu1 directly and send broadcast packets
- Uses default bridge network for database connectivity

### Deployment Commands

```bash
# SSH to production
ssh kamienc@192.168.0.124

# Navigate to project
cd /home/kamienc/projects/liga-hessen-news-aggregator/news-aggregator

# Pull latest changes
git pull origin dev

# Rebuild and restart (after code changes)
docker compose -f docker-compose.prod.yml down
docker compose -f docker-compose.prod.yml up -d --build

# Quick restart (no rebuild)
docker compose -f docker-compose.prod.yml restart

# View logs
docker logs -f liga-news-backend
docker logs -f liga-news-frontend

# Check status
docker ps | grep liga-news
```

### Database Operations

```bash
# Export database from gpu1 (source)
docker exec liga-news-db pg_dump -U liga -d liga_news --clean --if-exists > /tmp/liga_news_dump.sql

# Import on docker-ai (target)
docker exec -i liga-news-db psql -U liga -d liga_news < /tmp/liga_news_dump.sql

# Check database stats
docker exec liga-news-db psql -U liga -d liga_news -c "SELECT COUNT(*) FROM items;"
```

### Wake-on-LAN Configuration

The system automatically wakes gpu1 when LLM processing is needed:

| Setting | Value | Description |
|---------|-------|-------------|
| `GPU1_WOL_ENABLED` | `true` | Enable WoL feature |
| `GPU1_AUTO_SHUTDOWN` | `true` | Shutdown gpu1 after idle |
| `GPU1_IDLE_TIMEOUT` | `300` | 5 minutes before auto-shutdown |

SSH key for shutdown is stored in `./ssh/id_ed25519` (mounted read-only).

See [docs/operations/GPU1_POWER_MANAGEMENT.md](docs/operations/GPU1_POWER_MANAGEMENT.md) for details.

### Data Persistence

| Volume | Container | Purpose |
|--------|-----------|---------|
| `liga-news-postgres` | liga-news-db | PostgreSQL data |
| `liga-news-data` | liga-news-backend | Backend data files |

### Security Checklist

- [x] PostgreSQL password set in `.env`
- [x] SSH key for gpu1 shutdown (read-only mount)
- [ ] Configure backup for PostgreSQL database
- [ ] Set appropriate log levels for production

## Troubleshooting

### Backend won't start

```bash
# Check logs
docker compose logs backend

# Verify database permissions
ls -la backend/data/
```

### LLM not working

```bash
# Test Ollama connection
curl http://localhost:11434/api/tags

# Check if model is available
ollama list
```

### Frontend can't reach backend

```bash
# Verify backend is healthy
curl http://localhost:8000/health

# Check docker network
docker compose ps
```

## Support

For issues and feature requests, visit:
https://github.com/claymore666/liga-hessen-news-aggregator/issues
