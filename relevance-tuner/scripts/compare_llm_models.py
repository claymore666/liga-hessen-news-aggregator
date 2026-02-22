#!/usr/bin/env python3
"""
Compare LLM models on news classification quality and speed.

Reproducible benchmark: uses a fixed test set of 20 items with known
ground-truth classifications (haiku-verified). Runs each item through
both models with the same system prompt and compares results.

Usage:
    # Default: qwen3:14b-q8_0 vs glm-4.7-flash-tools
    python scripts/compare_llm_models.py

    # Custom models:
    python scripts/compare_llm_models.py --models qwen3:14b-q8_0 glm-4.7-flash-tools mistral-small3.1

    # Custom Ollama URL:
    OLLAMA_URL=http://localhost:11434 python scripts/compare_llm_models.py

    # Save detailed results:
    python scripts/compare_llm_models.py --output results.json

    # Use fixed eval set instead of hardcoded TEST_SET:
    python scripts/compare_llm_models.py --eval-set

For proper reproducible evaluations against the fixed eval set, use
run_llm_eval.py instead — it computes full metrics and saves results
to evaluations/results/ for cross-run comparison.

The system prompt is read from the production processor.py to ensure
we test exactly what runs in production.
"""

import argparse
import json
import os
import re
import sys
import time
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
DEFAULT_MODELS = ["qwen3:14b-q8_0", "glm-4.7-flash-tools"]

# Fixed test set: 20 items with haiku-verified ground truth
# Categories: 7 irrelevant, 5 relevant, 6 edge cases, 2 additional relevant
# Ground truth established 2026-02-21 via Claude haiku verification
TEST_SET = [
    # === CLEARLY IRRELEVANT (haiku confirmed) ===
    {"id": 26123, "expected": "none", "category": "irrelevant",
     "note": "Venezuela Hungerstreik — international, no DE social policy"},
    {"id": 26786, "expected": "none", "category": "irrelevant",
     "note": "Elefantenkuh Opel-Zoo — entertainment"},
    {"id": 26729, "expected": "none", "category": "irrelevant",
     "note": "Nahtoderfahrungen Studie — medical curiosity"},
    {"id": 26503, "expected": "none", "category": "irrelevant",
     "note": "Intervallfasten — lifestyle/consumer"},
    {"id": 26246, "expected": "none", "category": "irrelevant",
     "note": "FC Bayern Neuer verletzt — sports"},
    {"id": 26848, "expected": "none", "category": "irrelevant",
     "note": "Frederick Wiseman Nachruf — entertainment"},
    {"id": 26795, "expected": "none", "category": "irrelevant",
     "note": "Wohnhausbrand Malsfeld — individual incident"},

    # === CLEARLY RELEVANT (haiku confirmed) ===
    {"id": 26105, "expected": "medium", "category": "relevant",
     "note": "Sozialabgaben 50% bis 2035 — IGES Studie stärkt Liga-Position"},
    {"id": 26539, "expected": "low", "category": "relevant",
     "note": "AWO Vermögensteuer — Liga member policy statement"},
    {"id": 27028, "expected": "medium", "category": "relevant",
     "note": "Hessen Pflegeleistungen dynamisieren — Hessen minister policy"},
    {"id": 27030, "expected": "medium", "category": "relevant",
     "note": "SPD-Anfrage Pflegegeräte Wiederverwendung — structural care issue"},
    {"id": 27145, "expected": "medium", "category": "relevant",
     "note": "CDU Rente mit 70 — pension reform debate"},

    # === EDGE CASES (haiku flagged as misclassified by v3) ===
    {"id": 26600, "expected": "medium", "category": "edge-fn",
     "note": "230 Mio Hessengeld — Hessen budget/Förderprogramm, should be relevant"},
    {"id": 26433, "expected": "medium", "category": "edge-fn",
     "note": "BAMF streicht Integrationskurs-Förderung — federal policy cut"},
    {"id": 26560, "expected": "low", "category": "edge-fn",
     "note": "Sozialwohnbau Kostenüberschreitung — housing structural issue"},
    {"id": 26753, "expected": "none", "category": "edge-fp",
     "note": "Brandenburg Pflege-Umfrage — other state, no federal connection"},
    {"id": 26410, "expected": "none", "category": "edge-fp",
     "note": "Brandenburg Sparkurs — other state internal politics"},
    {"id": 26588, "expected": "none", "category": "edge-fp",
     "note": "SPD Social-Media-Verbot Debatte — party rhetoric, no concrete law"},

    # === ADDITIONAL RELEVANT ===
    {"id": 26903, "expected": "none", "category": "relevant-boundary",
     "note": "Pflegeversicherung Brandenburg Umfrage — other state, borderline"},
    {"id": 26718, "expected": "low", "category": "relevant",
     "note": "Hessen Sozialwohnungen Förderung — Hessen housing policy"},
]


def get_system_prompt():
    """Read the production system prompt from processor.py."""
    processor_path = Path(__file__).parent.parent.parent / "news-aggregator" / "backend" / "services" / "processor.py"
    if not processor_path.exists():
        print(f"ERROR: Cannot find {processor_path}")
        sys.exit(1)

    content = processor_path.read_text()
    # Extract the ANALYSIS_SYSTEM_PROMPT string
    match = re.search(r'ANALYSIS_SYSTEM_PROMPT = """(.*?)"""', content, re.DOTALL)
    if not match:
        print("ERROR: Cannot find ANALYSIS_SYSTEM_PROMPT in processor.py")
        sys.exit(1)

    return match.group(1).strip()


def fetch_item(item_id: int) -> dict | None:
    """Fetch item from the news-aggregator API."""
    api_url = os.environ.get("API_URL", "http://localhost:8000/api")
    try:
        r = requests.get(f"{api_url}/items/{item_id}", timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"  WARNING: Cannot fetch item {item_id}: {e}")
    return None


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
    # Strip thinking tags (qwen3 uses <think>...</think>)
    if "</think>" in text:
        text = text.split("</think>", 1)[-1]

    # Find JSON block
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start:end])
            relevant = data.get("relevant", False)
            priority = data.get("priority")
            # Normalize: if not relevant, priority is "none"
            if not relevant or priority is None or priority == "null":
                priority = "none"
            return {"relevant": relevant, "priority": priority, "parse_ok": True}
        except json.JSONDecodeError:
            pass

    return {"relevant": None, "priority": None, "parse_ok": False}


def main():
    parser = argparse.ArgumentParser(description="Compare LLM models on news classification")
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS,
                        help="Models to compare (default: %(default)s)")
    parser.add_argument("--output", "-o", type=str, default=None,
                        help="Save detailed results to JSON file")
    parser.add_argument("--items", nargs="+", type=int, default=None,
                        help="Only test specific item IDs")
    parser.add_argument("--eval-set", action="store_true",
                        help="Use evaluations/eval_set.json instead of hardcoded TEST_SET")
    args = parser.parse_args()

    models = args.models
    system_prompt = get_system_prompt()

    print("=" * 100)
    print(f"LLM Model Comparison: {' vs '.join(models)}")
    print(f"System prompt: v4 (from processor.py)")
    print(f"Ollama URL: {OLLAMA_URL}")
    print(f"Temperature: 0.3")
    print("=" * 100)

    # Load test items from eval set or hardcoded TEST_SET
    if args.eval_set:
        eval_set_path = Path(__file__).parent.parent / "evaluations" / "eval_set.json"
        if not eval_set_path.exists():
            print(f"ERROR: Eval set not found: {eval_set_path}")
            print("Run curate_eval_set.py first.")
            sys.exit(1)
        eval_data = json.loads(eval_set_path.read_text())
        test_items = []
        for item in eval_data["items"]:
            if not item.get("ground_truth"):
                continue
            gt = item["ground_truth"]
            expected = gt.get("priority") or "none" if gt["relevant"] else "none"
            test_items.append({
                "id": item["id"],
                "expected": expected,
                "category": item["category"],
                "note": item.get("note") or item.get("title", "")[:60],
                "_content": item.get("content"),
                "_title": item.get("title"),
            })
        print(f"Loaded {len(test_items)} items from eval set")
    else:
        test_items = TEST_SET

    # Filter test set if specific items requested
    if args.items:
        test_items = [t for t in test_items if t["id"] in args.items]

    # Fetch items from API (or use embedded content from eval set)
    print(f"\nFetching {len(test_items)} test items...")
    items_data = {}
    for t in test_items:
        if t.get("_content") and t.get("_title"):
            # Content embedded from eval set — no API needed
            items_data[t["id"]] = {"title": t["_title"], "content": t["_content"]}
            print(f"  {t['id']}: {t['_title'][:60]} (from eval set)")
        else:
            item = fetch_item(t["id"])
            if item:
                items_data[t["id"]] = item
                print(f"  {t['id']}: {item.get('title', '???')[:60]}")
            else:
                print(f"  {t['id']}: MISSING — skipping")

    if not items_data:
        print("ERROR: No items fetched. Is the API running?")
        sys.exit(1)

    # Run comparison
    results = {m: [] for m in models}
    print(f"\n{'ID':>6} | {'Title':40.40} | {'Expected':>8} | {'Model':25.25} | {'Result':>8} | {'Match':>5} | {'Time':>6} | {'tok/s':>6}")
    print("-" * 115)

    for t in test_items:
        if t["id"] not in items_data:
            continue
        item = items_data[t["id"]]
        title = item.get("title", "")
        content = item.get("content", "") or item.get("summary", "") or ""
        user_prompt = f"Analysiere diesen Nachrichtenartikel:\n\nTitel: {title}\n\nInhalt: {content[:2000]}"

        for model in models:
            r = call_ollama(model, user_prompt, system_prompt)
            cls = parse_classification(r["response"])
            priority = cls["priority"] if cls["priority"] else "none"
            match = priority == t["expected"]

            results[model].append({
                "id": t["id"],
                "title": title[:60],
                "expected": t["expected"],
                "got": priority,
                "relevant": cls["relevant"],
                "match": match,
                "parse_ok": cls["parse_ok"],
                "wall_time_s": r["wall_time_s"],
                "tok_per_sec": r["tok_per_sec"],
                "tokens": r["tokens_generated"],
                "prompt_eval_ms": r["prompt_eval_ms"],
                "category": t["category"],
                "note": t["note"],
                "error": r["error"],
            })

            mark = "OK" if match else "MISS"
            model_short = model[:25]
            print(f"{t['id']:>6} | {title:40.40} | {t['expected']:>8} | {model_short:25.25} | {priority:>8} | {mark:>5} | {r['wall_time_s']:5.1f}s | {r['tok_per_sec']:5.1f}")

        print()  # blank line between items

    # === Summary ===
    print("\n" + "=" * 100)
    print("SUMMARY")
    print("=" * 100)

    for model in models:
        mr = results[model]
        if not mr:
            continue

        matches = sum(1 for r in mr if r["match"])
        parse_ok = sum(1 for r in mr if r["parse_ok"])
        total = len(mr)
        times = [r["wall_time_s"] for r in mr]
        tps = [r["tok_per_sec"] for r in mr if r["tok_per_sec"] > 0]

        # Category breakdown
        cats = {}
        for r in mr:
            cat = r["category"]
            if cat not in cats:
                cats[cat] = {"total": 0, "match": 0}
            cats[cat]["total"] += 1
            if r["match"]:
                cats[cat]["match"] += 1

        # False positive / false negative analysis
        fp = sum(1 for r in mr if r["expected"] == "none" and r["got"] != "none")
        fn = sum(1 for r in mr if r["expected"] != "none" and r["got"] == "none")
        tp = sum(1 for r in mr if r["expected"] != "none" and r["got"] != "none")
        tn = sum(1 for r in mr if r["expected"] == "none" and r["got"] == "none")

        print(f"\n{'─' * 50}")
        print(f"  {model}")
        print(f"{'─' * 50}")
        print(f"  Overall accuracy:   {matches}/{total} ({100*matches/total:.0f}%)")
        print(f"  JSON parse success: {parse_ok}/{total} ({100*parse_ok/total:.0f}%)")
        print(f"  TP: {tp}  TN: {tn}  FP: {fp}  FN: {fn}")
        if tp + fp > 0:
            print(f"  Precision: {100*tp/(tp+fp):.0f}%  Recall: {100*tp/(tp+fn):.0f}%")
        print(f"  Avg time:  {sum(times)/len(times):.1f}s")
        print(f"  Min/Max:   {min(times):.1f}s / {max(times):.1f}s")
        if tps:
            print(f"  Avg tok/s: {sum(tps)/len(tps):.1f}")

        print(f"\n  By category:")
        for cat in sorted(cats.keys()):
            c = cats[cat]
            print(f"    {cat:20s}: {c['match']}/{c['total']} correct")

        # Show mismatches
        misses = [r for r in mr if not r["match"]]
        if misses:
            print(f"\n  Mismatches ({len(misses)}):")
            for r in misses:
                print(f"    {r['id']}: expected={r['expected']}, got={r['got']} — {r['note']}")

    # Save results
    if args.output:
        output_path = Path(args.output)
        output_data = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "models": models,
            "ollama_url": OLLAMA_URL,
            "system_prompt_version": "v4",
            "results": results,
        }
        output_path.write_text(json.dumps(output_data, indent=2, ensure_ascii=False))
        print(f"\nDetailed results saved to {output_path}")

    print()


if __name__ == "__main__":
    main()
