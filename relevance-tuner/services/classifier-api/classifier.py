"""
Embedding Classifier for production deployment.
Wraps NomicV2Embedder + sklearn RandomForest classifiers.
"""

import pickle
from pathlib import Path
from typing import Optional

import numpy as np
import torch


class NomicV2Embedder:
    """
    Nomic Embed Text v2 MoE embedder.
    768 dimensions, ~512 tokens, multilingual.
    """

    def __init__(self, max_length: int = 2000):
        self.model_name = "nomic-ai/nomic-embed-text-v2-moe"
        self.max_length = max_length
        self.task_prefix = "search_document: "
        self._model = None
        self._embedding_dim = 768

    def _load_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            print(f"Loading {self.model_name}...")
            self._model = SentenceTransformer(
                self.model_name,
                trust_remote_code=True,
            )
            # Check if GPU is available
            if torch.cuda.is_available():
                print(f"Using GPU: {torch.cuda.get_device_name(0)}")
            else:
                print("WARNING: No GPU detected, running on CPU")
        return self._model

    @property
    def embedding_dim(self) -> int:
        return self._embedding_dim

    def encode(
        self,
        texts: list[str],
        show_progress_bar: bool = False,
        batch_size: int = 16,
    ) -> list[list[float]]:
        model = self._load_model()

        # Truncate and add task prefix
        prefixed = [f"{self.task_prefix}{t[:self.max_length]}" for t in texts]

        embeddings = model.encode(
            prefixed,
            show_progress_bar=show_progress_bar,
            convert_to_numpy=True,
            normalize_embeddings=True,
            batch_size=batch_size,
        )

        return embeddings.tolist()


class EmbeddingClassifier:
    """
    Production embedding classifier.
    Combines NomicV2 embeddings with sklearn RandomForest.
    """

    # Label mappings (integer class index to string label)
    PRIORITY_LABELS = ["critical", "high", "medium", "low"]
    AK_LABELS = ["AK1", "AK2", "AK3", "AK4", "AK5", "QAG"]

    def __init__(self):
        self.embedder = NomicV2Embedder()
        self.relevance_clf = None
        self.priority_clf = None
        self.ak_clf = None
        self.backend = "nomic-v2"

    @classmethod
    def load(cls, model_path: str = "models/embedding_classifier_nomic-v2.pkl"):
        """Load trained classifier from pickle file."""
        instance = cls()

        path = Path(model_path)
        if not path.exists():
            raise FileNotFoundError(f"Model not found: {model_path}")

        with open(path, "rb") as f:
            data = pickle.load(f)

        instance.relevance_clf = data["relevance_clf"]
        instance.priority_clf = data["priority_clf"]
        instance.ak_clf = data["ak_clf"]
        instance.backend = data.get("backend", "nomic-v2")

        print(f"Loaded classifier: {instance.backend}")
        return instance

    def predict(
        self,
        title: str,
        content: str,
        source: str = "",
    ) -> dict:
        """
        Predict relevance, priority, and AK for a single item.

        Returns:
            dict with keys: relevant, relevance_confidence, priority,
                           priority_confidence, ak, ak_confidence
        """
        # Combine text fields
        text = f"{title} {content}"

        # Get embedding
        embedding = np.array(self.embedder.encode([text], show_progress_bar=False))

        # Predict relevance
        relevance_proba = self.relevance_clf.predict_proba(embedding)[0]
        relevant_idx = list(self.relevance_clf.classes_).index(1)
        relevance_confidence = relevance_proba[relevant_idx]
        is_relevant = relevance_confidence > 0.5

        result = {
            "relevant": is_relevant,
            "relevance_confidence": float(relevance_confidence),
        }

        # Only predict priority/AK if relevant
        if is_relevant and self.priority_clf and self.ak_clf:
            # Priority
            priority_proba = self.priority_clf.predict_proba(embedding)[0]
            priority_idx = int(np.argmax(priority_proba))
            result["priority"] = self.PRIORITY_LABELS[priority_idx]
            result["priority_confidence"] = float(priority_proba[priority_idx])

            # AK
            ak_proba = self.ak_clf.predict_proba(embedding)[0]
            ak_idx = int(np.argmax(ak_proba))
            result["ak"] = self.AK_LABELS[ak_idx]
            result["ak_confidence"] = float(ak_proba[ak_idx])

        return result

    def is_gpu_available(self) -> bool:
        """Check if GPU is available."""
        return torch.cuda.is_available()

    def get_info(self) -> dict:
        """Get classifier info."""
        return {
            "backend": self.backend,
            "embedding_dim": self.embedder.embedding_dim,
            "gpu_available": self.is_gpu_available(),
            "gpu_name": torch.cuda.get_device_name(0) if self.is_gpu_available() else None,
        }
