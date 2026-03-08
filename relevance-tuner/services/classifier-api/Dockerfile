# Python 3.11: numpy<2.4 required for docker-ai (no SSE4.2)
FROM python:3.11-slim-bookworm

WORKDIR /app

# Install build dependencies for numpy/sklearn compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && apt-get purge -y --auto-remove build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy application code
COPY . .

# Create data directory and non-root user
RUN mkdir -p /app/data && \
    useradd -r -s /bin/false -d /app appuser && \
    chown -R appuser:appuser /app /app/data
USER appuser

# Expose port
EXPOSE 8082

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8082/health')" || exit 1

# Run the application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8082"]
