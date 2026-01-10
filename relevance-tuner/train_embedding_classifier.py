#!/usr/bin/env python3
"""
Liga Hessen Embedding-based Classifier

Uses sentence-transformers embeddings instead of TF-IDF for better semantic understanding.

Benefits:
- "Kita" and "Kindergarten" are similar (semantic)
- "Pflege" and "Altenpflege" are similar
- Works better with limited training data

Model: paraphrase-multilingual-MiniLM-L12-v2 (German support, 384 dims)

Usage:
    python train_embedding_classifier.py

Production:
    from train_embedding_classifier import EmbeddingClassifier
    clf = EmbeddingClassifier.load()
    result = clf.predict(title, content)
"""

import json
import os
import pickle
import time
from collections import Counter
from pathlib import Path
from typing import Optional

import numpy as np

# Content length for text truncation (env var or default 6000)
CONTENT_MAX_LENGTH = int(os.environ.get("CONTENT_MAX_LENGTH", 6000))
from sentence_transformers import SentenceTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, f1_score
from sklearn.preprocessing import LabelEncoder

# ============================================================================
# Configuration
# ============================================================================

DATA_DIR = Path(__file__).parent / "data" / "final"
MODEL_DIR = Path(__file__).parent / "models" / "embedding"

# Multilingual model with German support (384 dimensions)
EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"

PRIORITY_LEVELS = ["low", "medium", "high", "critical"]
AK_CLASSES = ["AK1", "AK2", "AK3", "AK4", "AK5", "QAG"]


# ============================================================================
# Embedding Classifier
# ============================================================================

class EmbeddingClassifier:
    """
    Hierarchical classifier using sentence-transformer embeddings.

    Stage 1: Relevance (binary)
    Stage 2: Priority (4-class, only for relevant)
    Stage 3: AK (6-class, only for relevant)
    """

    def __init__(self, model_name: str = EMBEDDING_MODEL):
        self.model_name = model_name
        self.embedder = None  # Lazy load

        # Stage 1: Relevance
        self.relevance_clf = LogisticRegression(
            max_iter=1000,
            class_weight="balanced",
            C=1.0,
            random_state=42,
        )

        # Stage 2: Priority (RandomForest works well with embeddings)
        self.priority_clf = RandomForestClassifier(
            n_estimators=200,
            max_depth=15,
            class_weight="balanced",
            random_state=42,
            n_jobs=-1,
        )
        self.priority_encoder = LabelEncoder()

        # Stage 3: AK
        self.ak_clf = RandomForestClassifier(
            n_estimators=200,
            max_depth=15,
            class_weight="balanced",
            random_state=42,
            n_jobs=-1,
        )
        self.ak_encoder = LabelEncoder()

        self.is_fitted = False

    def _load_embedder(self):
        """Lazy load the embedding model."""
        if self.embedder is None:
            print(f"  Loading embedding model: {self.model_name}")
            self.embedder = SentenceTransformer(self.model_name)
        return self.embedder

    def _embed(self, texts: list[str], show_progress: bool = True) -> np.ndarray:
        """Embed texts using sentence-transformers."""
        embedder = self._load_embedder()
        return embedder.encode(
            texts,
            show_progress_bar=show_progress,
            convert_to_numpy=True,
            normalize_embeddings=True,  # For cosine similarity
        )

    def fit(
        self,
        texts: list[str],
        relevance: list[int],
        priorities: list[str],
        aks: list[str],
    ):
        """Train all classifiers."""
        print("  Computing embeddings...")
        X = self._embed(texts, show_progress=True)
        print(f"  Embedding matrix: {X.shape}")

        # Stage 1: Relevance
        print("  Training relevance classifier...")
        y_rel = np.array(relevance)
        self.relevance_clf.fit(X, y_rel)

        # Stage 2: Priority (only on relevant)
        print("  Training priority classifier...")
        relevant_mask = y_rel == 1
        priorities_arr = np.array(priorities)
        valid_priority = np.array([p in PRIORITY_LEVELS for p in priorities_arr])
        valid_mask = relevant_mask & valid_priority

        if np.sum(valid_mask) > 10:
            X_priority = X[valid_mask]
            y_priority = priorities_arr[valid_mask]
            self.priority_encoder.fit(PRIORITY_LEVELS)
            y_priority_enc = self.priority_encoder.transform(y_priority)
            self.priority_clf.fit(X_priority, y_priority_enc)

        # Stage 3: AK (only on relevant)
        print("  Training AK classifier...")
        aks_arr = np.array(aks)
        valid_ak = np.array([a in AK_CLASSES for a in aks_arr])
        valid_mask = relevant_mask & valid_ak

        if np.sum(valid_mask) > 10:
            X_ak = X[valid_mask]
            y_ak = aks_arr[valid_mask]
            self.ak_encoder.fit(AK_CLASSES)
            y_ak_enc = self.ak_encoder.transform(y_ak)
            self.ak_clf.fit(X_ak, y_ak_enc)

        self.is_fitted = True
        print("  Training complete!")

    def predict(self, title: str, content: str, source: Optional[str] = None) -> dict:
        """Predict for a single item."""
        text = f"{title} {content[:CONTENT_MAX_LENGTH]}"
        if source:
            text += f" Quelle: {source}"

        X = self._embed([text], show_progress=False)

        # Stage 1: Relevance
        relevance_prob = self.relevance_clf.predict_proba(X)[0]
        is_relevant = self.relevance_clf.predict(X)[0]

        result = {
            "relevant": bool(is_relevant),
            "relevance_confidence": float(max(relevance_prob)),
            "priority": None,
            "priority_confidence": None,
            "ak": None,
            "ak_confidence": None,
        }

        if is_relevant:
            # Stage 2: Priority
            try:
                priority_prob = self.priority_clf.predict_proba(X)[0]
                priority_idx = np.argmax(priority_prob)
                result["priority"] = self.priority_encoder.inverse_transform([priority_idx])[0]
                result["priority_confidence"] = float(priority_prob[priority_idx])
            except Exception:
                result["priority"] = "medium"
                result["priority_confidence"] = 0.5

            # Stage 3: AK
            try:
                ak_prob = self.ak_clf.predict_proba(X)[0]
                ak_idx = np.argmax(ak_prob)
                result["ak"] = self.ak_encoder.inverse_transform([ak_idx])[0]
                result["ak_confidence"] = float(ak_prob[ak_idx])
            except Exception:
                result["ak"] = "QAG"
                result["ak_confidence"] = 0.5

        return result

    def predict_batch(self, texts: list[str]) -> list[dict]:
        """Predict for multiple texts."""
        X = self._embed(texts, show_progress=False)

        # Stage 1: Relevance
        relevance_preds = self.relevance_clf.predict(X)
        relevance_probs = self.relevance_clf.predict_proba(X)

        results = []
        for i in range(len(texts)):
            is_relevant = bool(relevance_preds[i])
            result = {
                "relevant": is_relevant,
                "relevance_confidence": float(max(relevance_probs[i])),
                "priority": None,
                "priority_confidence": None,
                "ak": None,
                "ak_confidence": None,
            }
            results.append(result)

        # Stage 2 & 3: Only for relevant items
        relevant_indices = [i for i, r in enumerate(results) if r["relevant"]]
        if relevant_indices:
            X_relevant = X[relevant_indices]

            try:
                priority_probs = self.priority_clf.predict_proba(X_relevant)
                ak_probs = self.ak_clf.predict_proba(X_relevant)

                for j, i in enumerate(relevant_indices):
                    priority_idx = np.argmax(priority_probs[j])
                    results[i]["priority"] = self.priority_encoder.inverse_transform([priority_idx])[0]
                    results[i]["priority_confidence"] = float(priority_probs[j, priority_idx])

                    ak_idx = np.argmax(ak_probs[j])
                    results[i]["ak"] = self.ak_encoder.inverse_transform([ak_idx])[0]
                    results[i]["ak_confidence"] = float(ak_probs[j, ak_idx])
            except Exception as e:
                print(f"Warning: {e}")

        return results

    def save(self, path: Optional[Path] = None):
        """Save model (without embedder - it will be reloaded)."""
        path = Path(path) if path else MODEL_DIR
        path.mkdir(parents=True, exist_ok=True)

        # Temporarily remove embedder (too large to pickle, will reload)
        embedder = self.embedder
        self.embedder = None

        with open(path / "embedding_classifier.pkl", "wb") as f:
            pickle.dump(self, f)

        self.embedder = embedder
        print(f"  Model saved to: {path / 'embedding_classifier.pkl'}")

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "EmbeddingClassifier":
        """Load model."""
        path = Path(path) if path else MODEL_DIR
        with open(path / "embedding_classifier.pkl", "rb") as f:
            clf = pickle.load(f)
        return clf


# ============================================================================
# Data Loading
# ============================================================================

def load_data(split: str) -> tuple[list[str], list[int], list[str], list[str]]:
    """Load data from a split."""
    texts = []
    relevance = []
    priorities = []
    aks = []

    path = DATA_DIR / f"{split}.jsonl"
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            record = json.loads(line)
            inp = record["input"]
            lab = record["labels"]

            # Text: title + truncated content
            text = f"{inp['title']} {inp['content'][:1500]}"
            if inp.get("source"):
                text += f" Quelle: {inp['source']}"
            texts.append(text)

            is_rel = 1 if lab["relevant"] else 0
            relevance.append(is_rel)

            priority = lab.get("priority") or "none"
            if priority == "information":
                priority = "low"
            priorities.append(priority)

            ak = lab.get("ak") or "none"
            aks.append(ak)

    return texts, relevance, priorities, aks


# ============================================================================
# Evaluation
# ============================================================================

def evaluate(clf: EmbeddingClassifier, texts: list[str], relevance: list[int],
             priorities: list[str], aks: list[str]) -> dict:
    """Evaluate the classifier."""
    predictions = clf.predict_batch(texts)

    # Relevance
    y_true_rel = np.array(relevance)
    y_pred_rel = np.array([1 if p["relevant"] else 0 for p in predictions])

    rel_acc = accuracy_score(y_true_rel, y_pred_rel)
    rel_f1 = f1_score(y_true_rel, y_pred_rel)

    print("\n=== RELEVANCE (binary) ===")
    print(classification_report(y_true_rel, y_pred_rel, target_names=["irrelevant", "relevant"]))
    print(f"Accuracy: {rel_acc:.1%}, F1: {rel_f1:.1%}")

    # Priority (on true relevant)
    relevant_mask = y_true_rel == 1
    priorities_arr = np.array(priorities)
    valid_priority = np.array([p in PRIORITY_LEVELS for p in priorities_arr])
    eval_mask = relevant_mask & valid_priority

    if np.sum(eval_mask) > 0:
        y_true_priority = priorities_arr[eval_mask]
        y_pred_priority = np.array([p["priority"] or "medium" for p in predictions])[eval_mask]

        priority_acc = accuracy_score(y_true_priority, y_pred_priority)
        print("\n=== PRIORITY (4-class) ===")
        print(classification_report(y_true_priority, y_pred_priority, labels=PRIORITY_LEVELS, zero_division=0))
        print(f"Accuracy: {priority_acc:.1%}")

        # Within-1-level
        level_map = {p: i for i, p in enumerate(PRIORITY_LEVELS)}
        within_one = np.mean([
            abs(level_map[t] - level_map.get(p, 1)) <= 1
            for t, p in zip(y_true_priority, y_pred_priority)
        ])
        print(f"Within-1-level: {within_one:.1%}")
    else:
        priority_acc = 0
        within_one = 0

    # AK (on true relevant)
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
        "relevance_f1": rel_f1,
        "priority_accuracy": priority_acc,
        "priority_within_one": within_one,
        "ak_accuracy": ak_acc,
    }


# ============================================================================
# Main
# ============================================================================

def main():
    print("=" * 60)
    print("Liga Embedding Classifier - Sentence Transformers")
    print("=" * 60)

    # Load data
    print("\n[1/4] Loading data...")
    train_texts, train_rel, train_pri, train_ak = load_data("train")
    val_texts, val_rel, val_pri, val_ak = load_data("validation")
    test_texts, test_rel, test_pri, test_ak = load_data("test")

    # Combine train + val
    all_texts = train_texts + val_texts
    all_rel = train_rel + val_rel
    all_pri = train_pri + val_pri
    all_ak = train_ak + val_ak

    print(f"  Training: {len(all_texts)} items")
    print(f"  Test: {len(test_texts)} items")

    rel_count = sum(all_rel)
    print(f"  Relevant: {rel_count} ({rel_count/len(all_rel)*100:.1f}%)")

    pri_dist = Counter([p for p, r in zip(all_pri, all_rel) if r and p in PRIORITY_LEVELS])
    print(f"  Priority: {dict(pri_dist)}")

    ak_dist = Counter([a for a, r in zip(all_ak, all_rel) if r and a in AK_CLASSES])
    print(f"  AK: {dict(ak_dist)}")

    # Train
    print("\n[2/4] Training classifier...")
    clf = EmbeddingClassifier()
    clf.fit(all_texts, all_rel, all_pri, all_ak)

    # Evaluate
    print("\n[3/4] Evaluating on test set...")
    metrics = evaluate(clf, test_texts, test_rel, test_pri, test_ak)

    # Speed benchmark
    print("\n[4/4] Speed benchmark...")
    # Warmup
    clf.predict_batch(test_texts[:10])

    start = time.perf_counter()
    clf.predict_batch(test_texts)
    elapsed = time.perf_counter() - start
    speed = len(test_texts) / elapsed
    print(f"  Speed: {speed:.1f} items/sec ({1000/speed:.1f}ms per item)")
    print(f"  Note: Embedding is the bottleneck, sklearn prediction is instant")

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

    # Comparison with TF-IDF
    print("\n=== COMPARISON ===")
    print("                    TF-IDF    Embeddings")
    print(f"  Relevance:         83.9%     {metrics['relevance_accuracy']:.1%}")
    print(f"  Priority:          63.2%     {metrics['priority_accuracy']:.1%}")
    print(f"  AK:                39.5%     {metrics['ak_accuracy']:.1%}")

    # Examples
    print("\n=== Example Predictions ===")
    examples = [
        ("Hessen kürzt Kita-Mittel um 50 Millionen Euro",
         "Die Landesregierung plant Kürzungen bei der Kinderbetreuung.",
         "hessenschau.de"),
        ("Champions League: Bayern München gewinnt",
         "Mit 3:0 siegte Bayern gegen den italienischen Meister.",
         "sport1.de"),
        ("Neue Pflegeheime brauchen mehr Personal",
         "Das Sozialministerium fordert bessere Personalausstattung in der Altenpflege.",
         "tagesschau.de"),
        ("Flüchtlingsunterkünfte in Frankfurt überfüllt",
         "Die Erstaufnahme meldet Kapazitätsengpässe bei der Unterbringung.",
         "fr.de"),
    ]

    for title, content, source in examples:
        pred = clf.predict(title, content, source)
        status = "RELEVANT" if pred["relevant"] else "irrelevant"
        print(f"\n  [{status}] {title[:45]}...")
        print(f"    relevance: {pred['relevance_confidence']:.0%}")
        if pred["relevant"]:
            print(f"    priority: {pred['priority']} ({pred['priority_confidence']:.0%})")
            print(f"    ak: {pred['ak']} ({pred['ak_confidence']:.0%})")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
