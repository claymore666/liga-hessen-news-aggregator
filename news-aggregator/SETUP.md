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

# Create data directory
mkdir -p data

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

### Production Server

| Property | Value |
|----------|-------|
| Host | VM 112 (docker-ai) at 192.168.0.124 |
| SSH | `ssh kamienc@192.168.0.124` |
| Project Path | `/home/kamienc/projects/liga-hessen-news-aggregator/news-aggregator/` |
| Compose File | `docker-compose.prod.yml` |
| LLM Server | gpu1 (192.168.0.141:11434) with model `liga-relevance` |

### Production Architecture

| Component | Container | Port | Notes |
|-----------|-----------|------|-------|
| Backend | liga-news-backend | 8000 | Python/FastAPI |
| Frontend | liga-news-frontend | 3001 | Vue 3 via nginx |
| Database | SQLite | - | Volume: `liga-news-data` at `/app/data/liga_news.db` |
| LLM | External | - | Ollama on gpu1 |

### Deployment Commands

```bash
# SSH to production
ssh kamienc@192.168.0.124

# Navigate to project
cd /home/kamienc/projects/liga-hessen-news-aggregator/news-aggregator

# Pull latest changes
git pull origin main

# Rebuild and restart (after code changes)
sudo docker compose -f docker-compose.prod.yml build --no-cache
sudo docker compose -f docker-compose.prod.yml up -d

# Quick restart (no rebuild)
sudo docker compose -f docker-compose.prod.yml up -d

# Stop containers
sudo docker compose -f docker-compose.prod.yml down

# View logs
sudo docker logs -f liga-news-backend
sudo docker logs -f liga-news-frontend

# Check status
sudo docker ps -a | grep liga-news
```

### Data Persistence

- **Volume**: `liga-news-data` mounted at `/app/data` in backend container
- **Database**: SQLite at `/var/lib/docker/volumes/liga-news-data/_data/liga_news.db`
- Data survives container rebuilds

### Security Checklist

- [ ] Change `SECRET_KEY` in `.env`
- [ ] Configure proper CORS origins
- [ ] Configure backup for SQLite database
- [ ] Set appropriate log levels

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
