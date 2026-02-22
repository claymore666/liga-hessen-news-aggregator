#!/usr/bin/env python3
"""Run Ollama LLM against the fixed eval set and measure quality.

Content comes from the eval_set.json snapshot — no API needed. The system
prompt is read from processor.py (same as production).

Usage:
    python scripts/run_llm_eval.py                                    # defaults
    python scripts/run_llm_eval.py --model nemotron-3-nano-30b        # different model
    python scripts/run_llm_eval.py --prompt-tag prompts-v5            # label override

Requires:
    - Ollama running locally (or set OLLAMA_URL)
    - evaluations/eval_set.json (run curate_eval_set.py first)
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: requests not installed. Run: pip install requests")
    sys.exit(1)

# ============================================================================
# Configuration
# ============================================================================

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
DEFAULT_MODEL = "qwen3:14b-q8_0"
EVAL_SET_PATH = Path(__file__).parent.parent / "evaluations" / "eval_set.json"
RESULTS_DIR = Path(__file__).parent.parent / "evaluations" / "results"


# ============================================================================
# Shared functions (from compare_llm_models.py)
# ============================================================================

def get_system_prompt() -> str:
    """Read the production system prompt from processor.py."""
    processor_path = Path(__file__).parent.parent.parent / "news-aggregator" / "backend" / "services" / "processor.py"
    if not processor_path.exists():
        print(f"ERROR: Cannot find {processor_path}")
        sys.exit(1)

    content = processor_path.read_text()
    match = re.search(r'ANALYSIS_SYSTEM_PROMPT = """(.*?)"""', content, re.DOTALL)
    if not match:
        print("ERROR: Cannot find ANALYSIS_SYSTEM_PROMPT in processor.py")
        sys.exit(1)

    return match.group(1).strip()


def call_ollama(model: str, prompt: str, system: str) -> dict:
    """Call Ollama API and return response with timing info."""
    payload = {
        "model": model,
        "prompt": prompt,
        "system": system,
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": 2048},
    }

    start = time.time()
    try:
        r = requests.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=180)
        elapsed = time.time() - start
        data = r.json()

        eval_count = data.get("eval_count", 0)
        eval_duration = data.get("eval_duration", 0)
        prompt_eval_duration = data.get("prompt_eval_duration", 0)
        tok_per_sec = eval_count / (eval_duration / 1e9) if eval_duration > 0 else 0

        return {
            "response": data.get("response", ""),
            "wall_time_s": round(elapsed, 1),
            "tokens_generated": eval_count,
            "tok_per_sec": round(tok_per_sec, 1),
            "prompt_eval_ms": round(prompt_eval_duration / 1e6),
            "eval_ms": round(eval_duration / 1e6),
            "error": None,
        }
    except Exception as e:
        return {
            "response": "",
            "wall_time_s": round(time.time() - start, 1),
            "tokens_generated": 0,
            "tok_per_sec": 0,
            "prompt_eval_ms": 0,
            "eval_ms": 0,
            "error": str(e),
        }


def parse_classification(response_text: str) -> dict:
    """Extract relevant/priority from model response JSON."""
    text = response_text
    if "</think>" in text:
        text = text.split("</think>", 1)[-1]

    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start:end])
            relevant = data.get("relevant", False)
            priority = data.get("priority")
            if not relevant or priority is None or priority == "null":
                priority = "none"
            aks = data.get("assigned_aks", [])
            return {
                "relevant": relevant,
                "priority": priority,
                "aks": aks,
                "parse_ok": True,
            }
        except json.JSONDecodeError:
            pass

    return {"relevant": None, "priority": None, "aks": [], "parse_ok": False}


# ============================================================================
# Metrics
# ============================================================================

PRIORITY_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3}


def compute_metrics(results: list[dict]) -> dict:
    """Compute evaluation metrics from results."""
    total = len(results)
    if total == 0:
        return {}

    # Relevance metrics
    correct_rel = sum(1 for r in results if r["correct_relevance"])
    tp = sum(1 for r in results if r["expected_relevant"] and r["got_relevant"])
    tn = sum(1 for r in results if not r["expected_relevant"] and not r["got_relevant"])
    fp = sum(1 for r in results if not r["expected_relevant"] and r["got_relevant"])
    fn = sum(1 for r in results if r["expected_relevant"] and not r["got_relevant"])

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

    # Priority accuracy (on items where both agree it's relevant)
    priority_items = [r for r in results if r["expected_relevant"] and r["got_relevant"]]
    priority_correct = sum(1 for r in priority_items if r.get("correct_priority"))
    priority_acc = priority_correct / len(priority_items) if priority_items else 0

    # Priority within-1-level (high→medium OK, high→none bad)
    priority_within_one = 0.0
    if priority_items:
        within = sum(
            1 for r in priority_items
            if abs(PRIORITY_ORDER.get(r["expected_priority"], 1)
                   - PRIORITY_ORDER.get(r["got_priority"], 1)) <= 1
        )
        priority_within_one = within / len(priority_items)

    # AK accuracy (on items where both agree it's relevant and ground truth has AKs)
    ak_items = [r for r in results
                if r["expected_relevant"] and r["got_relevant"]
                and r.get("expected_aks")]
    ak_correct = 0
    ak_partial = 0
    if ak_items:
        for r in ak_items:
            expected_set = set(r["expected_aks"])
            got_set = set(r.get("got_aks", []))
            if expected_set == got_set:
                ak_correct += 1
            elif expected_set & got_set:  # at least one overlap
                ak_partial += 1
    ak_exact = ak_correct / len(ak_items) if ak_items else 0
    ak_overlap = (ak_correct + ak_partial) / len(ak_items) if ak_items else 0

    # Parse success rate
    parse_ok = sum(1 for r in results if r["parse_ok"])

    # Per-category breakdown
    categories = {}
    for r in results:
        cat = r["category"]
        if cat not in categories:
            categories[cat] = {"total": 0, "correct": 0}
        categories[cat]["total"] += 1
        if r["correct_relevance"]:
            categories[cat]["correct"] += 1

    # FP/FN by subcategory
    fp_by_subcategory = {}
    fn_by_subcategory = {}
    for r in results:
        subcat = r.get("subcategory", "unknown")
        if not r["expected_relevant"] and r["got_relevant"]:  # FP
            fp_by_subcategory[subcat] = fp_by_subcategory.get(subcat, 0) + 1
        elif r["expected_relevant"] and not r["got_relevant"]:  # FN
            fn_by_subcategory[subcat] = fn_by_subcategory.get(subcat, 0) + 1

    # Timing
    times = [r["wall_time_s"] for r in results if r["wall_time_s"] > 0]
    tps = [r["tok_per_sec"] for r in results if r["tok_per_sec"] > 0]

    return {
        "accuracy": round(correct_rel / total, 4),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "priority_accuracy": round(priority_acc, 4),
        "priority_within_one": round(priority_within_one, 4),
        "ak_exact_accuracy": round(ak_exact, 4),
        "ak_overlap_accuracy": round(ak_overlap, 4),
        "tp": tp, "tn": tn, "fp": fp, "fn": fn,
        "fp_by_subcategory": fp_by_subcategory,
        "fn_by_subcategory": fn_by_subcategory,
        "parse_success_rate": round(parse_ok / total, 4),
        "total_items": total,
        "categories": categories,
        "avg_time_s": round(sum(times) / len(times), 1) if times else 0,
        "avg_tok_per_sec": round(sum(tps) / len(tps), 1) if tps else 0,
    }


# ============================================================================
# Main
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="Run LLM eval against fixed eval set")
    parser.add_argument("--model", type=str, default=DEFAULT_MODEL,
                        help=f"Ollama model (default: {DEFAULT_MODEL})")
    parser.add_argument("--prompt-tag", type=str, default=None,
                        help="Label for this eval run (default: auto from git)")
    parser.add_argument("--eval-set", type=str, default=None,
                        help=f"Path to eval set JSON (default: {EVAL_SET_PATH})")
    parser.add_argument("--limit", type=int, default=None,
                        help="Only evaluate first N items (for testing)")
    args = parser.parse_args()

    eval_path = Path(args.eval_set) if args.eval_set else EVAL_SET_PATH

    # Load eval set
    if not eval_path.exists():
        print(f"ERROR: Eval set not found: {eval_path}")
        print("Run curate_eval_set.py first.")
        sys.exit(1)

    eval_data = json.loads(eval_path.read_text())
    items = eval_data["items"]
    eval_version = eval_data.get("version", 1)

    # Filter to items with ground truth
    items = [i for i in items if i.get("ground_truth")]
    if args.limit:
        items = items[:args.limit]

    if not items:
        print("ERROR: No items with ground truth in eval set")
        sys.exit(1)

    # Determine tag
    tag = args.prompt_tag
    if not tag:
        try:
            import subprocess
            result = subprocess.run(["git", "describe", "--tags", "--abbrev=0"],
                                    capture_output=True, text=True, timeout=5)
            tag = result.stdout.strip() if result.returncode == 0 else "unknown"
        except Exception:
            tag = "unknown"

    model = args.model
    system_prompt = get_system_prompt()

    print("=" * 80)
    print("LLM EVALUATION")
    print("=" * 80)
    print(f"  Model: {model}")
    print(f"  Tag: {tag}")
    print(f"  Eval set: {eval_path} (v{eval_version})")
    print(f"  Items: {len(items)} (with ground truth)")
    print(f"  Ollama: {OLLAMA_URL}")

    # Run evaluation
    results = []
    print(f"\n{'#':>3} {'ID':>6} {'Title':40.40} {'Expected':>8} {'Got':>8} {'Match':>5} {'Time':>6}")
    print("-" * 90)

    for idx, item in enumerate(items, 1):
        gt = item["ground_truth"]
        expected_relevant = gt["relevant"]
        expected_priority = gt.get("priority") or "none"
        expected_aks = gt.get("aks", [])

        # Format prompt like production
        content = item.get("content", "")[:2000]
        user_prompt = f"Analysiere diesen Nachrichtenartikel:\n\nTitel: {item['title']}\n\nInhalt: {content}"

        # Call Ollama
        r = call_ollama(model, user_prompt, system_prompt)
        cls = parse_classification(r["response"])

        got_relevant = cls["relevant"] if cls["relevant"] is not None else False
        got_priority = cls["priority"] if cls["priority"] else "none"

        correct_rel = (expected_relevant == got_relevant)
        correct_pri = (expected_priority == got_priority) if expected_relevant and got_relevant else None

        result = {
            "id": item["id"],
            "title": item["title"][:60],
            "category": item["category"],
            "subcategory": item.get("subcategory", ""),
            "expected_relevant": expected_relevant,
            "expected_priority": expected_priority,
            "expected_aks": expected_aks,
            "got_relevant": got_relevant,
            "got_priority": got_priority,
            "got_aks": cls.get("aks", []),
            "correct_relevance": correct_rel,
            "correct_priority": correct_pri,
            "parse_ok": cls["parse_ok"],
            "wall_time_s": r["wall_time_s"],
            "tok_per_sec": r["tok_per_sec"],
            "tokens": r["tokens_generated"],
            "error": r["error"],
        }
        results.append(result)

        exp_str = expected_priority if expected_relevant else "none"
        got_str = got_priority if got_relevant else "none"
        mark = "OK" if correct_rel else "MISS"
        print(f"{idx:>3} {item['id']:>6} {item['title']:40.40} {exp_str:>8} {got_str:>8} {mark:>5} {r['wall_time_s']:5.1f}s")

    # Compute metrics
    metrics = compute_metrics(results)

    # Print summary
    print(f"\n{'=' * 80}")
    print("SUMMARY")
    print(f"{'=' * 80}")
    print(f"  Accuracy:     {metrics['accuracy']:.1%}")
    print(f"  Precision:    {metrics['precision']:.1%}")
    print(f"  Recall:       {metrics['recall']:.1%}")
    print(f"  F1:           {metrics['f1']:.1%}")
    print(f"  Priority:     {metrics['priority_accuracy']:.1%} (exact)  {metrics['priority_within_one']:.1%} (within-1)")
    print(f"  AK:           {metrics['ak_exact_accuracy']:.1%} (exact)  {metrics['ak_overlap_accuracy']:.1%} (overlap)")
    print(f"  TP: {metrics['tp']}  TN: {metrics['tn']}  FP: {metrics['fp']}  FN: {metrics['fn']}")
    print(f"  Parse OK:     {metrics['parse_success_rate']:.0%}")
    print(f"  Avg time:     {metrics['avg_time_s']}s")
    print(f"  Avg tok/s:    {metrics['avg_tok_per_sec']}")

    print(f"\n  By category:")
    for cat, vals in sorted(metrics["categories"].items()):
        pct = vals["correct"] / vals["total"] * 100 if vals["total"] > 0 else 0
        print(f"    {cat:15s}: {vals['correct']}/{vals['total']} ({pct:.0f}%)")

    # FP/FN by subcategory
    if metrics.get("fp_by_subcategory"):
        print(f"\n  False positives by type:")
        for subcat, count in sorted(metrics["fp_by_subcategory"].items(), key=lambda x: -x[1]):
            print(f"    {subcat:20s}: {count}")

    if metrics.get("fn_by_subcategory"):
        print(f"\n  False negatives by type:")
        for subcat, count in sorted(metrics["fn_by_subcategory"].items(), key=lambda x: -x[1]):
            print(f"    {subcat:20s}: {count}")

    # Show mismatches
    misses = [r for r in results if not r["correct_relevance"]]
    if misses:
        print(f"\n  Mismatches ({len(misses)}):")
        for r in misses:
            exp = "REL" if r["expected_relevant"] else "IRR"
            got = "REL" if r["got_relevant"] else "IRR"
            print(f"    {r['id']:>6}: expected={exp}/{r['expected_priority']}, got={got}/{r['got_priority']} [{r.get('subcategory', '')}] — {r['title']}")

    # Save results
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    model_short = model.replace(":", "-").replace("/", "-")
    result_file = RESULTS_DIR / f"llm_{date_str}_{tag}_{model_short}.json"

    output = {
        "type": "llm",
        "timestamp": datetime.now().isoformat(),
        "model": model,
        "prompt_tag": tag,
        "ollama_url": OLLAMA_URL,
        "eval_set_version": eval_version,
        "eval_set_path": str(eval_path),
        "metrics": metrics,
        "results": results,
    }

    result_file.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    print(f"\nResults saved: {result_file}")


if __name__ == "__main__":
    main()
