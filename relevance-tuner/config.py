#!/usr/bin/env python3
"""
Central Configuration for Liga Hessen Relevance Tuner

All shared settings, paths, and constants are defined here.
Environment variables can override defaults.
"""

import os
from pathlib import Path

# ============================================================================
# Paths
# ============================================================================

PROJECT_ROOT = Path(__file__).parent
DATA_DIR = PROJECT_ROOT / "data" / "final"
MODELS_DIR = PROJECT_ROOT / "models"

# ============================================================================
# Embedding Model (Ollama)
# ============================================================================

EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "nomic-embed-text:137m-v1.5-fp16")
EMBEDDING_NUM_CTX = int(os.environ.get("EMBEDDING_NUM_CTX", 8192))
EMBEDDING_DIMS = 768  # nomic-embed-text produces 768-dim vectors
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

# Chunking settings for long texts (nomic handles ~11,937 chars max)
EMBEDDING_CHUNK_SIZE = int(os.environ.get("EMBEDDING_CHUNK_SIZE", 10000))
EMBEDDING_CHUNK_OVERLAP = int(os.environ.get("EMBEDDING_CHUNK_OVERLAP", 500))

# ============================================================================
# TF-IDF Settings (for TF-IDF approaches)
# ============================================================================

TFIDF_MAX_FEATURES = int(os.environ.get("TFIDF_MAX_FEATURES", 3000))
TFIDF_NGRAM_RANGE = (1, 2)
TFIDF_MIN_DF = 2
TFIDF_MAX_DF = float(os.environ.get("TFIDF_MAX_DF", 0.9))
TFIDF_SUBLINEAR_TF = True

# ============================================================================
# Classifier Settings
# ============================================================================

RANDOM_SEED = 42

# LogisticRegression defaults
LR_MAX_ITER = 1000
LR_C = 1.0
LR_SOLVER = "lbfgs"

# RandomForest defaults
RF_N_ESTIMATORS = 200
RF_MAX_DEPTH = 15

# ============================================================================
# Classification Labels
# ============================================================================

PRIORITY_LEVELS = ["low", "medium", "high"]
AK_CLASSES = ["AK1", "AK2", "AK3", "AK4", "AK5", "QAG"]

# AK Definitions (for reference)
AK_DEFINITIONS = {
    "AK1": "Grundsatz und Sozialpolitik (general social policy)",
    "AK2": "Migration und Flucht (migration, refugees, asylum)",
    "AK3": "Gesundheit, Pflege und Senioren (health, care, elderly)",
    "AK4": "Eingliederungshilfe (disability inclusion)",
    "AK5": "Kinder, Jugend, Frauen und Familie (children, youth, families)",
    "QAG": "Querschnitt - Digitalisierung, Klimaschutz, Wohnen (cross-cutting)",
}

# ============================================================================
# Embedding Backend Configurations
# ============================================================================
# Each backend has its own tunable parameters for both the embedding model
# and the classifier on top of it.
#
# Status values:
#   - "production": Known good, tested, recommended
#   - "tested": Works, has metrics, may need tuning
#   - "experimental": Untested or in development

BACKEND_CONFIGS = {
    "sentence-transformers": {
        "status": "production",  # Fast, good baseline
        "type": "sentence-transformers",
        "model": "paraphrase-multilingual-MiniLM-L12-v2",
        "embedding_dim": 384,
        "max_length": 1500,  # Characters (~128 tokens)
        # Classifier settings (tuned for 384-dim embeddings)
        "lr_c": 0.5,
        "lr_max_iter": 1000,
        "rf_n_estimators": 100,
        "rf_max_depth": 10,
        # Known metrics (2026-01-10)
        "known_metrics": {
            "relevance_accuracy": 0.859,
            "ak_accuracy": 0.632,
            "speed_items_per_sec": 675.2,
        },
    },
    "nomic-v2": {
        "status": "production",  # BEST accuracy (relevance + AK)
        "type": "sentence-transformers",
        "model": "nomic-ai/nomic-embed-text-v2-moe",
        "embedding_dim": 768,
        "max_length": 2000,  # Characters (~512 tokens)
        "task_prefix": "search_document: ",  # Verified best prefix
        # Classifier settings (tuned 2026-01-10)
        "lr_c": 0.5,
        "lr_max_iter": 1000,
        "rf_n_estimators": 300,  # Tuned up from 150
        "rf_max_depth": 30,  # Tuned up from 15
        # Known metrics (2026-01-10)
        "known_metrics": {
            "relevance_accuracy": 0.899,
            "ak_accuracy": 0.711,
            "speed_items_per_sec": 32.9,
        },
    },
    "bge-m3": {
        "status": "tested",  # Good relevance, longer context
        "type": "sentence-transformers",
        "model": "BAAI/bge-m3",
        "embedding_dim": 1024,
        "max_length": 8000,  # Characters (8192 tokens)
        # Classifier settings (tuned for 1024-dim embeddings)
        "lr_c": 1.0,
        "lr_max_iter": 1000,
        "rf_n_estimators": 200,
        "rf_max_depth": 20,  # Deeper for larger embeddings
        # Known metrics (2026-01-10)
        "known_metrics": {
            "relevance_accuracy": 0.886,
            "ak_accuracy": 0.553,
            "speed_items_per_sec": 25.9,
        },
    },
    "jina-v3": {
        "status": "tested",  # Good accuracy, fast, long context
        "type": "sentence-transformers",
        "model": "jinaai/jina-embeddings-v3",
        "embedding_dim": 1024,
        "max_length": 8000,  # Characters (8192 tokens)
        # Classifier settings
        "lr_c": 1.0,
        "lr_max_iter": 1000,
        "rf_n_estimators": 200,
        "rf_max_depth": 20,
        # Known metrics (2026-01-10)
        "known_metrics": {
            "relevance_accuracy": 0.879,
            "ak_accuracy": 0.579,
            "speed_items_per_sec": 55.2,
        },
    },
    "ollama": {
        "status": "tested",  # Local-only option, lower accuracy
        "type": "ollama",
        "model": "nomic-embed-text:137m-v1.5-fp16",
        "embedding_dim": 768,
        "max_length": 10000,  # Characters (8192 tokens)
        "num_ctx": 8192,
        # Classifier settings
        "lr_c": 1.0,
        "lr_max_iter": 1000,
        "rf_n_estimators": 200,
        "rf_max_depth": 15,
        # Known metrics (2026-01-10)
        "known_metrics": {
            "relevance_accuracy": 0.718,
            "ak_accuracy": 0.368,
            "speed_items_per_sec": 37.1,
        },
    },
}

# Default backend (production ready - best accuracy)
DEFAULT_BACKEND = "nomic-v2"


def get_backend_config(backend_name: str) -> dict:
    """Get configuration for a specific backend."""
    if backend_name not in BACKEND_CONFIGS:
        raise ValueError(
            f"Unknown backend: {backend_name}. "
            f"Available: {list(BACKEND_CONFIGS.keys())}"
        )
    return BACKEND_CONFIGS[backend_name].copy()
