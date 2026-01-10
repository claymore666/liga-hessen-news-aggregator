#!/usr/bin/env python3
"""
Liga Hessen AK (Arbeitskreis) Classifier

Dedicated sklearn model for AK assignment using sentence embeddings.
Classes: AK1, AK2, AK3, AK4, AK5, QAG (only for relevant items)

AK Definitions:
- AK1: Grundsatz und Sozialpolitik (general social policy)
- AK2: Migration und Flucht (migration, refugees, asylum)
- AK3: Gesundheit, Pflege und Senioren (health, care, elderly)
- AK4: Eingliederungshilfe (disability inclusion)
- AK5: Kinder, Jugend, Frauen und Familie (children, youth, families)
- QAG: Querschnitt - Digitalisierung, Klimaschutz, Wohnen (cross-cutting)

Usage:
    python train_ak_classifier.py

Production:
    from train_ak_classifier import AKClassifier
    clf = AKClassifier.load()
    result = clf.predict(title, content)
    # result = {"ak": "AK3", "confidence": 0.75, "probabilities": {...}}

Note: This classifier is for RELEVANT items only. Use a separate relevance
classifier first, then apply this for AK assignment.
"""

import json
import os
import pickle
import time
from pathlib import Path
from typing import Optional

import numpy as np
from sentence_transformers import SentenceTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from sklearn.preprocessing import LabelEncoder

# Content length for text truncation (env var or default 6000)
CONTENT_MAX_LENGTH = int(os.environ.get("CONTENT_MAX_LENGTH", 6000))

# ============================================================================
# Configuration
# ============================================================================

DATA_DIR = Path(__file__).parent / "data" / "final"
MODEL_DIR = Path(__file__).parent / "models" / "ak"

# AK classes (no irrelevant - this is for relevant items only)
AK_CLASSES = ["AK1", "AK2", "AK3", "AK4", "AK5", "QAG"]

# Embedding model
EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"

# Keywords per AK for feature engineering
AK_KEYWORDS = {
    "AK1": [  # Grundsatz und Sozialpolitik
        "sozialpolitik", "sozialstaat", "wohlfahrt", "gemeinnützig", "ehrenamt",
        "bürgerengagement", "zivilgesellschaft", "soziale arbeit", "träger",
        "verband", "freie wohlfahrtspflege", "sozialgesetzgebung", "grundsatz",
        "reform", "haushalt", "finanzierung", "förderung", "landesregierung",
    ],
    "AK2": [  # Migration und Flucht
        "migration", "flucht", "flüchtling", "asyl", "geflüchtete", "zuwanderung",
        "integration", "migranten", "einwanderung", "abschiebung", "aufenthalt",
        "aufnahme", "unterbringung", "erstaufnahme", "sprachkurs", "integrationskurs",
        "asylbewerber", "asylverfahren", "duldung", "bleiberecht", "einbürgerung",
    ],
    "AK3": [  # Gesundheit, Pflege und Senioren
        "pflege", "pflegekraft", "altenpflege", "pflegeheim", "krankenhaus",
        "gesundheit", "demenz", "senioren", "häusliche pflege", "pflegedienst",
        "pflegeversicherung", "pflegestufe", "pflegegrad", "pflegebedürftig",
        "kranken", "patient", "altersheim", "hospiz", "palliativ", "therapie",
    ],
    "AK4": [  # Eingliederungshilfe
        "eingliederungshilfe", "behinderung", "behindert", "inklusion", "teilhabe",
        "werkstatt", "barrierefreiheit", "bthg", "bundesteilhabegesetz",
        "schwerbehindert", "teilhabeplan", "persönliches budget", "wfbm",
        "assistenz", "förderung", "selbstbestimmung", "un-brk",
    ],
    "AK5": [  # Kinder, Jugend, Frauen und Familie
        "kita", "kindergarten", "kinderbetreuung", "jugend", "jugendhilfe",
        "familie", "familienberatung", "schwangerschaft", "kinderschutz",
        "erziehung", "eltern", "alleinerziehend", "jugendamt", "kindertagesstätte",
        "krippe", "hort", "schulkind", "frauenhaus", "beratungsstelle",
        "schwangerschaftskonflikt", "frühe hilfen", "frauenberatung",
    ],
    "QAG": [  # Querschnitt
        "digitalisierung", "klimaschutz", "wohnen", "wohnungsnot", "obdachlos",
        "wohnungslos", "sozialraum", "nachbarschaft", "quartier", "energiearmut",
        "nachhaltigkeit", "mobilität", "ländlicher raum", "stadtentwicklung",
        "armut", "armutsbericht", "existenzsicherung", "grundsicherung",
    ],
}

# Liga organizations (boost relevance)
LIGA_ORGS = ["liga", "awo", "caritas", "diakonie", "drk", "paritätisch", "wohlfahrt"]

# Hessen-specific terms
HESSEN_TERMS = ["hessen", "hessisch", "landesregierung", "landtag", "wiesbaden", "frankfurt"]


# ============================================================================
# Feature Engineering
# ============================================================================

def count_keywords(text: str, keywords: list[str]) -> int:
    """Count keyword occurrences (case-insensitive)."""
    text_lower = text.lower()
    return sum(1 for kw in keywords if kw.lower() in text_lower)


def extract_ak_features(texts: list[str]) -> np.ndarray:
    """Extract AK-specific features."""
    features = []
    for text in texts:
        text_lower = text.lower()
        row = [
            count_keywords(text, AK_KEYWORDS["AK1"]),
            count_keywords(text, AK_KEYWORDS["AK2"]),
            count_keywords(text, AK_KEYWORDS["AK3"]),
            count_keywords(text, AK_KEYWORDS["AK4"]),
            count_keywords(text, AK_KEYWORDS["AK5"]),
            count_keywords(text, AK_KEYWORDS["QAG"]),
            count_keywords(text, LIGA_ORGS),
            count_keywords(text, HESSEN_TERMS),
            len(text),
        ]
        features.append(row)
    return np.array(features, dtype=np.float32)


# ============================================================================
# Classifier
# ============================================================================

class AKClassifier:
    """
    AK (Arbeitskreis) classifier using sentence embeddings.

    Predicts: AK1, AK2, AK3, AK4, AK5, QAG (for relevant items only)

    Uses paraphrase-multilingual-MiniLM-L12-v2 embeddings with LogisticRegression.
    Achieved 71.1% accuracy in experiments (vs 39.5% with TF-IDF).
    """

    def __init__(self):
        self.embedding_model = None  # Lazy load
        self.clf = LogisticRegression(
            max_iter=1000,
            class_weight="balanced",
            random_state=42,
        )
        self.label_encoder = LabelEncoder()
        self.is_fitted = False

    def _load_embedding_model(self):
        """Lazy load embedding model."""
        if self.embedding_model is None:
            print(f"  Loading embedding model: {EMBEDDING_MODEL}")
            self.embedding_model = SentenceTransformer(EMBEDDING_MODEL)

    def _prepare_text(self, title: str, content: str, source: Optional[str] = None) -> str:
        """Prepare text for classification."""
        text = f"{title} {content[:CONTENT_MAX_LENGTH]}"
        if source:
            text += f" Quelle: {source}"
        return text

    def _get_embeddings(self, texts: list[str]) -> np.ndarray:
        """Get embeddings for texts."""
        self._load_embedding_model()
        return self.embedding_model.encode(texts, show_progress_bar=len(texts) > 10)

    def fit(self, texts: list[str], aks: list[str]):
        """
        Train the classifier.

        Args:
            texts: List of text strings (relevant items only)
            aks: List of AK labels (AK1/.../QAG)
        """
        print("  Computing embeddings...")
        X = self._get_embeddings(texts)
        print(f"  Embedding matrix: {X.shape}")

        print("  Encoding labels...")
        self.label_encoder.fit(AK_CLASSES)
        y = self.label_encoder.transform(aks)

        print("  Training classifier...")
        self.clf.fit(X, y)

        self.is_fitted = True
        print("  Training complete!")

    def predict(self, title: str, content: str, source: Optional[str] = None) -> dict:
        """
        Predict AK for a single item.

        Returns:
            {"ak": "AK3", "confidence": 0.75, "probabilities": {...}}
        """
        text = self._prepare_text(title, content, source)
        return self.predict_text(text)

    def predict_text(self, text: str) -> dict:
        """Predict from combined text."""
        X = self._get_embeddings([text])
        proba = self.clf.predict_proba(X)[0]
        pred_idx = np.argmax(proba)

        ak = self.label_encoder.inverse_transform([pred_idx])[0]
        confidence = float(proba[pred_idx])

        probabilities = {
            self.label_encoder.inverse_transform([i])[0]: float(p)
            for i, p in enumerate(proba)
        }

        return {
            "ak": ak,
            "confidence": confidence,
            "probabilities": probabilities,
        }

    def predict_batch(self, texts: list[str]) -> list[dict]:
        """Predict for multiple texts efficiently."""
        X = self._get_embeddings(texts)
        proba = self.clf.predict_proba(X)
        pred_indices = np.argmax(proba, axis=1)

        results = []
        for i, (idx, p) in enumerate(zip(pred_indices, proba)):
            ak = self.label_encoder.inverse_transform([idx])[0]
            results.append({
                "ak": ak,
                "confidence": float(p[idx]),
                "probabilities": {
                    self.label_encoder.inverse_transform([j])[0]: float(prob)
                    for j, prob in enumerate(p)
                },
            })
        return results

    def save(self, path: Optional[Path] = None):
        """Save model to disk (without embedding model - it's loaded on demand)."""
        path = Path(path) if path else MODEL_DIR
        path.mkdir(parents=True, exist_ok=True)
        # Don't save the embedding model - it's large and loaded on demand
        embedding_model = self.embedding_model
        self.embedding_model = None
        with open(path / "ak_classifier.pkl", "wb") as f:
            pickle.dump(self, f)
        self.embedding_model = embedding_model
        print(f"  Model saved to: {path / 'ak_classifier.pkl'}")

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "AKClassifier":
        """Load model from disk."""
        path = Path(path) if path else MODEL_DIR
        with open(path / "ak_classifier.pkl", "rb") as f:
            return pickle.load(f)


# ============================================================================
# Data Loading
# ============================================================================

def load_data() -> tuple[list[str], list[str]]:
    """Load and prepare training data (relevant items only)."""
    texts = []
    aks = []

    for split in ["train.jsonl", "validation.jsonl"]:
        path = DATA_DIR / split
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                record = json.loads(line)
                inp = record["input"]
                lab = record["labels"]

                # Only include relevant items
                if not lab["relevant"]:
                    continue

                # Prepare text
                text = f"{inp['title']} {inp['content'][:CONTENT_MAX_LENGTH]}"
                if inp.get("source"):
                    text += f" Quelle: {inp['source']}"
                texts.append(text)

                # Get AK
                ak = lab.get("assigned_ak") or lab.get("ak") or "QAG"
                aks.append(ak)

    return texts, aks


def load_test_data() -> tuple[list[str], list[str]]:
    """Load test data (relevant items only)."""
    texts = []
    aks = []

    path = DATA_DIR / "test.jsonl"
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            record = json.loads(line)
            inp = record["input"]
            lab = record["labels"]

            # Only include relevant items
            if not lab["relevant"]:
                continue

            text = f"{inp['title']} {inp['content'][:CONTENT_MAX_LENGTH]}"
            if inp.get("source"):
                text += f" Quelle: {inp['source']}"
            texts.append(text)

            ak = lab.get("assigned_ak") or lab.get("ak") or "QAG"
            aks.append(ak)

    return texts, aks


# ============================================================================
# Evaluation
# ============================================================================

def evaluate(clf: AKClassifier, texts: list[str], true_aks: list[str]) -> dict:
    """Evaluate classifier performance on relevant items."""
    predictions = clf.predict_batch(texts)
    pred_aks = [p["ak"] for p in predictions]

    print("\n=== AK Classification Report ===")
    print(classification_report(
        true_aks, pred_aks,
        labels=AK_CLASSES,
        zero_division=0
    ))

    accuracy = accuracy_score(true_aks, pred_aks)

    # Confusion matrix
    cm = confusion_matrix(true_aks, pred_aks, labels=AK_CLASSES)
    print("\nConfusion Matrix:")
    print(f"{'':>8} " + " ".join(f"{p:>6}" for p in AK_CLASSES))
    for i, p in enumerate(AK_CLASSES):
        print(f"{p:>8} " + " ".join(f"{cm[i,j]:>6}" for j in range(len(AK_CLASSES))))

    print(f"\n=== Summary ===")
    print(f"  AK accuracy: {accuracy:.1%}")

    return {
        "accuracy": accuracy,
    }


def benchmark_speed(clf: AKClassifier, texts: list[str], n_runs: int = 5) -> float:
    """Benchmark prediction speed."""
    clf.predict_batch(texts[:10])  # Warmup

    times = []
    for _ in range(n_runs):
        start = time.perf_counter()
        clf.predict_batch(texts)
        elapsed = time.perf_counter() - start
        times.append(elapsed)

    return len(texts) / np.mean(times)


# ============================================================================
# Main
# ============================================================================

def main():
    print("=" * 60)
    print("Liga Hessen AK Classifier (Embeddings)")
    print("=" * 60)
    print("Note: This classifier is for RELEVANT items only.")

    # Load data
    print("\n[1/4] Loading training data (relevant items only)...")
    train_texts, train_aks = load_data()
    test_texts, test_aks = load_test_data()

    print(f"  Training items: {len(train_texts)}")
    print(f"  Test items: {len(test_texts)}")

    # Distribution
    from collections import Counter
    dist = Counter(train_aks)
    print(f"\n  AK distribution (train):")
    for ak in AK_CLASSES:
        count = dist.get(ak, 0)
        pct = count/len(train_aks)*100 if train_aks else 0
        print(f"    {ak:>8}: {count:>4} ({pct:.1f}%)")

    # Train
    print("\n[2/4] Training classifier...")
    clf = AKClassifier()
    clf.fit(train_texts, train_aks)

    # Evaluate
    print("\n[3/4] Evaluating on test set...")
    metrics = evaluate(clf, test_texts, test_aks)

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
        ("Neue Kita-Förderung in Hessen beschlossen",
         "Die Landesregierung erhöht die Mittel für Kinderbetreuung um 100 Millionen Euro.",
         "hessenschau.de"),
        ("Pflegereform: Mehr Personal für Altenheime",
         "Das neue Pflegegesetz sieht eine bessere Personalausstattung in der Altenpflege vor.",
         "tagesschau.de"),
        ("Flüchtlingsunterkünfte in Frankfurt überfüllt",
         "Die Erstaufnahmeeinrichtung meldet Kapazitätsengpässe bei der Unterbringung von Asylbewerbern.",
         "fr.de"),
        ("Champions League: Bayern München gewinnt",
         "Mit 3:0 siegte Bayern gegen den italienischen Meister.",
         "sport1.de"),
        ("Inklusion: Werkstätten brauchen mehr Förderung",
         "Die Werkstätten für behinderte Menschen fordern mehr Mittel vom Bund.",
         "kobinet.de"),
    ]

    for title, content, source in examples:
        pred = clf.predict(title, content, source)
        print(f"\n  {title[:50]}...")
        print(f"    → {pred['ak']} ({pred['confidence']:.0%})")

    print("\n" + "=" * 60)
    print("Training complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
