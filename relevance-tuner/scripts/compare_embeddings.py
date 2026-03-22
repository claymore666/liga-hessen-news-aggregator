#!/usr/bin/env python3
"""
Compare embedding models for classification and duplicate detection.

Tests voyage-3.5 and gemini-embedding-001 against the current
nomic-embed-text-v2-moe (classification) and paraphrase-multilingual (dedup).

Usage:
    python scripts/compare_embeddings.py [--ollama-url http://docker-ai:11434]
"""

import argparse
import json
import sys
import time
from pathlib import Path

import httpx
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    accuracy_score, f1_score, precision_score, recall_score,
    confusion_matrix,
)

# Add parent dir for feature_extraction
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "classifier-api"))
from feature_extraction import extract_features

EVAL_SET = Path(__file__).resolve().parent.parent / "evaluations" / "eval_set.json"
TRAIN_DATA = Path(__file__).resolve().parent.parent / "data" / "final" / "train.jsonl"
TEST_DATA = Path(__file__).resolve().parent.parent / "data" / "final" / "test.jsonl"

MODELS = {
    "nomic-v2-moe": "nomic-embed-text-v2-moe",
    "nomic-v1.5": "nomic-embed-text:137m-v1.5-fp16",
    "paraphrase": "paraphrase-multilingual:278m-mpnet-base-v2-fp16",
    "gemini": "gemini-embedding-001",
}


MODEL_LIMITS = {
    "paraphrase-multilingual:278m-mpnet-base-v2-fp16": {"max_chars": 800, "batch_size": 1},
    "gemini-embedding-001": {"max_chars": 2000, "batch_size": 2},
}


def embed_texts(client: httpx.Client, model: str, texts: list[str], batch_size: int = 8) -> list[list[float]]:
    """Embed texts via Ollama /api/embed endpoint with retry on rate limit."""
    limits = MODEL_LIMITS.get(model, {})
    max_chars = limits.get("max_chars", 8000)
    batch_size = limits.get("batch_size", batch_size)
    texts = [t[:max_chars] for t in texts]

    dims = None  # Will be set from first successful response
    all_embeddings = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        for attempt in range(20):
            resp = client.post("/api/embed", json={"model": model, "input": batch})
            data = resp.json()
            if resp.status_code == 429 or "rate limit" in data.get("error", "").lower():
                wait = 75  # Wait longer than the 60s cooldown to avoid re-triggering
                print(f"  Rate limited, waiting {wait}s (attempt {attempt+1}/5)...")
                time.sleep(wait)
                continue
            if "context length" in data.get("error", "").lower() or "input length" in data.get("error", "").lower():
                # Truncate more aggressively and retry with individual items
                for text in batch:
                    for trunc in [400, 200, 100]:
                        r2 = client.post("/api/embed", json={"model": model, "input": [text[:trunc]]})
                        d2 = r2.json()
                        if r2.status_code == 200 and "error" not in d2:
                            embs = d2.get("embeddings", [[]])
                            if embs and embs[0]:
                                all_embeddings.append(embs[0])
                                if dims is None:
                                    dims = len(embs[0])
                                break
                    else:
                        # Give up on this item, use zero vector
                        all_embeddings.append([0.0] * (dims or 768))
                break
            if resp.status_code != 200:
                raise RuntimeError(f"Embed error {resp.status_code} for {model}: {data.get('error', resp.text)}")
            if "error" in data:
                raise RuntimeError(f"Embed error for {model}: {data['error']}")
            embeddings = data.get("embeddings", [])
            if not embeddings or not embeddings[0]:
                raise RuntimeError(f"Empty embeddings from {model} (batch {i})")
            if dims is None:
                dims = len(embeddings[0])
            all_embeddings.extend(embeddings)
            break
        else:
            raise RuntimeError(f"Rate limit exhausted for {model} after 20 retries")
        if i > 0 and i % 200 == 0:
            print(f"  ... {i}/{len(texts)}")
    return all_embeddings


def load_eval_set() -> list[dict]:
    with open(EVAL_SET) as f:
        data = json.load(f)
    return data["items"]


def load_training_data() -> tuple[list[dict], list[dict]]:
    """Load train and test splits from exported training data."""
    train_items = []
    with open(TRAIN_DATA) as f:
        for line in f:
            d = json.loads(line)
            train_items.append({
                "title": d["input"]["title"],
                "content": d["input"]["content"],
                "source": d["input"].get("source", ""),
                "relevant": d["labels"]["relevant"],
            })

    test_items = []
    with open(TEST_DATA) as f:
        for line in f:
            d = json.loads(line)
            test_items.append({
                "title": d["input"]["title"],
                "content": d["input"]["content"],
                "source": d["input"].get("source", ""),
                "relevant": d["labels"]["relevant"],
            })

    return train_items, test_items


def _prepare_texts(items: list[dict]) -> list[str]:
    """Format texts the same way as the production classifier."""
    texts = []
    for item in items:
        text = f"{item['title']} {item['content']}"
        if item.get("source"):
            text += f" Quelle: {item['source']}"
        texts.append(text)
    return texts


def run_classification_test(client: httpx.Client, train_items: list[dict],
                            test_items: list[dict], model_name: str, model_id: str):
    """Train RF classifier on train set, evaluate on test set."""
    print(f"\n{'='*60}")
    print(f"CLASSIFICATION: {model_name} ({model_id})")
    print(f"{'='*60}")

    train_texts = _prepare_texts(train_items)
    test_texts = _prepare_texts(test_items)
    y_train = np.array([1 if it["relevant"] else 0 for it in train_items])
    y_test = np.array([1 if it["relevant"] else 0 for it in test_items])

    print(f"  Train: {len(train_texts)} ({y_train.sum()} relevant)")
    print(f"  Test:  {len(test_texts)} ({y_test.sum()} relevant)")

    # Embed train
    print(f"  Embedding train set...")
    t0 = time.time()
    train_emb = embed_texts(client, model_id, train_texts)
    t_train = time.time() - t0
    X_train_emb = np.array(train_emb)
    print(f"  Train done in {t_train:.1f}s ({len(train_texts)/t_train:.1f} items/s), dims={X_train_emb.shape[1]}")

    # Embed test
    print(f"  Embedding test set...")
    t0 = time.time()
    test_emb = embed_texts(client, model_id, test_texts)
    t_test = time.time() - t0
    X_test_emb = np.array(test_emb)
    print(f"  Test done in {t_test:.1f}s")

    elapsed = t_train + t_test

    # Extract geographic features
    from sklearn.preprocessing import StandardScaler
    train_features = np.array([
        extract_features(it["title"], it["content"], it.get("source", ""))
        for it in train_items
    ])
    test_features = np.array([
        extract_features(it["title"], it["content"], it.get("source", ""))
        for it in test_items
    ])

    scaler = StandardScaler()
    train_feat_scaled = scaler.fit_transform(train_features)
    test_feat_scaled = scaler.transform(test_features)

    X_train_full = np.hstack([X_train_emb, train_feat_scaled])
    X_test_full = np.hstack([X_test_emb, test_feat_scaled])

    results = {}
    for label, X_tr, X_te in [("embeddings only", X_train_emb, X_test_emb),
                                ("emb + features", X_train_full, X_test_full)]:
        clf = RandomForestClassifier(n_estimators=200, class_weight="balanced",
                                     random_state=42, n_jobs=-1)
        clf.fit(X_tr, y_train)
        y_pred = clf.predict(X_te)

        # Also get probability scores to test at threshold 0.6 (like production)
        y_proba = clf.predict_proba(X_te)[:, list(clf.classes_).index(1)]
        y_pred_t06 = (y_proba >= 0.6).astype(int)

        acc = accuracy_score(y_test, y_pred)
        f1 = f1_score(y_test, y_pred)
        prec = precision_score(y_test, y_pred, zero_division=0)
        rec = recall_score(y_test, y_pred)
        tn, fp, fn, tp = confusion_matrix(y_test, y_pred).ravel()

        print(f"\n  [{label}] @threshold=0.5")
        print(f"  Accuracy:  {acc:.1%}")
        print(f"  F1:        {f1:.1%}")
        print(f"  Precision: {prec:.1%}")
        print(f"  Recall:    {rec:.1%}")
        print(f"  FP={fp}  FN={fn}  (TP={tp}  TN={tn})")

        # Threshold 0.6 (production)
        acc_t = accuracy_score(y_test, y_pred_t06)
        f1_t = f1_score(y_test, y_pred_t06)
        prec_t = precision_score(y_test, y_pred_t06, zero_division=0)
        rec_t = recall_score(y_test, y_pred_t06)
        tn_t, fp_t, fn_t, tp_t = confusion_matrix(y_test, y_pred_t06).ravel()

        print(f"\n  [{label}] @threshold=0.6 (production)")
        print(f"  Accuracy:  {acc_t:.1%}")
        print(f"  F1:        {f1_t:.1%}")
        print(f"  Precision: {prec_t:.1%}")
        print(f"  Recall:    {rec_t:.1%}")
        print(f"  FP={fp_t}  FN={fn_t}  (TP={tp_t}  TN={tn_t})")

        key = "emb_only" if "only" in label else "emb_features"
        results[key] = {
            "t05": {"accuracy": acc, "f1": f1, "precision": prec, "recall": rec,
                    "fn": int(fn), "fp": int(fp)},
            "t06": {"accuracy": acc_t, "f1": f1_t, "precision": prec_t, "recall": rec_t,
                    "fn": int(fn_t), "fp": int(fp_t)},
        }

    return {
        "model": model_name,
        "dims": X_train_emb.shape[1],
        "embed_time_s": round(elapsed, 1),
        "train_size": len(train_items),
        "test_size": len(test_items),
        **results,
    }


def run_duplicate_test(client: httpx.Client, items: list[dict], model_name: str, model_id: str):
    """Test duplicate detection quality using cosine similarity.

    Strategy:
    - True positives: items about the same topic (same subcategory in eval set)
      that SHOULD have high similarity
    - True negatives: items from different categories that should be dissimilar
    - Measure separation between the two distributions
    """
    print(f"\n{'='*60}")
    print(f"DUPLICATE DETECTION: {model_name} ({model_id})")
    print(f"{'='*60}")

    # Prepare texts
    texts = [f"{item['title']} {item['content']}" for item in items]

    # Embed
    print(f"  Embedding {len(texts)} texts...")
    t0 = time.time()
    embeddings = embed_texts(client, model_id, texts)
    elapsed = time.time() - t0
    X = np.array(embeddings)
    print(f"  Done in {elapsed:.1f}s, dims={X.shape[1]}")

    # Normalize for cosine similarity
    norms = np.linalg.norm(X, axis=1, keepdims=True)
    X_norm = X / norms

    # Compute full similarity matrix
    sim_matrix = X_norm @ X_norm.T

    # Group items by subcategory for "same topic" pairs
    from collections import defaultdict
    subcat_groups = defaultdict(list)
    for idx, item in enumerate(items):
        subcat = item.get("subcategory", "unknown")
        subcat_groups[subcat].append(idx)

    # Same-topic pairs (within subcategory, different items)
    same_topic_sims = []
    for subcat, indices in subcat_groups.items():
        if len(indices) < 2:
            continue
        for i in range(len(indices)):
            for j in range(i + 1, len(indices)):
                same_topic_sims.append(sim_matrix[indices[i], indices[j]])

    # Different-topic pairs (random sample across subcategories)
    rng = np.random.RandomState(42)
    subcats = list(subcat_groups.keys())
    diff_topic_sims = []
    for _ in range(len(same_topic_sims) * 2):  # 2x as many negatives
        sc1, sc2 = rng.choice(len(subcats), 2, replace=False)
        i = rng.choice(subcat_groups[subcats[sc1]])
        j = rng.choice(subcat_groups[subcats[sc2]])
        diff_topic_sims.append(sim_matrix[i, j])

    same_topic_sims = np.array(same_topic_sims)
    diff_topic_sims = np.array(diff_topic_sims)

    print(f"\n  Same-topic pairs:  n={len(same_topic_sims)}")
    print(f"    mean={same_topic_sims.mean():.3f}  median={np.median(same_topic_sims):.3f}  "
          f"std={same_topic_sims.std():.3f}  min={same_topic_sims.min():.3f}  max={same_topic_sims.max():.3f}")

    print(f"  Diff-topic pairs:  n={len(diff_topic_sims)}")
    print(f"    mean={diff_topic_sims.mean():.3f}  median={np.median(diff_topic_sims):.3f}  "
          f"std={diff_topic_sims.std():.3f}  min={diff_topic_sims.min():.3f}  max={diff_topic_sims.max():.3f}")

    # Separation: how well does the model distinguish same vs different topics?
    separation = same_topic_sims.mean() - diff_topic_sims.mean()
    # Cohen's d effect size
    pooled_std = np.sqrt((same_topic_sims.std()**2 + diff_topic_sims.std()**2) / 2)
    cohens_d = separation / pooled_std if pooled_std > 0 else 0

    print(f"\n  Separation (mean diff): {separation:.3f}")
    print(f"  Cohen's d:             {cohens_d:.2f}")

    # Test at typical thresholds
    for threshold in [0.70, 0.75, 0.80, 0.85]:
        tp = (same_topic_sims >= threshold).sum()
        fp = (diff_topic_sims >= threshold).sum()
        fn = (same_topic_sims < threshold).sum()
        tn = (diff_topic_sims < threshold).sum()
        prec = tp / (tp + fp) if (tp + fp) > 0 else 0
        rec = tp / (tp + fn) if (tp + fn) > 0 else 0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0
        print(f"  @threshold={threshold}: precision={prec:.2f} recall={rec:.2f} f1={f1:.2f} (TP={tp} FP={fp} FN={fn})")

    return {
        "model": model_name,
        "dims": X.shape[1],
        "embed_time_s": round(elapsed, 1),
        "same_topic_mean": round(float(same_topic_sims.mean()), 3),
        "diff_topic_mean": round(float(diff_topic_sims.mean()), 3),
        "separation": round(float(separation), 3),
        "cohens_d": round(float(cohens_d), 2),
        "n_same_pairs": len(same_topic_sims),
        "n_diff_pairs": len(diff_topic_sims),
    }


def main():
    parser = argparse.ArgumentParser(description="Compare embedding models")
    parser.add_argument("--ollama-url", default="http://docker-ai:11434",
                        help="Ollama API URL (default: http://docker-ai:11434)")
    parser.add_argument("--models", nargs="+", default=list(MODELS.keys()),
                        choices=list(MODELS.keys()),
                        help="Models to test (default: all)")
    parser.add_argument("--skip-classification", action="store_true")
    parser.add_argument("--skip-dedup", action="store_true")
    parser.add_argument("--max-train", type=int, default=0,
                        help="Max training items (0=all). Useful for rate-limited models.")
    args = parser.parse_args()

    print(f"Ollama URL: {args.ollama_url}")
    print(f"Models: {args.models}")

    client = httpx.Client(base_url=args.ollama_url, timeout=300.0)

    # Verify connectivity
    for name in args.models:
        model_id = MODELS[name]
        try:
            resp = client.post("/api/embed", json={"model": model_id, "input": ["test"]})
            data = resp.json()
            if "error" in data:
                print(f"ERROR: {name} ({model_id}): {data['error']}")
                sys.exit(1)
            dims = len(data.get("embeddings", [[]])[0])
            print(f"  {name}: OK (dims={dims})")
        except Exception as e:
            print(f"ERROR: {name} ({model_id}): {e}")
            sys.exit(1)

    # Load data
    eval_items = load_eval_set()
    print(f"\nEval set (dedup): {len(eval_items)} items")

    train_items, test_items = None, None
    if not args.skip_classification:
        train_items, test_items = load_training_data()
        if args.max_train > 0:
            # Stratified subsample
            rng = np.random.RandomState(42)
            rel_idx = [i for i, it in enumerate(train_items) if it["relevant"]]
            irr_idx = [i for i, it in enumerate(train_items) if not it["relevant"]]
            n_rel = int(args.max_train * len(rel_idx) / len(train_items))
            n_irr = args.max_train - n_rel
            chosen = list(rng.choice(rel_idx, n_rel, replace=False)) + \
                     list(rng.choice(irr_idx, n_irr, replace=False))
            train_items = [train_items[i] for i in sorted(chosen)]
            # Also subsample test proportionally
            max_test = int(args.max_train * len(test_items) / 3569)
            rel_idx_t = [i for i, it in enumerate(test_items) if it["relevant"]]
            irr_idx_t = [i for i, it in enumerate(test_items) if not it["relevant"]]
            n_rel_t = int(max_test * len(rel_idx_t) / len(test_items))
            n_irr_t = max_test - n_rel_t
            chosen_t = list(rng.choice(rel_idx_t, n_rel_t, replace=False)) + \
                       list(rng.choice(irr_idx_t, n_irr_t, replace=False))
            test_items = [test_items[i] for i in sorted(chosen_t)]
        print(f"Training data: {len(train_items)} train, {len(test_items)} test")

    classification_results = []
    dedup_results = []

    for name in args.models:
        model_id = MODELS[name]
        if not args.skip_classification:
            result = run_classification_test(client, train_items, test_items, name, model_id)
            classification_results.append(result)
        if not args.skip_dedup:
            result = run_duplicate_test(client, eval_items, name, model_id)
            dedup_results.append(result)

    # Summary tables
    if classification_results:
        print(f"\n{'='*90}")
        print("CLASSIFICATION SUMMARY (emb + features, @threshold=0.6)")
        print(f"{'='*90}")
        print(f"{'Model':<20} {'Dims':>5} {'Time':>6} {'Acc':>7} {'F1':>7} {'Prec':>7} {'Recall':>7} {'FN':>4} {'FP':>4}")
        print("-" * 90)
        for r in classification_results:
            m = r["emb_features"]["t06"]
            print(f"{r['model']:<20} {r['dims']:>5} {r['embed_time_s']:>5.1f}s "
                  f"{m['accuracy']:>6.1%} {m['f1']:>6.1%} {m['precision']:>6.1%} "
                  f"{m['recall']:>6.1%} {m['fn']:>4} {m['fp']:>4}")

    if dedup_results:
        print(f"\n{'='*80}")
        print("DUPLICATE DETECTION SUMMARY")
        print(f"{'='*80}")
        print(f"{'Model':<20} {'Dims':>5} {'Time':>6} {'Same':>7} {'Diff':>7} {'Sep':>7} {'Cohen d':>8}")
        print("-" * 80)
        for r in dedup_results:
            print(f"{r['model']:<20} {r['dims']:>5} {r['embed_time_s']:>5.1f}s "
                  f"{r['same_topic_mean']:>6.3f} {r['diff_topic_mean']:>6.3f} "
                  f"{r['separation']:>6.3f} {r['cohens_d']:>7.2f}")

    # Save results
    output = {
        "ollama_url": args.ollama_url,
        "eval_set_size": len(eval_items),
        "classification": classification_results,
        "duplicate_detection": dedup_results,
    }
    out_path = Path(__file__).resolve().parent.parent / "evaluations" / "embedding_comparison.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()
