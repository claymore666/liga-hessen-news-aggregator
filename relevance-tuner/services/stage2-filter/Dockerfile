# Stage-2 junk filter (gbert-base student) - runs on gpu1 only, NOT part of the news-aggregator stack.
FROM python:3.12-slim-bookworm
WORKDIR /app
ENV PIP_NO_CACHE_DIR=1 PYTHONUNBUFFERED=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
COPY requirements.txt .
RUN pip install -r requirements.txt
RUN useradd -m -u 1000 appuser
COPY --chown=appuser:appuser app.py thresholds.json ./
USER appuser
EXPOSE 8083
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8083/health')" || exit 1
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8083"]
