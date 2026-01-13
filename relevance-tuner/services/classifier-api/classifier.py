"""
Embedding Classifier for production deployment.
Wraps NomicV2Embedder + sklearn RandomForest classifiers.
Includes VectorStore for semantic search and similarity.
"""

import pickle
from pathlib import Path
from typing import Optional

import chromadb
from chromadb.config import Settings
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
    Supports both single-label and multi-label AK predictions.
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
        self.multilabel = False  # Whether AK classifier is multi-label

    @classmethod
    def load(cls, model_path: str = "models/embedding_classifier_nomic-v2.pkl"):
        """Load trained classifier from pickle file."""
        instance = cls()

        path = Path(model_path)
        if not path.exists():
            raise FileNotFoundError(f"Model not found: {model_path}")

        with open(path, "rb") as f:
            data = pickle.load(f)

        # Handle dict format (both single-label and multi-label)
        if isinstance(data, dict):
            instance.relevance_clf = data["relevance_clf"]
            instance.priority_clf = data["priority_clf"]
            instance.ak_clf = data["ak_clf"]
            instance.backend = data.get("backend", "nomic-v2")
            instance.multilabel = data.get("multilabel", False)
            if instance.multilabel and "ak_classes" in data:
                instance.AK_LABELS = data["ak_classes"]
        else:
            # Legacy: class instance (shouldn't happen in production)
            instance.relevance_clf = data.relevance_clf
            instance.priority_clf = data.priority_clf
            instance.ak_clf = data.ak_clf
            instance.backend = getattr(data, "backend_name", "nomic-v2")
            instance.multilabel = hasattr(data, "ak_classes")
            if instance.multilabel:
                instance.AK_LABELS = data.ak_classes

        print(f"Loaded classifier: {instance.backend} (multilabel={instance.multilabel})")
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
                           priority_confidence, ak, ak_confidence,
                           aks (list), ak_confidences (dict) for multi-label
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
            "priority": None,
            "priority_confidence": None,
            "ak": None,
            "ak_confidence": None,
            "aks": [],
            "ak_confidences": {},
        }

        # Only predict priority/AK if relevant
        if is_relevant and self.priority_clf and self.ak_clf:
            # Priority
            priority_proba = self.priority_clf.predict_proba(embedding)[0]
            priority_idx = int(np.argmax(priority_proba))
            result["priority"] = self.PRIORITY_LABELS[priority_idx]
            result["priority_confidence"] = float(priority_proba[priority_idx])

            # AK prediction
            if self.multilabel:
                # Multi-label: predict multiple AKs
                ak_preds = self.ak_clf.predict(embedding)[0]
                ak_confidences = {}

                # Get probabilities for each AK
                predicted_aks = []
                for i, estimator in enumerate(self.ak_clf.estimators_):
                    prob = estimator.predict_proba(embedding)[0]
                    conf = prob[1] if len(prob) > 1 else prob[0]
                    ak_confidences[self.AK_LABELS[i]] = float(conf)
                    if ak_preds[i] == 1:
                        predicted_aks.append(self.AK_LABELS[i])

                # Fallback if no AK predicted
                if not predicted_aks:
                    best_idx = max(range(len(self.AK_LABELS)),
                                   key=lambda i: ak_confidences[self.AK_LABELS[i]])
                    predicted_aks = [self.AK_LABELS[best_idx]]

                result["aks"] = predicted_aks
                result["ak_confidences"] = ak_confidences
                # Primary AK for backward compatibility
                result["ak"] = predicted_aks[0] if predicted_aks else None
                result["ak_confidence"] = ak_confidences.get(result["ak"], 0.0)
            else:
                # Single-label: predict one AK
                ak_proba = self.ak_clf.predict_proba(embedding)[0]
                ak_idx = int(np.argmax(ak_proba))
                result["ak"] = self.AK_LABELS[ak_idx]
                result["ak_confidence"] = float(ak_proba[ak_idx])
                result["aks"] = [result["ak"]]
                result["ak_confidences"] = {result["ak"]: result["ak_confidence"]}

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


class VectorStore:
    """
    ChromaDB-based vector store for semantic search and similarity.
    Uses the same NomicV2 embeddings as the classifier.
    """

    def __init__(self, embedder: NomicV2Embedder, persist_dir: str = "/app/data/vectordb"):
        """
        Initialize vector store.

        Args:
            embedder: NomicV2Embedder instance (shared with classifier)
            persist_dir: Directory for persistent storage
        """
        self.embedder = embedder
        self.persist_dir = persist_dir

        # Initialize ChromaDB with persistent storage
        Path(persist_dir).mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(
            path=persist_dir,
            settings=Settings(anonymized_telemetry=False),
        )

        # Get or create collection
        self.collection = self.client.get_or_create_collection(
            name="news_items",
            metadata={"hnsw:space": "cosine"},
        )

        print(f"VectorStore initialized: {self.collection.count()} items in collection")

    def add_item(
        self,
        item_id: str,
        title: str,
        content: str,
        metadata: Optional[dict] = None,
    ) -> bool:
        """
        Add or update an item in the vector store.

        Args:
            item_id: Unique identifier (e.g., database ID)
            title: Item title
            content: Item content
            metadata: Optional metadata (source, priority, ak, etc.)

        Returns:
            True if added, False if already exists
        """
        # Check if already exists
        existing = self.collection.get(ids=[item_id])
        if existing["ids"]:
            return False  # Already indexed

        # Generate embedding
        text = f"{title} {content}"
        embedding = self.embedder.encode([text], show_progress_bar=False)[0]

        # Prepare metadata
        meta = metadata or {}
        meta["title"] = title[:500]  # Truncate for storage

        # Add to collection
        self.collection.add(
            ids=[item_id],
            embeddings=[embedding],
            metadatas=[meta],
            documents=[text[:2000]],  # Store truncated text
        )

        return True

    def add_items_batch(
        self,
        items: list[dict],
    ) -> int:
        """
        Add multiple items in batch.

        Args:
            items: List of dicts with keys: id, title, content, metadata (optional)

        Returns:
            Number of items added
        """
        # Filter out existing items
        ids = [str(item["id"]) for item in items]
        existing = set(self.collection.get(ids=ids)["ids"])
        new_items = [item for item in items if str(item["id"]) not in existing]

        if not new_items:
            return 0

        # Generate embeddings
        texts = [f"{item['title']} {item['content']}" for item in new_items]
        embeddings = self.embedder.encode(texts, show_progress_bar=len(texts) > 10)

        # Prepare data
        ids = [str(item["id"]) for item in new_items]
        metadatas = []
        documents = []
        for item in new_items:
            meta = item.get("metadata", {}) or {}
            meta["title"] = item["title"][:500]
            metadatas.append(meta)
            documents.append(texts[new_items.index(item)][:2000])

        # Add to collection
        self.collection.add(
            ids=ids,
            embeddings=embeddings,
            metadatas=metadatas,
            documents=documents,
        )

        return len(new_items)

    def search(
        self,
        query: str,
        n_results: int = 10,
        filter_metadata: Optional[dict] = None,
    ) -> list[dict]:
        """
        Semantic search for items matching a query.

        Args:
            query: Search query text
            n_results: Number of results to return
            filter_metadata: Optional filter (e.g., {"source": "hr"})

        Returns:
            List of results with id, title, score, metadata
        """
        # Generate query embedding
        embedding = self.embedder.encode([query], show_progress_bar=False)[0]

        # Search
        results = self.collection.query(
            query_embeddings=[embedding],
            n_results=n_results,
            where=filter_metadata,
            include=["metadatas", "documents", "distances"],
        )

        # Format results
        items = []
        for i in range(len(results["ids"][0])):
            items.append({
                "id": results["ids"][0][i],
                "title": results["metadatas"][0][i].get("title", ""),
                "score": 1 - results["distances"][0][i],  # Convert distance to similarity
                "metadata": results["metadatas"][0][i],
                "snippet": results["documents"][0][i][:300] if results["documents"] else "",
            })

        return items

    def find_similar(
        self,
        item_id: str,
        n_results: int = 5,
        exclude_same_source: bool = True,
    ) -> list[dict]:
        """
        Find items similar to a given item.

        Args:
            item_id: ID of the item to find similar items for
            n_results: Number of results to return
            exclude_same_source: Whether to exclude items from same source

        Returns:
            List of similar items with id, title, score, metadata
        """
        # Get the item's embedding
        item = self.collection.get(
            ids=[item_id],
            include=["embeddings", "metadatas"],
        )

        if not item["ids"]:
            return []

        embedding = item["embeddings"][0]
        item_source = item["metadatas"][0].get("source", "")

        # Search for similar (request extra to filter)
        results = self.collection.query(
            query_embeddings=[embedding],
            n_results=n_results + 10,  # Get extra for filtering
            include=["metadatas", "documents", "distances"],
        )

        # Format and filter results
        items = []
        for i in range(len(results["ids"][0])):
            result_id = results["ids"][0][i]

            # Skip the query item itself
            if result_id == item_id:
                continue

            # Optionally skip same source
            result_source = results["metadatas"][0][i].get("source", "")
            if exclude_same_source and result_source == item_source:
                continue

            items.append({
                "id": result_id,
                "title": results["metadatas"][0][i].get("title", ""),
                "score": 1 - results["distances"][0][i],
                "metadata": results["metadatas"][0][i],
                "snippet": results["documents"][0][i][:300] if results["documents"] else "",
            })

            if len(items) >= n_results:
                break

        return items

    def get_stats(self) -> dict:
        """Get vector store statistics."""
        return {
            "total_items": self.collection.count(),
            "persist_dir": self.persist_dir,
        }
