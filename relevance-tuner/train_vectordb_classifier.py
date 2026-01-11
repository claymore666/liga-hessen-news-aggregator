#!/usr/bin/env python3
"""
Liga Hessen Vector DB Classifier

k-NN classification using semantic similarity.
For each new item, find k most similar labeled items and vote.

Benefits:
- No training needed (just store embeddings)
- Works well with few examples per class
- Provides interpretable results (show similar items)
- Can be used for data augmentation

Usage:
    python train_vectordb_classifier.py

Production:
    from train_vectordb_classifier import VectorDBClassifier
    clf = VectorDBClassifier.load()
    result = clf.predict(title, content)
    # Also get similar items for context
    similar = clf.find_similar(title, content, k=5)
"""

import pickle
import time
from collections import Counter
from pathlib import Path
from typing import Optional

import numpy as np
from sklearn.metrics import accuracy_score, classification_report

# Import from central config and utilities
from config import AK_CLASSES, MODELS_DIR, PRIORITY_LEVELS
from utils import get_embedder, load_test_data, load_training_data

# ============================================================================
# Configuration
# ============================================================================

MODEL_DIR = MODELS_DIR / "vectordb"

# k-NN parameters
K_RELEVANCE = 7   # neighbors for relevance vote
K_PRIORITY = 5    # neighbors for priority vote
K_AK = 5          # neighbors for AK vote


# ============================================================================
# Vector Database
# ============================================================================

class VectorDBClassifier:
    """
    k-NN classifier using vector similarity.

    Stores all training items as embeddings, then classifies new items
    by finding the k most similar items and voting.
    """

    def __init__(self):
        self.embedder = None  # Lazy load

        # Storage
        self.embeddings: Optional[np.ndarray] = None
        self.texts: list[str] = []
        self.relevance: list[int] = []  # 0 or 1
        self.priorities: list[str] = []  # "low", "medium", etc.
        self.aks: list[str] = []  # "AK1", "AK2", etc.
        self.metadata: list[dict] = []  # title, source, etc.

        self.is_fitted = False

    def _load_embedder(self):
        """Lazy load embedding model."""
        if self.embedder is None:
            print("  Loading embedder...")
            self.embedder = get_embedder()  # Uses EMBEDDING_BACKEND env var
            print(f"  Backend: {self.embedder}")
        return self.embedder

    def _embed(self, texts: list[str], show_progress: bool = True) -> np.ndarray:
        """Embed texts."""
        embedder = self._load_embedder()
        embeddings = embedder.encode(texts, show_progress_bar=show_progress)
        # Normalize for cosine similarity
        embeddings = np.array(embeddings)
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms[norms == 0] = 1  # Avoid division by zero
        return embeddings / norms

    def _cosine_similarity(self, query: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
        """
        Find k most similar items to query.

        Returns:
            indices: indices of k nearest neighbors
            similarities: cosine similarities (0-1)
        """
        # query is already normalized, embeddings are normalized
        # cosine similarity = dot product for normalized vectors
        similarities = np.dot(self.embeddings, query)

        # Get top k indices
        top_k_indices = np.argsort(similarities)[-k:][::-1]
        top_k_similarities = similarities[top_k_indices]

        return top_k_indices, top_k_similarities

    def _weighted_vote(self, values: list, weights: np.ndarray, valid_classes: list) -> tuple[str, float]:
        """
        Weighted voting among neighbors.

        Returns:
            winner: most voted class
            confidence: proportion of weighted votes for winner
        """
        # Filter to valid values
        valid_mask = [v in valid_classes for v in values]
        if not any(valid_mask):
            return valid_classes[0], 0.0

        # Weight aggregation per class
        class_weights = Counter()
        total_weight = 0
        for val, weight, valid in zip(values, weights, valid_mask):
            if valid:
                class_weights[val] += weight
                total_weight += weight

        if total_weight == 0:
            return valid_classes[0], 0.0

        winner = class_weights.most_common(1)[0][0]
        confidence = class_weights[winner] / total_weight

        return winner, confidence

    def fit(self, texts: list[str], relevance: list[int], priorities: list[str],
            aks: list[str], metadata: Optional[list[dict]] = None):
        """
        Store training data in the vector database.
        """
        print("  Computing embeddings for database...")
        self.embeddings = self._embed(texts, show_progress=True)
        self.texts = texts
        self.relevance = relevance
        self.priorities = priorities
        self.aks = aks
        self.metadata = metadata or [{} for _ in texts]

        print(f"  Database size: {len(texts)} items, {self.embeddings.shape[1]} dimensions")
        self.is_fitted = True

    def predict(self, title: str, content: str, source: Optional[str] = None) -> dict:
        """
        Predict using k-NN voting.
        """
        # No truncation needed - embedder handles long texts
        text = f"{title} {content}"
        if source:
            text += f" Quelle: {source}"

        query = self._embed([text], show_progress=False)[0]

        # Stage 1: Relevance (k=7)
        indices, similarities = self._cosine_similarity(query, K_RELEVANCE)
        rel_values = [self.relevance[i] for i in indices]
        rel_winner, rel_conf = self._weighted_vote(rel_values, similarities, [0, 1])
        is_relevant = rel_winner == 1

        result = {
            "relevant": is_relevant,
            "relevance_confidence": rel_conf,
            "priority": None,
            "priority_confidence": None,
            "ak": None,
            "ak_confidence": None,
            "similar_items": [],  # For interpretability
        }

        # Add similar items info
        for idx, sim in zip(indices[:3], similarities[:3]):
            result["similar_items"].append({
                "text": self.texts[idx][:100] + "...",
                "similarity": float(sim),
                "relevant": bool(self.relevance[idx]),
                "priority": self.priorities[idx],
                "ak": self.aks[idx],
            })

        if is_relevant:
            # Stage 2: Priority (k=5, from relevant neighbors only)
            # Re-search with more neighbors to get enough relevant ones
            indices, similarities = self._cosine_similarity(query, K_PRIORITY * 3)

            # Filter to relevant items
            rel_mask = [self.relevance[i] == 1 for i in indices]
            rel_indices = [i for i, m in zip(indices, rel_mask) if m][:K_PRIORITY]
            rel_sims = similarities[rel_mask][:K_PRIORITY]

            if len(rel_indices) > 0:
                pri_values = [self.priorities[i] for i in rel_indices]
                winner, conf = self._weighted_vote(pri_values, rel_sims, PRIORITY_LEVELS)
                result["priority"] = winner
                result["priority_confidence"] = conf

                # Stage 3: AK
                ak_values = [self.aks[i] for i in rel_indices]
                winner, conf = self._weighted_vote(ak_values, rel_sims, AK_CLASSES)
                result["ak"] = winner
                result["ak_confidence"] = conf
            else:
                result["priority"] = "medium"
                result["priority_confidence"] = 0.5
                result["ak"] = "QAG"
                result["ak_confidence"] = 0.5

        return result

    def predict_batch(self, texts: list[str]) -> list[dict]:
        """Predict for multiple texts."""
        results = []
        for text in texts:
            # Parse text back to title/content (approximate)
            parts = text.split(" ", 1)
            title = parts[0] if parts else ""
            content = parts[1] if len(parts) > 1 else ""
            results.append(self.predict(title, content))
        return results

    def find_similar(self, title: str, content: str, k: int = 5,
                     filter_relevant: Optional[bool] = None) -> list[dict]:
        """
        Find k most similar items in the database.

        Useful for:
        - Data augmentation (find similar items to label)
        - LLM context (find similar Liga reactions)
        - Debugging (why was this classified this way?)

        Args:
            filter_relevant: If True, only return relevant items. If False, only irrelevant.
        """
        # No truncation needed - embedder handles long texts
        text = f"{title} {content}"
        query = self._embed([text], show_progress=False)[0]

        # Get more neighbors if filtering
        search_k = k * 3 if filter_relevant is not None else k
        indices, similarities = self._cosine_similarity(query, search_k)

        results = []
        for idx, sim in zip(indices, similarities):
            if filter_relevant is not None:
                is_rel = self.relevance[idx] == 1
                if filter_relevant != is_rel:
                    continue

            results.append({
                "index": int(idx),
                "text": self.texts[idx],
                "similarity": float(sim),
                "relevant": bool(self.relevance[idx]),
                "priority": self.priorities[idx],
                "ak": self.aks[idx],
                "metadata": self.metadata[idx],
            })

            if len(results) >= k:
                break

        return results

    def find_misclassified_candidates(self, threshold: float = 0.5) -> list[dict]:
        """
        Find items where neighbors disagree (potential mislabels).
        """
        candidates = []

        for i, text in enumerate(self.texts):
            query = self.embeddings[i]
            # Get neighbors (excluding self)
            indices, similarities = self._cosine_similarity(query, K_RELEVANCE + 1)
            indices = indices[1:]  # Remove self
            similarities = similarities[1:]

            # Check agreement
            rel_values = [self.relevance[j] for j in indices]
            majority_rel = 1 if sum(rel_values) > len(rel_values) / 2 else 0

            if self.relevance[i] != majority_rel:
                agreement = sum(1 for v in rel_values if v == majority_rel) / len(rel_values)
                if agreement >= threshold:
                    candidates.append({
                        "index": i,
                        "text": text[:100] + "...",
                        "current_label": self.relevance[i],
                        "suggested_label": majority_rel,
                        "neighbor_agreement": agreement,
                    })

        return candidates

    def save(self, path: Optional[Path] = None):
        """Save database."""
        path = Path(path) if path else MODEL_DIR
        path.mkdir(parents=True, exist_ok=True)

        # Don't save embedder
        embedder = self.embedder
        self.embedder = None

        with open(path / "vectordb_classifier.pkl", "wb") as f:
            pickle.dump(self, f)

        self.embedder = embedder
        print(f"  Saved to: {path / 'vectordb_classifier.pkl'}")

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "VectorDBClassifier":
        """Load database."""
        path = Path(path) if path else MODEL_DIR
        with open(path / "vectordb_classifier.pkl", "rb") as f:
            return pickle.load(f)


# ============================================================================
# Evaluation
# ============================================================================

def evaluate(clf: VectorDBClassifier, texts: list[str], relevance: list[int],
             priorities: list[str], aks: list[str]) -> dict:
    """Evaluate k-NN classifier."""

    print("  Predicting...")
    predictions = []
    for i, text in enumerate(texts):
        # Simple prediction without parsing
        query = clf._embed([text], show_progress=False)[0]

        # Relevance
        indices, similarities = clf._cosine_similarity(query, K_RELEVANCE)
        rel_values = [clf.relevance[j] for j in indices]
        rel_winner, rel_conf = clf._weighted_vote(rel_values, similarities, [0, 1])
        is_relevant = rel_winner == 1

        pred = {
            "relevant": is_relevant,
            "relevance_confidence": rel_conf,
            "priority": None,
            "ak": None,
        }

        if is_relevant:
            indices, similarities = clf._cosine_similarity(query, K_PRIORITY * 3)
            rel_mask = [clf.relevance[j] == 1 for j in indices]
            rel_indices = [j for j, m in zip(indices, rel_mask) if m][:K_PRIORITY]
            rel_sims = similarities[rel_mask][:K_PRIORITY]

            if len(rel_indices) > 0:
                pri_values = [clf.priorities[j] for j in rel_indices]
                pred["priority"], _ = clf._weighted_vote(pri_values, rel_sims, PRIORITY_LEVELS)

                ak_values = [clf.aks[j] for j in rel_indices]
                pred["ak"], _ = clf._weighted_vote(ak_values, rel_sims, AK_CLASSES)

        predictions.append(pred)

    # Relevance
    y_true_rel = np.array(relevance)
    y_pred_rel = np.array([1 if p["relevant"] else 0 for p in predictions])

    rel_acc = accuracy_score(y_true_rel, y_pred_rel)
    print("\n=== RELEVANCE (binary) ===")
    print(classification_report(y_true_rel, y_pred_rel, target_names=["irrelevant", "relevant"]))
    print(f"Accuracy: {rel_acc:.1%}")

    # Priority
    relevant_mask = y_true_rel == 1
    priorities_arr = np.array(priorities)
    valid_priority = np.array([p in PRIORITY_LEVELS for p in priorities_arr])
    eval_mask = relevant_mask & valid_priority

    if np.sum(eval_mask) > 0:
        y_true_pri = priorities_arr[eval_mask]
        y_pred_pri = np.array([p["priority"] or "medium" for p in predictions])[eval_mask]

        pri_acc = accuracy_score(y_true_pri, y_pred_pri)
        print("\n=== PRIORITY (4-class) ===")
        print(classification_report(y_true_pri, y_pred_pri, labels=PRIORITY_LEVELS, zero_division=0))
        print(f"Accuracy: {pri_acc:.1%}")

        level_map = {p: i for i, p in enumerate(PRIORITY_LEVELS)}
        within_one = np.mean([abs(level_map[t] - level_map.get(p, 1)) <= 1
                             for t, p in zip(y_true_pri, y_pred_pri)])
        print(f"Within-1-level: {within_one:.1%}")
    else:
        pri_acc = 0
        within_one = 0

    # AK
    aks_arr = np.array(aks)
    valid_ak = np.array([a in AK_CLASSES for a in aks_arr])
    eval_mask = relevant_mask & valid_ak

    if np.sum(eval_mask) > 0:
        y_true_ak = aks_arr[eval_mask]
        y_pred_ak = np.array([p["ak"] or "QAG" for p in predictions])[eval_mask]

        ak_acc = accuracy_score(y_true_ak, y_pred_ak)
        print("\n=== AK (6-class) ===")
        print(classification_report(y_true_ak, y_pred_ak, labels=AK_CLASSES, zero_division=0))
        print(f"Accuracy: {ak_acc:.1%}")
    else:
        ak_acc = 0

    return {
        "relevance_accuracy": rel_acc,
        "priority_accuracy": pri_acc,
        "priority_within_one": within_one,
        "ak_accuracy": ak_acc,
    }


# ============================================================================
# Main
# ============================================================================

def main():
    print("=" * 60)
    print("Liga Vector DB Classifier - k-NN with Embeddings")
    print("=" * 60)

    # Load data using shared utilities
    print("\n[1/5] Loading data...")
    train_texts, train_rel, train_pri, train_ak = load_training_data(
        splits=["train", "validation"]
    )
    test_texts, test_rel, test_pri, test_ak = load_test_data()

    print(f"  Database: {len(train_texts)} items")
    print(f"  Test: {len(test_texts)} items")

    # Build database
    print("\n[2/5] Building vector database...")
    clf = VectorDBClassifier()
    clf.fit(train_texts, train_rel, train_pri, train_ak)

    # Evaluate
    print("\n[3/5] Evaluating on test set...")
    metrics = evaluate(clf, test_texts, test_rel, test_pri, test_ak)

    # Speed benchmark
    print("\n[4/5] Speed benchmark...")
    start = time.perf_counter()
    for text in test_texts[:50]:
        query = clf._embed([text], show_progress=False)[0]
        clf._cosine_similarity(query, 7)
    elapsed = time.perf_counter() - start
    speed = 50 / elapsed
    print(f"  Speed: {speed:.1f} items/sec ({1000/speed:.1f}ms per item)")

    # Find potential mislabels
    print("\n[5/5] Finding potential mislabels...")
    candidates = clf.find_misclassified_candidates(threshold=0.7)
    print(f"  Found {len(candidates)} items where neighbors strongly disagree")
    if candidates[:3]:
        print("  Top candidates:")
        for c in candidates[:3]:
            print(f"    - {c['text']}")
            print(f"      current={c['current_label']}, suggested={c['suggested_label']}, "
                  f"agreement={c['neighbor_agreement']:.0%}")

    # Save
    print("\n=== Saving Model ===")
    clf.save()

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Relevance accuracy:     {metrics['relevance_accuracy']:.1%}")
    print(f"Priority accuracy:      {metrics['priority_accuracy']:.1%}")
    print(f"Priority within-1:      {metrics['priority_within_one']:.1%}")
    print(f"AK accuracy:            {metrics['ak_accuracy']:.1%}")
    print(f"Speed:                  {speed:.1f} items/sec")

    # Comparison
    print("\n=== COMPARISON: All Approaches ===")
    print("                    TF-IDF   Embeddings   VectorDB")
    print(f"  Relevance:         83.9%      85.2%      {metrics['relevance_accuracy']:.1%}")
    print(f"  Priority:          63.2%      63.2%      {metrics['priority_accuracy']:.1%}")
    print(f"  AK:                39.5%      55.3%      {metrics['ak_accuracy']:.1%}")

    # Demo: Similar items
    print("\n=== Demo: Find Similar Items ===")
    demo_title = "Pflegenotstand in Hessen verschärft sich"
    demo_content = "Immer mehr Pflegeheime melden Personalmangel. Die Situation in der Altenpflege wird kritisch."

    similar = clf.find_similar(demo_title, demo_content, k=3, filter_relevant=True)
    print(f"\n  Query: {demo_title}")
    print(f"  Similar relevant items:")
    for item in similar:
        print(f"    [{item['similarity']:.0%}] {item['ak']}/{item['priority']}: {item['metadata'].get('title', 'N/A')[:50]}...")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
