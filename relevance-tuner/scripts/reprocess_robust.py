#!/usr/bin/env python3
"""Robust sequential reprocessing of items through the prod API.

Sends one reprocess request at a time, waits for actual LLM analysis
(not error defaults), retries on failure. Can be stopped and resumed.

Usage:
    python reprocess_robust.py <id_file> [--offset N] [--max-retries N] [--timeout N]

    # Resume from where we left off (reads progress file)
    python reprocess_robust.py <id_file> --resume
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

PROGRESS_FILE = Path("/tmp/reprocess_progress.json")
RESULTS_FILE = Path("/tmp/reprocess_results.jsonl")
API_HOST = "docker-ai"
POLL_INTERVAL = 3  # seconds between status checks
ERROR_MARKER = "Automatische Analyse nicht verf"


def api_call(path: str, method: str = "GET", timeout: int = 30) -> dict | None:
    """Call the prod API via SSH to docker-ai."""
    url = f"http://localhost:8000{path}"
    if method == "POST":
        cmd = f'curl -sf -X POST "{url}"'
    else:
        cmd = f'curl -sf "{url}"'

    try:
        result = subprocess.run(
            ["ssh", "-n", API_HOST, cmd],
            capture_output=True, text=True, timeout=timeout
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception):
        pass
    return None


def check_item_status(item_id: int) -> tuple[bool, str, float]:
    """Check if item has real LLM analysis. Returns (has_analysis, priority, score)."""
    data = api_call(f"/api/items/{item_id}")
    if not data:
        return False, "unknown", 0.0

    llm = data.get("metadata", {}).get("llm_analysis", {})
    reasoning = llm.get("reasoning") or ""
    score = llm.get("relevance_score", 0.0)
    priority = data.get("priority", "none")

    if ERROR_MARKER in reasoning:
        return False, priority, score
    if reasoning and len(reasoning) > 20:
        return True, priority, score
    return False, priority, score


def reprocess_item(item_id: int, max_wait: int = 90, max_retries: int = 3) -> dict:
    """Reprocess a single item with retries. Returns result dict."""
    for attempt in range(1, max_retries + 1):
        # Fire reprocess
        resp = api_call(f"/api/items/{item_id}/reprocess", method="POST")
        if not resp or resp.get("status") != "started":
            return {"id": item_id, "status": "api_error", "attempt": attempt}

        # Wait for LLM to process
        waited = 0
        while waited < max_wait:
            time.sleep(POLL_INTERVAL)
            waited += POLL_INTERVAL

            has_analysis, priority, score = check_item_status(item_id)
            if has_analysis:
                return {
                    "id": item_id, "status": "ok",
                    "priority": priority, "score": score,
                    "attempt": attempt, "wait": waited
                }

        # Timed out — retry
        if attempt < max_retries:
            time.sleep(5)  # Brief pause before retry

    return {"id": item_id, "status": "timeout", "attempt": max_retries}


def save_progress(offset: int, stats: dict):
    """Save progress to file for resume."""
    PROGRESS_FILE.write_text(json.dumps({"offset": offset, "stats": stats}))


def load_progress() -> tuple[int, dict]:
    """Load progress from file."""
    if PROGRESS_FILE.exists():
        data = json.loads(PROGRESS_FILE.read_text())
        return data["offset"], data["stats"]
    return 0, {"ok": 0, "timeout": 0, "api_error": 0}


def main():
    parser = argparse.ArgumentParser(description="Robust item reprocessing")
    parser.add_argument("id_file", help="File with item IDs, one per line")
    parser.add_argument("--offset", type=int, default=0, help="Start offset")
    parser.add_argument("--resume", action="store_true", help="Resume from progress file")
    parser.add_argument("--max-retries", type=int, default=3, help="Max retries per item")
    parser.add_argument("--timeout", type=int, default=90, help="Max wait per attempt (seconds)")
    args = parser.parse_args()

    # Load IDs
    ids = [int(line.strip()) for line in open(args.id_file) if line.strip()]
    total = len(ids)

    # Determine start offset
    if args.resume:
        offset, stats = load_progress()
        print(f"Resuming from offset {offset} (previous: {stats})")
    else:
        offset = args.offset
        stats = {"ok": 0, "timeout": 0, "api_error": 0}

    print(f"=== Robust Reprocessing ===")
    print(f"Total: {total}, Starting from: {offset}")
    print(f"Max retries: {args.max_retries}, Timeout: {args.timeout}s per attempt")
    print(f"Press Ctrl+C to stop (progress is saved)\n")

    start_time = time.time()

    try:
        for i in range(offset, total):
            item_id = ids[i]
            elapsed = time.time() - start_time
            elapsed_min = int(elapsed / 60)

            done = stats["ok"] + stats["timeout"] + stats["api_error"]
            if done > 0:
                rate = elapsed / done
                eta_min = int((total - i) * rate / 60)
                eta_str = f"ETA {eta_min}m"
            else:
                eta_str = "ETA ?m"

            sys.stdout.write(f"\r[{i+1}/{total}] Item {item_id} ({elapsed_min}m, {eta_str})... ")
            sys.stdout.flush()

            result = reprocess_item(item_id, max_wait=args.timeout, max_retries=args.max_retries)
            status = result["status"]
            stats[status] = stats.get(status, 0) + 1

            if status == "ok":
                sys.stdout.write(f"OK ({result['priority']}, {result['score']}, {result['wait']}s)\n")
            else:
                sys.stdout.write(f"{status.upper()} (attempt {result['attempt']})\n")

            # Save result
            with open(RESULTS_FILE, "a") as f:
                f.write(json.dumps(result) + "\n")

            # Save progress every 10 items
            if (i + 1) % 10 == 0:
                save_progress(i + 1, stats)

    except KeyboardInterrupt:
        print(f"\n\nInterrupted at item {i+1}/{total}")
        save_progress(i, stats)
        print(f"Resume with: python {sys.argv[0]} {args.id_file} --resume")

    total_min = int((time.time() - start_time) / 60)
    save_progress(i + 1 if i + 1 <= total else total, stats)

    print(f"\n=== Done ===")
    print(f"OK: {stats['ok']}, Timeout: {stats['timeout']}, API Error: {stats['api_error']}")
    print(f"Time: {total_min}m")

    if stats["timeout"] > 0:
        print(f"\n{stats['timeout']} items timed out. Re-run with --resume to retry.")


if __name__ == "__main__":
    main()
