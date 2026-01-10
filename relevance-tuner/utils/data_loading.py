#!/usr/bin/env python3
"""
Unified data loading utilities for Liga Hessen classifiers.

Consolidates data loading logic from various training scripts into a single module.
No content truncation - embedders handle long texts via chunking.
"""

import json
import sys
from pathlib import Path
from typing import Optional

# Add parent directory to path for config import
sys.path.insert(0, str(__file__).rsplit("/", 2)[0])

from config import DATA_DIR


def load_training_data(
    splits: list[str] = ["train", "validation"],
    data_dir: Optional[Path] = None,
    include_source: bool = True,
) -> tuple[list[str], list[int], list[str], list[str]]:
    """
    Load training data from JSONL splits.

    Args:
        splits: List of splits to load (e.g., ["train", "validation"])
        data_dir: Data directory (default: config.DATA_DIR)
        include_source: Whether to append source to text

    Returns:
        Tuple of (texts, relevance_labels, priority_labels, ak_labels)
        - texts: List of formatted text strings
        - relevance_labels: List of 0/1 for relevant/irrelevant
        - priority_labels: List of priority strings (for relevant items only)
        - ak_labels: List of AK strings (for relevant items only)
    """
    if data_dir is None:
        data_dir = DATA_DIR

    texts = []
    relevance = []
    priorities = []
    aks = []

    for split in splits:
        path = data_dir / f"{split}.jsonl"
        if not path.exists():
            print(f"Warning: {path} not found, skipping")
            continue

        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue

                record = json.loads(line)
                inp = record.get("input", {})
                lab = record.get("labels", {})

                # Format text (no truncation - embedder handles long texts)
                title = inp.get("title", "")
                content = inp.get("content", "")
                text = f"{title} {content}"

                if include_source and inp.get("source"):
                    text += f" Quelle: {inp['source']}"

                texts.append(text)

                # Labels
                is_relevant = lab.get("relevant", False)
                relevance.append(1 if is_relevant else 0)

                if is_relevant:
                    priorities.append(lab.get("priority", "medium"))
                    aks.append(lab.get("ak", "AK1"))
                else:
                    priorities.append("")
                    aks.append("")

    return texts, relevance, priorities, aks


def load_test_data(
    data_dir: Optional[Path] = None,
    include_source: bool = True,
) -> tuple[list[str], list[int], list[str], list[str]]:
    """
    Load test split data.

    Convenience wrapper for load_training_data with splits=["test"].
    """
    return load_training_data(
        splits=["test"],
        data_dir=data_dir,
        include_source=include_source,
    )


def load_all_data(
    data_dir: Optional[Path] = None,
    include_source: bool = True,
) -> tuple[list[str], list[int], list[str], list[str]]:
    """
    Load all data (train + validation + test).

    Convenience wrapper for load_training_data with all splits.
    """
    return load_training_data(
        splits=["train", "validation", "test"],
        data_dir=data_dir,
        include_source=include_source,
    )


def load_relevant_only(
    splits: list[str] = ["train", "validation"],
    data_dir: Optional[Path] = None,
    include_source: bool = True,
) -> tuple[list[str], list[str], list[str]]:
    """
    Load only relevant items (for AK/priority-only classifiers).

    Args:
        splits: List of splits to load
        data_dir: Data directory
        include_source: Whether to append source to text

    Returns:
        Tuple of (texts, priority_labels, ak_labels) for relevant items only
    """
    texts, relevance, priorities, aks = load_training_data(
        splits=splits,
        data_dir=data_dir,
        include_source=include_source,
    )

    # Filter to relevant only
    rel_texts = []
    rel_priorities = []
    rel_aks = []

    for text, rel, pri, ak in zip(texts, relevance, priorities, aks):
        if rel == 1:
            rel_texts.append(text)
            rel_priorities.append(pri)
            rel_aks.append(ak)

    return rel_texts, rel_priorities, rel_aks


def get_data_stats(
    splits: list[str] = ["train", "validation", "test"],
    data_dir: Optional[Path] = None,
) -> dict:
    """
    Get statistics about the dataset.

    Returns dict with counts and distributions.
    """
    from collections import Counter

    texts, relevance, priorities, aks = load_training_data(
        splits=splits,
        data_dir=data_dir,
    )

    # Basic counts
    total = len(texts)
    relevant = sum(relevance)
    irrelevant = total - relevant

    # Priority distribution (relevant only)
    rel_priorities = [p for p, r in zip(priorities, relevance) if r == 1]
    priority_dist = Counter(rel_priorities)

    # AK distribution (relevant only)
    rel_aks = [a for a, r in zip(aks, relevance) if r == 1]
    ak_dist = Counter(rel_aks)

    # Text length stats
    lengths = [len(t) for t in texts]

    return {
        "total": total,
        "relevant": relevant,
        "irrelevant": irrelevant,
        "relevance_ratio": relevant / total if total > 0 else 0,
        "priority_distribution": dict(priority_dist),
        "ak_distribution": dict(ak_dist),
        "text_length": {
            "min": min(lengths) if lengths else 0,
            "max": max(lengths) if lengths else 0,
            "mean": sum(lengths) / len(lengths) if lengths else 0,
        },
    }


if __name__ == "__main__":
    # Quick test
    print("Testing data loading...")

    # Load training data
    texts, relevance, priorities, aks = load_training_data()
    print(f"Training data: {len(texts)} items")
    print(f"  Relevant: {sum(relevance)}")
    print(f"  Irrelevant: {len(texts) - sum(relevance)}")

    # Load test data
    test_texts, test_rel, test_pri, test_aks = load_test_data()
    print(f"Test data: {len(test_texts)} items")

    # Get stats
    stats = get_data_stats()
    print(f"\nDataset statistics:")
    print(f"  Total: {stats['total']}")
    print(f"  Relevant: {stats['relevant']} ({stats['relevance_ratio']:.1%})")
    print(f"  Priority distribution: {stats['priority_distribution']}")
    print(f"  AK distribution: {stats['ak_distribution']}")

    print("\nAll tests passed!")
