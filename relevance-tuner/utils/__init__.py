"""
Shared utilities for Liga Hessen relevance tuner.
"""

from .data_loading import (
    get_data_stats,
    load_all_data,
    load_relevant_only,
    load_test_data,
    load_training_data,
)
from .embeddings import (
    BGEM3Embedder,
    BaseEmbedder,
    JinaV3Embedder,
    NomicV2Embedder,
    OllamaEmbedder,
    SentenceTransformerEmbedder,
    get_embedder,
    get_embeddings,
)
from .evaluation import (
    evaluate_ak,
    evaluate_hierarchical,
    evaluate_priority,
    evaluate_relevance,
)

__all__ = [
    # Embeddings
    "OllamaEmbedder",
    "SentenceTransformerEmbedder",
    "NomicV2Embedder",
    "BGEM3Embedder",
    "JinaV3Embedder",
    "BaseEmbedder",
    "get_embedder",
    "get_embeddings",
    # Data loading
    "load_training_data",
    "load_test_data",
    "load_all_data",
    "load_relevant_only",
    "get_data_stats",
    # Evaluation
    "evaluate_relevance",
    "evaluate_priority",
    "evaluate_ak",
    "evaluate_hierarchical",
]
