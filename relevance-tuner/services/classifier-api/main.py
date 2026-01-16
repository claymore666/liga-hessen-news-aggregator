"""
Embedding Classifier API
FastAPI service for news relevance classification and semantic search.
"""

import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from classifier import EmbeddingClassifier, VectorStore, DuplicateStore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global instances
classifier: EmbeddingClassifier | None = None
vector_store: VectorStore | None = None
duplicate_store: DuplicateStore | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load classifier, vector store, and duplicate store on startup."""
    global classifier, vector_store, duplicate_store
    logger.info("Loading embedding classifier...")
    try:
        classifier = EmbeddingClassifier.load("models/embedding_classifier_nomic-v2.pkl")
        info = classifier.get_info()
        logger.info(f"Classifier loaded: {info}")

        # Initialize vector store (shares embedder with classifier)
        logger.info("Initializing vector store...")
        vector_store = VectorStore(
            embedder=classifier.embedder,
            persist_dir="/app/data/vectordb",
        )
        logger.info(f"Vector store ready: {vector_store.get_stats()}")

        # Initialize duplicate store (separate paraphrase embedder)
        logger.info("Initializing duplicate store (paraphrase model)...")
        duplicate_store = DuplicateStore(
            persist_dir="/app/data/duplicatedb",
        )
        logger.info(f"Duplicate store ready: {duplicate_store.get_stats()}")

        # Warm up the models with test predictions
        logger.info("Warming up models...")
        _ = classifier.predict("Test", "Test content", "test")
        _ = duplicate_store.find_duplicates("Test", "Test content")
        logger.info("Models ready!")
    except Exception as e:
        logger.error(f"Failed to load classifier: {e}")
        raise

    yield

    logger.info("Shutting down classifier service")


app = FastAPI(
    title="Embedding Classifier API",
    description="GPU-accelerated news relevance classification and semantic search",
    version="2.0.0",
    lifespan=lifespan,
)


# ============== Request/Response Models ==============

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
    ak: str | None = None  # Primary AK (backward compatibility)
    ak_confidence: float | None = None
    aks: list[str] = []  # Multi-label: all predicted AKs
    ak_confidences: dict[str, float] = {}  # Confidence per AK


class SearchRequest(BaseModel):
    """Request model for semantic search."""
    query: str
    n_results: int = 10
    source: Optional[str] = None  # Filter by source


class SearchResult(BaseModel):
    """Single search result."""
    id: str
    title: str
    score: float
    snippet: str
    metadata: dict


class SearchResponse(BaseModel):
    """Response model for search."""
    query: str
    results: list[SearchResult]
    total_in_store: int


class SimilarRequest(BaseModel):
    """Request model for similar items."""
    item_id: str
    n_results: int = 5
    exclude_same_source: bool = True


class DuplicateRequest(BaseModel):
    """Request model for finding semantic duplicates."""
    title: str
    content: str
    threshold: float = 0.75  # Cosine similarity threshold (paraphrase model)
    n_results: int = 5


class DuplicateResponse(BaseModel):
    """Response model for duplicate detection."""
    duplicates: list[SearchResult]
    has_duplicates: bool


class IndexRequest(BaseModel):
    """Request model for indexing a single item."""
    id: str
    title: str
    content: str
    metadata: Optional[dict] = None


class IndexBatchRequest(BaseModel):
    """Request model for batch indexing."""
    items: list[IndexRequest]


class IndexResponse(BaseModel):
    """Response model for indexing."""
    indexed: int
    total_in_store: int


class HealthResponse(BaseModel):
    """Response model for health check."""
    status: str
    model: str
    gpu: bool
    gpu_name: str | None = None
    vector_store_items: int = 0
    duplicate_store_items: int = 0
    duplicate_model: str | None = None


class StorageSizeResponse(BaseModel):
    """Response model for storage sizes."""
    vector_store_size_bytes: int
    vector_store_items: int
    duplicate_store_size_bytes: int
    duplicate_store_items: int


# ============== Endpoints ==============

@app.get("/health", response_model=HealthResponse)
async def health():
    """Health check endpoint."""
    if classifier is None:
        raise HTTPException(status_code=503, detail="Classifier not loaded")

    info = classifier.get_info()
    vs_items = vector_store.get_stats()["total_items"] if vector_store else 0
    ds_stats = duplicate_store.get_stats() if duplicate_store else {}

    return HealthResponse(
        status="ok",
        model=info["backend"],
        gpu=info["gpu_available"],
        gpu_name=info["gpu_name"],
        vector_store_items=vs_items,
        duplicate_store_items=ds_stats.get("total_items", 0),
        duplicate_model=ds_stats.get("model"),
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


@app.post("/search", response_model=SearchResponse)
async def search(request: SearchRequest):
    """
    Semantic search for articles matching a query.

    Returns articles ranked by semantic similarity to the query.
    """
    if vector_store is None:
        raise HTTPException(status_code=503, detail="Vector store not initialized")

    try:
        # Build filter if source specified
        filter_metadata = {"source": request.source} if request.source else None

        results = vector_store.search(
            query=request.query,
            n_results=request.n_results,
            filter_metadata=filter_metadata,
        )

        return SearchResponse(
            query=request.query,
            results=[SearchResult(**r) for r in results],
            total_in_store=vector_store.get_stats()["total_items"],
        )
    except Exception as e:
        logger.error(f"Search failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/similar", response_model=SearchResponse)
async def similar(request: SimilarRequest):
    """
    Find articles similar to a given article.

    Returns articles ranked by semantic similarity, optionally excluding same source.
    """
    if vector_store is None:
        raise HTTPException(status_code=503, detail="Vector store not initialized")

    try:
        results = vector_store.find_similar(
            item_id=request.item_id,
            n_results=request.n_results,
            exclude_same_source=request.exclude_same_source,
        )

        return SearchResponse(
            query=f"similar to {request.item_id}",
            results=[SearchResult(**r) for r in results],
            total_in_store=vector_store.get_stats()["total_items"],
        )
    except Exception as e:
        logger.error(f"Similar search failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/find-duplicates", response_model=DuplicateResponse)
async def find_duplicates(request: DuplicateRequest):
    """
    Find semantically similar items that may be duplicates.

    Uses paraphrase-multilingual-mpnet model for better same-story detection.
    Default threshold 0.75 catches same-story articles with different wording.

    Used during ingestion to detect cross-channel duplicates like:
    - RSS: "Title of Article" from Source A
    - RSS: "Same Story Different Words" from Source B
    """
    if duplicate_store is None:
        raise HTTPException(status_code=503, detail="Duplicate store not initialized")

    try:
        # Use dedicated duplicate store with paraphrase embeddings
        duplicates = duplicate_store.find_duplicates(
            title=request.title,
            content=request.content,
            threshold=request.threshold,
            n_results=request.n_results,
        )

        return DuplicateResponse(
            duplicates=[SearchResult(**r) for r in duplicates],
            has_duplicates=len(duplicates) > 0,
        )
    except Exception as e:
        logger.error(f"Duplicate search failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/index", response_model=IndexResponse)
async def index_item(request: IndexRequest):
    """
    Index a single article for semantic search and duplicate detection.

    Articles are indexed in both stores:
    - Vector store (nomic): for semantic search and similarity
    - Duplicate store (paraphrase): for duplicate detection
    """
    if vector_store is None:
        raise HTTPException(status_code=503, detail="Vector store not initialized")

    try:
        added = vector_store.add_item(
            item_id=request.id,
            title=request.title,
            content=request.content,
            metadata=request.metadata,
        )

        # Also add to duplicate store
        if duplicate_store:
            duplicate_store.add_item(
                item_id=request.id,
                title=request.title,
                content=request.content,
                metadata=request.metadata,
            )

        return IndexResponse(
            indexed=1 if added else 0,
            total_in_store=vector_store.get_stats()["total_items"],
        )
    except Exception as e:
        logger.error(f"Indexing failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/index/batch", response_model=IndexResponse)
async def index_batch(request: IndexBatchRequest):
    """
    Index multiple articles in batch.

    More efficient than indexing one by one for bulk operations.
    Indexes to both vector store (semantic search) and duplicate store.
    """
    if vector_store is None:
        raise HTTPException(status_code=503, detail="Vector store not initialized")

    try:
        items = [
            {
                "id": item.id,
                "title": item.title,
                "content": item.content,
                "metadata": item.metadata,
            }
            for item in request.items
        ]

        added = vector_store.add_items_batch(items)

        # Also add to duplicate store
        if duplicate_store:
            duplicate_store.add_items_batch(items)

        return IndexResponse(
            indexed=added,
            total_in_store=vector_store.get_stats()["total_items"],
        )
    except Exception as e:
        logger.error(f"Batch indexing failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/")
async def root():
    """Root endpoint with API info."""
    return {
        "service": "Embedding Classifier API",
        "version": "2.0.0",
        "endpoints": {
            "/health": "Health check (GET)",
            "/storage": "Storage sizes (GET)",
            "/classify": "Classify article relevance (POST)",
            "/search": "Semantic search (POST)",
            "/similar": "Find similar articles (POST)",
            "/index": "Index single article (POST)",
            "/index/batch": "Batch index articles (POST)",
        },
    }


def _get_dir_size(path: str) -> int:
    """Get total size of a directory in bytes."""
    import os
    total = 0
    if os.path.exists(path):
        for dirpath, dirnames, filenames in os.walk(path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                try:
                    total += os.path.getsize(fp)
                except OSError:
                    pass
    return total


@app.get("/storage", response_model=StorageSizeResponse)
async def get_storage_sizes():
    """Get storage sizes for vector stores.

    Returns disk usage for vector store and duplicate store directories.
    """
    vs_size = _get_dir_size("/app/data/vectordb")
    ds_size = _get_dir_size("/app/data/duplicatedb")

    vs_items = vector_store.get_stats()["total_items"] if vector_store else 0
    ds_items = duplicate_store.get_stats()["total_items"] if duplicate_store else 0

    return StorageSizeResponse(
        vector_store_size_bytes=vs_size,
        vector_store_items=vs_items,
        duplicate_store_size_bytes=ds_size,
        duplicate_store_items=ds_items,
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8082)
