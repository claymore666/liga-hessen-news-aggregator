"""
Embedding Classifier API
FastAPI service for news relevance classification.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from classifier import EmbeddingClassifier

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global classifier instance
classifier: EmbeddingClassifier | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load classifier on startup."""
    global classifier
    logger.info("Loading embedding classifier...")
    try:
        classifier = EmbeddingClassifier.load("models/embedding_classifier_nomic-v2.pkl")
        info = classifier.get_info()
        logger.info(f"Classifier loaded: {info}")

        # Warm up the model with a test prediction
        logger.info("Warming up model...")
        _ = classifier.predict("Test", "Test content", "test")
        logger.info("Model ready!")
    except Exception as e:
        logger.error(f"Failed to load classifier: {e}")
        raise

    yield

    logger.info("Shutting down classifier service")


app = FastAPI(
    title="Embedding Classifier API",
    description="GPU-accelerated news relevance classification",
    version="1.0.0",
    lifespan=lifespan,
)


class ClassifyRequest(BaseModel):
    """Request model for classification."""
    title: str
    content: str
    source: str = ""


class ClassifyResponse(BaseModel):
    """Response model for classification."""
    relevant: bool
    relevance_confidence: float
    priority: str | None = None
    priority_confidence: float | None = None
    ak: str | None = None
    ak_confidence: float | None = None


class HealthResponse(BaseModel):
    """Response model for health check."""
    status: str
    model: str
    gpu: bool
    gpu_name: str | None = None


@app.get("/health", response_model=HealthResponse)
async def health():
    """Health check endpoint."""
    if classifier is None:
        raise HTTPException(status_code=503, detail="Classifier not loaded")

    info = classifier.get_info()
    return HealthResponse(
        status="ok",
        model=info["backend"],
        gpu=info["gpu_available"],
        gpu_name=info["gpu_name"],
    )


@app.post("/classify", response_model=ClassifyResponse)
async def classify(request: ClassifyRequest):
    """
    Classify a news article for relevance.

    Returns relevance, priority, and AK (Arbeitskreis) predictions.
    """
    if classifier is None:
        raise HTTPException(status_code=503, detail="Classifier not loaded")

    try:
        result = classifier.predict(
            title=request.title,
            content=request.content,
            source=request.source,
        )
        return ClassifyResponse(**result)
    except Exception as e:
        logger.error(f"Classification failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/")
async def root():
    """Root endpoint with API info."""
    return {
        "service": "Embedding Classifier API",
        "version": "1.0.0",
        "endpoints": {
            "/health": "Health check",
            "/classify": "Classify article (POST)",
        },
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8081)
