#!/usr/bin/env python3
"""
Liga Hessen Priority Classifier

Dedicated sklearn model for priority classification.
Classes: irrelevant, low, medium, high, critical

Usage:
    python train_priority_classifier.py

Production:
    from train_priority_classifier import PriorityClassifier
    clf = PriorityClassifier.load()
    result = clf.predict(title, content)
    # result = {"priority": "high", "confidence": 0.85}
"""

import json
import os
import pickle
import time
from pathlib import Path
from typing import Optional

import numpy as np

# Content length for text truncation (env var or default 6000)
CONTENT_MAX_LENGTH = int(os.environ.get("CONTENT_MAX_LENGTH", 6000))
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from sklearn.preprocessing import LabelEncoder

# ============================================================================
# Configuration
# ============================================================================

DATA_DIR = Path(__file__).parent / "data" / "final"
MODEL_DIR = Path(__file__).parent / "models" / "priority"

# Priority levels (ordered from lowest to highest)
PRIORITY_LEVELS = ["irrelevant", "low", "medium", "high", "critical"]

# Keywords that indicate urgency/priority
URGENT_KEYWORDS = [
    "sofort", "dringend", "akut", "krise", "notfall", "eilig",
    "kürzung", "streichung", "einschnitt", "abbau",
    "deadline", "frist", "beschluss", "abstimmung", "entscheidung",
    "gesetz", "verordnung", "reform", "novelle",
    "stellungnahme", "anhörung", "protest", "demonstration",
    "warnung", "alarm", "kritik", "mangel", "notstand",
]

HESSEN_KEYWORDS = [
    "hessen", "hessisch", "landesregierung", "landtag", "staatsminister",
    "wiesbaden", "frankfurt", "kassel", "darmstadt", "offenbach",
]

LIGA_ORG_KEYWORDS = [
    "liga", "wohlfahrt", "awo", "caritas", "diakonie", "drk",
    "paritätisch", "freie wohlfahrtspflege",
]


# ============================================================================
# Feature Engineering
# ============================================================================

def count_keywords(text: str, keywords: list[str]) -> int:
    """Count keyword occurrences (case-insensitive)."""
    text_lower = text.lower()
    return sum(1 for kw in keywords if kw.lower() in text_lower)


def extract_priority_features(texts: list[str]) -> np.ndarray:
    """Extract priority-relevant features."""
    features = []
    for text in texts:
        text_lower = text.lower()
        features.append([
            count_keywords(text, URGENT_KEYWORDS),
            count_keywords(text, HESSEN_KEYWORDS),
            count_keywords(text, LIGA_ORG_KEYWORDS),
            len(text),  # Longer texts often more important
            text_lower.count("!"),  # Exclamation marks indicate urgency
            1 if "hessen" in text_lower else 0,
            1 if any(org in text_lower for org in ["liga", "wohlfahrt"]) else 0,
        ])
    return np.array(features, dtype=np.float32)


# ============================================================================
# Classifier
# ============================================================================

class PriorityClassifier:
    """
    Fast priority classifier for Liga news items.

    Predicts: irrelevant, low, medium, high, critical
    """

    def __init__(self):
        self.tfidf = TfidfVectorizer(
            max_features=3000,
            ngram_range=(1, 2),
            min_df=2,
            max_df=0.9,
            sublinear_tf=True,
        )
        self.clf = LogisticRegression(
            max_iter=500,
            solver="saga",
            class_weight="balanced",
            random_state=42,
        )
        self.label_encoder = LabelEncoder()
        self.is_fitted = False

    def _prepare_text(self, title: str, content: str, source: Optional[str] = None) -> str:
        """Prepare text for classification."""
        text = f"{title} {content[:CONTENT_MAX_LENGTH]}"
        if source:
            text += f" Quelle: {source}"
        return text

    def _extract_features(self, texts: list[str]) -> np.ndarray:
        """Extract combined TF-IDF and engineered features."""
        tfidf_features = self.tfidf.transform(texts).toarray()
        custom_features = extract_priority_features(texts)
        return np.hstack([tfidf_features, custom_features])

    def fit(self, texts: list[str], priorities: list[str]):
        """
        Train the classifier.

        Args:
            texts: List of text strings
            priorities: List of priority labels (irrelevant/low/medium/high/critical)
        """
        print("  Fitting TF-IDF vectorizer...")
        self.tfidf.fit(texts)

        print("  Extracting features...")
        X = self._extract_features(texts)
        print(f"  Feature matrix: {X.shape}")

        print("  Encoding labels...")
        self.label_encoder.fit(PRIORITY_LEVELS)  # Ensure consistent ordering
        y = self.label_encoder.transform(priorities)

        print("  Training classifier...")
        self.clf.fit(X, y)

        self.is_fitted = True
        print("  Training complete!")

    def predict(self, title: str, content: str, source: Optional[str] = None) -> dict:
        """
        Predict priority for a single item.

        Returns:
            {"priority": "high", "confidence": 0.85, "probabilities": {...}}
        """
        text = self._prepare_text(title, content, source)
        return self.predict_text(text)

    def predict_text(self, text: str) -> dict:
        """Predict from combined text."""
        X = self._extract_features([text])
        proba = self.clf.predict_proba(X)[0]
        pred_idx = np.argmax(proba)

        priority = self.label_encoder.inverse_transform([pred_idx])[0]
        confidence = float(proba[pred_idx])

        # All probabilities for debugging
        probabilities = {
            self.label_encoder.inverse_transform([i])[0]: float(p)
            for i, p in enumerate(proba)
        }

        return {
            "priority": priority,
            "confidence": confidence,
            "probabilities": probabilities,
        }

    def predict_batch(self, texts: list[str]) -> list[dict]:
        """Predict for multiple texts efficiently."""
        X = self._extract_features(texts)
        proba = self.clf.predict_proba(X)
        pred_indices = np.argmax(proba, axis=1)

        results = []
        for i, (idx, p) in enumerate(zip(pred_indices, proba)):
            priority = self.label_encoder.inverse_transform([idx])[0]
            results.append({
                "priority": priority,
                "confidence": float(p[idx]),
                "probabilities": {
                    self.label_encoder.inverse_transform([j])[0]: float(prob)
                    for j, prob in enumerate(p)
                },
            })
        return results

    def save(self, path: Optional[Path] = None):
        """Save model to disk."""
        path = Path(path) if path else MODEL_DIR
        path.mkdir(parents=True, exist_ok=True)
        with open(path / "priority_classifier.pkl", "wb") as f:
            pickle.dump(self, f)
        print(f"  Model saved to: {path / 'priority_classifier.pkl'}")

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "PriorityClassifier":
        """Load model from disk."""
        path = Path(path) if path else MODEL_DIR
        with open(path / "priority_classifier.pkl", "rb") as f:
            return pickle.load(f)


# ============================================================================
# Data Loading
# ============================================================================

def load_data() -> tuple[list[str], list[str]]:
    """Load and prepare training data."""
    texts = []
    priorities = []

    for split in ["train.jsonl", "validation.jsonl"]:
        path = DATA_DIR / split
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                record = json.loads(line)
                inp = record["input"]
                lab = record["labels"]

                # Prepare text
                text = f"{inp['title']} {inp['content'][:2000]}"
                if inp.get("source"):
                    text += f" Quelle: {inp['source']}"
                texts.append(text)

                # Determine priority
                if not lab["relevant"]:
                    priority = "irrelevant"
                else:
                    priority = lab.get("priority") or "medium"
                    # Normalize priority values
                    if priority == "information":
                        priority = "low"
                priorities.append(priority)

    return texts, priorities


def load_test_data() -> tuple[list[str], list[str]]:
    """Load test data."""
    texts = []
    priorities = []

    path = DATA_DIR / "test.jsonl"
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            record = json.loads(line)
            inp = record["input"]
            lab = record["labels"]

            text = f"{inp['title']} {inp['content'][:2000]}"
            if inp.get("source"):
                text += f" Quelle: {inp['source']}"
            texts.append(text)

            if not lab["relevant"]:
                priority = "irrelevant"
            else:
                priority = lab.get("priority") or "medium"
                if priority == "information":
                    priority = "low"
            priorities.append(priority)

    return texts, priorities


# ============================================================================
# Evaluation
# ============================================================================

def evaluate(clf: PriorityClassifier, texts: list[str], true_priorities: list[str]) -> dict:
    """Evaluate classifier performance."""
    predictions = clf.predict_batch(texts)
    pred_priorities = [p["priority"] for p in predictions]

    print("\n=== Priority Classification Report ===")
    print(classification_report(
        true_priorities, pred_priorities,
        labels=PRIORITY_LEVELS,
        zero_division=0
    ))

    accuracy = accuracy_score(true_priorities, pred_priorities)

    # Confusion matrix
    cm = confusion_matrix(true_priorities, pred_priorities, labels=PRIORITY_LEVELS)
    print("\nConfusion Matrix:")
    print(f"{'':>12} " + " ".join(f"{p:>8}" for p in PRIORITY_LEVELS))
    for i, p in enumerate(PRIORITY_LEVELS):
        print(f"{p:>12} " + " ".join(f"{cm[i,j]:>8}" for j in range(len(PRIORITY_LEVELS))))

    # Binary relevance accuracy (irrelevant vs all others)
    true_binary = [0 if p == "irrelevant" else 1 for p in true_priorities]
    pred_binary = [0 if p == "irrelevant" else 1 for p in pred_priorities]
    binary_acc = accuracy_score(true_binary, pred_binary)

    print(f"\n=== Summary ===")
    print(f"  Overall accuracy: {accuracy:.1%}")
    print(f"  Binary relevance accuracy: {binary_acc:.1%}")

    # Ordinal accuracy (within 1 level)
    level_map = {p: i for i, p in enumerate(PRIORITY_LEVELS)}
    within_one = sum(
        1 for t, p in zip(true_priorities, pred_priorities)
        if abs(level_map[t] - level_map[p]) <= 1
    ) / len(true_priorities)
    print(f"  Within-1-level accuracy: {within_one:.1%}")

    return {
        "accuracy": accuracy,
        "binary_accuracy": binary_acc,
        "within_one_accuracy": within_one,
    }


def benchmark_speed(clf: PriorityClassifier, texts: list[str], n_runs: int = 5) -> float:
    """Benchmark prediction speed."""
    # Warmup
    clf.predict_batch(texts[:10])

    times = []
    for _ in range(n_runs):
        start = time.perf_counter()
        clf.predict_batch(texts)
        elapsed = time.perf_counter() - start
        times.append(elapsed)

    avg_time = np.mean(times)
    items_per_sec = len(texts) / avg_time
    return items_per_sec


# ============================================================================
# Main
# ============================================================================

def main():
    print("=" * 60)
    print("Liga Hessen Priority Classifier")
    print("=" * 60)

    # Load data
    print("\n[1/4] Loading training data...")
    train_texts, train_priorities = load_data()
    test_texts, test_priorities = load_test_data()

    print(f"  Training items: {len(train_texts)}")
    print(f"  Test items: {len(test_texts)}")

    # Distribution
    from collections import Counter
    dist = Counter(train_priorities)
    print(f"\n  Priority distribution (train):")
    for p in PRIORITY_LEVELS:
        count = dist.get(p, 0)
        print(f"    {p:>12}: {count:>4} ({count/len(train_priorities)*100:.1f}%)")

    # Train
    print("\n[2/4] Training classifier...")
    clf = PriorityClassifier()
    clf.fit(train_texts, train_priorities)

    # Evaluate
    print("\n[3/4] Evaluating on test set...")
    metrics = evaluate(clf, test_texts, test_priorities)

    # Speed
    print("\n[4/4] Benchmarking speed...")
    speed = benchmark_speed(clf, test_texts)
    print(f"  Speed: {speed:.0f} items/sec ({1000/speed:.2f}ms per item)")

    # Save
    print("\n=== Saving Model ===")
    clf.save()

    # Examples
    print("\n=== Example Predictions ===")
    examples = [
        ("Hessen kürzt Mittel für Kitas um 50 Millionen Euro",
         "Die Landesregierung plant drastische Kürzungen bei der Kinderbetreuung. Ministerpräsident warnt vor dramatischen Folgen.",
         "hessenschau.de"),
        ("Champions League: Bayern München gegen Manchester City",
         "Das Spitzenspiel der Gruppenphase steht bevor.",
         "sport1.de"),
        ("Stellungnahme zur Pflegereform gefordert",
         "Die Liga der Freien Wohlfahrtspflege Hessen fordert eine Stellungnahme bis Freitag.",
         "liga-hessen.de"),
        ("Wetter in Frankfurt: Regen erwartet",
         "Am Wochenende wird es regnerisch in der Rhein-Main-Region.",
         "wetteronline.de"),
    ]

    for title, content, source in examples:
        pred = clf.predict(title, content, source)
        print(f"\n  {title[:50]}...")
        print(f"    → {pred['priority']} ({pred['confidence']:.0%})")

    print("\n" + "=" * 60)
    print("Training complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
