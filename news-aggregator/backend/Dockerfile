# Backend Dockerfile - Multi-stage build for better caching
# Stage 1: Base image with system dependencies (rarely changes)
FROM python:3.12-slim AS base

# Install system dependencies (including Playwright browser deps)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    postgresql-client gzip bc \
    openssh-client \
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

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browser (Chromium only)
RUN playwright install chromium

# Stage 3: Application (changes when code changes)
FROM deps AS app

WORKDIR /app

# Copy application code (uses .dockerignore to exclude unnecessary files)
COPY . .

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
# DATABASE_URL must be provided at runtime (PostgreSQL required)

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Run the application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
