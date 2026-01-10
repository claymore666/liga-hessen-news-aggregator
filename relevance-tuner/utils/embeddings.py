#!/usr/bin/env python3
"""
Configurable text embeddings supporting multiple backends.

Backends:
- Ollama: nomic-embed-text (8192 token context, 768 dims)
- Sentence-Transformers: paraphrase-multilingual-MiniLM-L12-v2 (128 tokens, 384 dims)

Usage:
    # Ollama backend (default)
    embedder = get_embedder("ollama")

    # Sentence-transformers backend
    embedder = get_embedder("sentence-transformers")

    # Or via environment variable
    EMBEDDING_BACKEND=sentence-transformers python train_embedding_classifier.py
"""

import os
import sys
from abc import ABC, abstractmethod
from typing import Optional

import requests

# Add parent directory to path for config import
sys.path.insert(0, str(__file__).rsplit("/", 2)[0])

from config import (
    EMBEDDING_CHUNK_OVERLAP,
    EMBEDDING_CHUNK_SIZE,
    EMBEDDING_DIMS,
    EMBEDDING_MODEL,
    EMBEDDING_NUM_CTX,
    OLLAMA_BASE_URL,
)

# Environment variable to select backend
EMBEDDING_BACKEND = os.environ.get("EMBEDDING_BACKEND", "ollama")

# Sentence-transformers model (for that backend)
ST_MODEL = os.environ.get("ST_EMBEDDING_MODEL", "paraphrase-multilingual-MiniLM-L12-v2")

# Nomic v2 MoE model (multilingual, 100 languages)
NOMIC_V2_MODEL = "nomic-ai/nomic-embed-text-v2-moe"


class BaseEmbedder(ABC):
    """Abstract base class for embedders."""

    @property
    @abstractmethod
    def embedding_dim(self) -> int:
        pass

    @abstractmethod
    def encode(
        self, texts: list[str], show_progress_bar: bool = True
    ) -> list[list[float]]:
        pass


class OllamaEmbedder(BaseEmbedder):
    """
    Ollama-based embedder with automatic chunking for long texts.
    """

    def __init__(
        self,
        model: str = EMBEDDING_MODEL,
        num_ctx: int = EMBEDDING_NUM_CTX,
        chunk_size: int = EMBEDDING_CHUNK_SIZE,
        chunk_overlap: int = EMBEDDING_CHUNK_OVERLAP,
        base_url: str = OLLAMA_BASE_URL,
    ):
        self.model = model
        self.num_ctx = num_ctx
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.base_url = base_url
        self._embedding_dim = EMBEDDING_DIMS

    @property
    def embedding_dim(self) -> int:
        return self._embedding_dim

    def encode(
        self,
        texts: list[str],
        show_progress_bar: bool = True,
        batch_size: int = 1,
    ) -> list[list[float]]:
        embeddings = []

        if show_progress_bar:
            try:
                from tqdm import tqdm
                texts = tqdm(texts, desc="Embedding (Ollama)")
            except ImportError:
                pass

        for text in texts:
            if len(text) <= self.chunk_size:
                emb = self._get_embedding(text)
            else:
                emb = self._get_chunked_embedding(text)
            embeddings.append(emb)

        return embeddings

    def _get_embedding(self, text: str, max_retries: int = 3) -> list[float]:
        """Get embedding for a single text with retry logic."""
        if len(text) > self.chunk_size * 2:
            text = text[: self.chunk_size * 2]

        for attempt in range(max_retries):
            try:
                response = requests.post(
                    f"{self.base_url}/api/embeddings",
                    json={
                        "model": self.model,
                        "prompt": text,
                        "options": {"num_ctx": self.num_ctx},
                    },
                    timeout=60,
                )

                if response.status_code == 500:
                    text = text[: len(text) // 2]
                    continue

                response.raise_for_status()
                result = response.json()

                embedding = result.get("embedding", [])
                if not embedding:
                    if attempt < max_retries - 1:
                        text = text[: len(text) // 2]
                        continue
                    return [0.0] * self._embedding_dim

                return embedding

            except requests.RequestException as e:
                if attempt < max_retries - 1:
                    text = text[: len(text) // 2]
                    continue
                print(f"Error getting embedding after {max_retries} retries: {e}")
                return [0.0] * self._embedding_dim

        return [0.0] * self._embedding_dim

    def _get_chunked_embedding(self, text: str) -> list[float]:
        """Get embedding for long text via chunking."""
        chunks = []
        start = 0

        while start < len(text):
            end = min(start + self.chunk_size, len(text))
            chunks.append(text[start:end])
            if end >= len(text):
                break
            start += self.chunk_size - self.chunk_overlap

        embeddings = []
        for chunk in chunks:
            emb = self._get_embedding(chunk)
            if emb and any(v != 0.0 for v in emb):
                embeddings.append(emb)

        if not embeddings:
            return [0.0] * self._embedding_dim

        return self._average_embeddings(embeddings)

    @staticmethod
    def _average_embeddings(embeddings: list[list[float]]) -> list[float]:
        if not embeddings:
            return []
        n_dims = len(embeddings[0])
        n_embs = len(embeddings)
        return [sum(emb[i] for emb in embeddings) / n_embs for i in range(n_dims)]

    def __repr__(self) -> str:
        return f"OllamaEmbedder(model='{self.model}', num_ctx={self.num_ctx})"


class SentenceTransformerEmbedder(BaseEmbedder):
    """
    Sentence-transformers based embedder.
    Uses paraphrase-multilingual-MiniLM-L12-v2 by default (German support, 384 dims).
    """

    def __init__(self, model_name: str = ST_MODEL, max_length: int = 1500):
        self.model_name = model_name
        self.max_length = max_length  # Truncate to this many chars
        self._model = None
        self._embedding_dim = None

    def _load_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.model_name)
            self._embedding_dim = self._model.get_sentence_embedding_dimension()
        return self._model

    @property
    def embedding_dim(self) -> int:
        if self._embedding_dim is None:
            self._load_model()
        return self._embedding_dim

    def encode(
        self,
        texts: list[str],
        show_progress_bar: bool = True,
        batch_size: int = 32,
    ) -> list[list[float]]:
        model = self._load_model()

        # Truncate texts to max_length
        truncated = [t[: self.max_length] for t in texts]

        embeddings = model.encode(
            truncated,
            show_progress_bar=show_progress_bar,
            convert_to_numpy=True,
            normalize_embeddings=True,
            batch_size=batch_size,
        )

        return embeddings.tolist()

    def __repr__(self) -> str:
        return f"SentenceTransformerEmbedder(model='{self.model_name}')"


class NomicV2Embedder(BaseEmbedder):
    """
    Nomic Embed Text v2 MoE embedder.

    Features:
    - 768 dimensions (or 256 with Matryoshka)
    - ~100 languages (multilingual)
    - 512 token context
    - MoE architecture (305M active params)

    Requires task prefixes:
    - Documents: "search_document: "
    - Queries: "search_query: "

    For classification, we treat training texts as documents.
    """

    def __init__(self, max_length: int = 2000, truncate_dim: Optional[int] = None):
        self.model_name = NOMIC_V2_MODEL
        self.max_length = max_length
        self.truncate_dim = truncate_dim  # Set to 256 for smaller embeddings
        self._model = None
        self._embedding_dim = truncate_dim or 768

    def _load_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            print(f"  Loading {self.model_name}...")
            self._model = SentenceTransformer(
                self.model_name,
                trust_remote_code=True,
                truncate_dim=self.truncate_dim,
            )
        return self._model

    @property
    def embedding_dim(self) -> int:
        return self._embedding_dim

    def encode(
        self,
        texts: list[str],
        show_progress_bar: bool = True,
        batch_size: int = 16,  # Smaller batch for larger model
        task_prefix: str = "search_document: ",
    ) -> list[list[float]]:
        model = self._load_model()

        # Truncate and add task prefix
        prefixed = [f"{task_prefix}{t[:self.max_length]}" for t in texts]

        embeddings = model.encode(
            prefixed,
            show_progress_bar=show_progress_bar,
            convert_to_numpy=True,
            normalize_embeddings=True,
            batch_size=batch_size,
        )

        return embeddings.tolist()

    def __repr__(self) -> str:
        dim_str = f", truncate_dim={self.truncate_dim}" if self.truncate_dim else ""
        return f"NomicV2Embedder({dim_str})"


def get_embedder(backend: Optional[str] = None) -> BaseEmbedder:
    """
    Get an embedder based on the specified backend.

    Args:
        backend: "ollama", "sentence-transformers", or "nomic-v2".
                 If None, uses EMBEDDING_BACKEND env var (default: ollama)

    Returns:
        BaseEmbedder instance
    """
    backend = backend or EMBEDDING_BACKEND

    if backend == "ollama":
        return OllamaEmbedder()
    elif backend in ("sentence-transformers", "st", "sbert"):
        return SentenceTransformerEmbedder()
    elif backend in ("nomic-v2", "nomic-moe", "nomic2"):
        return NomicV2Embedder()
    else:
        raise ValueError(f"Unknown embedding backend: {backend}")


# Convenience alias
def get_embeddings(texts: list[str], backend: Optional[str] = None) -> list[list[float]]:
    """Quick embedding function."""
    embedder = get_embedder(backend)
    return embedder.encode(texts)


if __name__ == "__main__":
    print("Testing embedders...")
    print(f"Default backend: {EMBEDDING_BACKEND}")

    # Test Ollama
    print("\n--- Ollama Embedder ---")
    ollama = OllamaEmbedder()
    print(f"Model: {ollama.model}")
    emb = ollama.encode(["Test sentence"], show_progress_bar=False)[0]
    print(f"Embedding dim: {len(emb)}")

    # Test sentence-transformers (if available)
    try:
        print("\n--- Sentence-Transformers Embedder ---")
        st = SentenceTransformerEmbedder()
        print(f"Model: {st.model_name}")
        emb = st.encode(["Test sentence"], show_progress_bar=False)[0]
        print(f"Embedding dim: {len(emb)}")
    except ImportError:
        print("Sentence-transformers not installed")

    print("\nDone!")
