# Backend Dockerfile - Multi-stage build for better caching
# Stage 1: Base image with system dependencies (rarely changes)
FROM python:3.12-slim-bookworm AS base

# Install system dependencies (including Playwright browser deps)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    postgresql-client gzip bc \
    openssh-client \
    msmtp msmtp-mta \
    # Playwright/Chromium dependencies
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
    libxrandr2 libgbm1 libasound2 libpango-1.0-0 libcairo2 \
    libx11-6 libx11-xcb1 libxcb1 libxext6 libxshmfence1 \
    fonts-liberation fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

# Stage 2: Dependencies (changes when requirements.txt changes)
FROM base AS deps

WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies and remove build-essential
RUN pip install --no-cache-dir -r requirements.txt \
    && apt-get purge -y --auto-remove build-essential \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir 'setuptools<76'

# Install Playwright browser (Chromium only)
RUN playwright install chromium

# Create non-root user
RUN useradd -m -s /bin/bash appuser

# Stage 3: Application (changes when code changes)
FROM deps AS app

WORKDIR /app

# Copy application code (uses .dockerignore to exclude unnecessary files)
COPY --chown=appuser:appuser . .

# Ensure data directory exists and is writable
RUN mkdir -p /app/data && chown -R appuser:appuser /app/data

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
# DATABASE_URL must be provided at runtime (PostgreSQL required)

USER appuser

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Multi-worker mode via gunicorn for production use.
# Leader election ensures only one worker runs background tasks.
# All workers serve API requests and read status from PostgreSQL.
CMD ["gunicorn", "main:app", "-w", "2", "-k", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8000", "--timeout", "300", "--graceful-timeout", "60"]
