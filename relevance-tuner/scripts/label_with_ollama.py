#!/usr/bin/env python3
"""
Batch labeling script using local Ollama LLM.

Usage:
    python scripts/label_with_ollama.py --batch 0
    python scripts/label_with_ollama.py --all
    python scripts/label_with_ollama.py --all --model qwen3:14b-q8_0
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Optional
import requests

# Configuration
OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen3:14b-q8_0"
BATCH_SIZE = 10  # Items per LLM call (smaller for better accuracy)

# Paths
PROJECT_ROOT = Path(__file__).parent.parent
PROMPT_FILE = PROJECT_ROOT / "LABELING_PROMPT.md"
BATCHES_DIR = PROJECT_ROOT / "data" / "raw" / "batches"
OUTPUT_DIR = PROJECT_ROOT / "data" / "reviewed" / "ollama_results"


class ProgressTracker:
    """Track and display progress across all batches."""

    def __init__(self, total_items: int, total_batches: int):
        self.total_items = total_items
        self.total_batches = total_batches
        self.items_done = 0
        self.batches_done = 0
        self.relevant_count = 0
        self.start_time = time.time()
        self.last_print_time = 0

    def update(self, items: int, relevant: int):
        """Update progress after processing items."""
        self.items_done += items
        self.relevant_count += relevant

    def batch_complete(self):
        """Mark a batch as complete."""
        self.batches_done += 1

    def get_eta(self) -> str:
        """Calculate estimated time remaining."""
        elapsed = time.time() - self.start_time
        if self.items_done == 0:
            return "calculating..."

        items_per_sec = self.items_done / elapsed
        remaining_items = self.total_items - self.items_done
        remaining_sec = remaining_items / items_per_sec

        if remaining_sec < 60:
            return f"{remaining_sec:.0f}s"
        elif remaining_sec < 3600:
            return f"{remaining_sec/60:.1f}min"
        else:
            return f"{remaining_sec/3600:.1f}h"

    def get_speed(self) -> str:
        """Get current processing speed."""
        elapsed = time.time() - self.start_time
        if elapsed == 0:
            return "..."
        items_per_min = self.items_done / (elapsed / 60)
        return f"{items_per_min:.1f}/min"

    def get_progress_bar(self, width: int = 30) -> str:
        """Generate a progress bar."""
        if self.total_items == 0:
            return "[" + " " * width + "]"

        pct = self.items_done / self.total_items
        filled = int(width * pct)
        bar = "█" * filled + "░" * (width - filled)
        return f"[{bar}]"

    def print_status(self, force: bool = False):
        """Print current status line."""
        now = time.time()
        # Throttle updates to every 0.5s unless forced
        if not force and (now - self.last_print_time) < 0.5:
            return
        self.last_print_time = now

        pct = (self.items_done / self.total_items * 100) if self.total_items > 0 else 0
        elapsed = time.time() - self.start_time

        status = (
            f"\r{self.get_progress_bar()} "
            f"{pct:5.1f}% | "
            f"{self.items_done}/{self.total_items} items | "
            f"Batch {self.batches_done}/{self.total_batches} | "
            f"{self.relevant_count} relevant | "
            f"{self.get_speed()} | "
            f"ETA: {self.get_eta()} | "
            f"Elapsed: {elapsed:.0f}s"
        )
        print(status, end="", flush=True)

    def print_final(self):
        """Print final summary."""
        elapsed = time.time() - self.start_time
        print()  # New line after progress bar
        print("=" * 70)
        print(f"COMPLETE: {self.items_done} items in {elapsed:.1f}s")
        if self.items_done > 0:
            items_per_min = self.items_done / (elapsed / 60)
            sec_per_item = elapsed / self.items_done
            relevant_pct = self.relevant_count / self.items_done * 100
            print(f"Speed: {items_per_min:.1f} items/min ({sec_per_item:.2f}s per item)")
            print(f"Found: {self.relevant_count} relevant ({relevant_pct:.1f}%)")
        print(f"Output: {OUTPUT_DIR}")


def load_prompt() -> str:
    """Load the labeling prompt from LABELING_PROMPT.md"""
    with open(PROMPT_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    # Extract the system prompt between ```
    start = content.find("```\n", content.find("## System Prompt"))
    end = content.find("```", start + 4)

    if start == -1 or end == -1:
        raise ValueError("Could not find system prompt in LABELING_PROMPT.md")

    return content[start + 4:end].strip()


def load_batch(batch_num: int) -> list[dict]:
    """Load items from a batch file."""
    batch_file = BATCHES_DIR / f"batch_{batch_num:02d}.jsonl"

    if not batch_file.exists():
        raise FileNotFoundError(f"Batch file not found: {batch_file}")

    items = []
    with open(batch_file, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                items.append(json.loads(line))

    return items


def count_batch_items(batch_num: int) -> int:
    """Count items in a batch without loading full content."""
    batch_file = BATCHES_DIR / f"batch_{batch_num:02d}.jsonl"
    if not batch_file.exists():
        return 0
    with open(batch_file, "r", encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())


def format_items_for_labeling(items: list[dict]) -> str:
    """Format items as text for the labeling agent.

    Note: Full content is passed to the LLM (no truncation) so that
    summary and detailed_analysis can capture all relevant information.
    """
    lines = []
    for i, item in enumerate(items, 1):
        inp = item.get("input", {})
        lines.append(f"=== ARTIKEL {i} ===")
        lines.append(f"Titel: {inp.get('title', 'N/A')}")
        lines.append(f"Quelle: {inp.get('source', 'N/A')}")
        lines.append(f"Datum: {inp.get('date', 'N/A')}")
        content = inp.get('content', 'N/A')  # Full content - no truncation
        lines.append(f"Inhalt: {content}")
        lines.append("")

    return "\n".join(lines)


def create_labeling_prompt(system_prompt: str, items_text: str, num_items: int) -> str:
    """Create the full prompt for labeling."""
    return f"""{system_prompt}

=== AUFGABE ===

Analysiere die folgenden {num_items} Artikel. Gib für JEDEN Artikel genau EINE JSON-Zeile aus.

{items_text}

=== AUSGABE ===

Gib für JEDEN der {num_items} Artikel genau EINE JSON-Zeile aus. Format:
{{"title": "...", "relevant": true/false, "ak": "AK1"|...|null, "priority": "critical"|...|null, "summary": "...", "detailed_analysis": "...", "argumentationskette": [...], "reasoning": "..."}}

WICHTIG:
- Genau {num_items} JSON-Zeilen
- Keine zusätzlichen Erklärungen
- Bei relevant=false: ak=null, priority=null, summary=null, detailed_analysis=null, argumentationskette=null
- Bei relevant=true: ALLE Felder MÜSSEN gesetzt sein
- summary: Reine Fakten (bis zu 8 Sätze)
- detailed_analysis: Fakten + Zitate + Auswirkungen (bis zu 15 Sätze) - KEINE Liga-Spekulation!
- argumentationskette: 2-6 konkrete Argumente für Liga (direkt verwendbar, keine Konjunktive)
- Keine "..." am Ende

Beginne jetzt mit der Analyse (nur JSON-Zeilen, keine Erklärungen):"""


def call_ollama(prompt: str, model: str) -> str:
    """Call Ollama API and return response."""
    url = f"{OLLAMA_URL}/api/generate"

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.3,  # Lower for consistency
            "num_predict": 8192,  # Increased for longer summary/detailed_analysis output
        }
    }

    try:
        response = requests.post(url, json=payload, timeout=300)
        response.raise_for_status()
        return response.json()["response"]
    except requests.exceptions.RequestException as e:
        print(f"\nError calling Ollama: {e}")
        raise


def parse_json_lines(response: str) -> list[dict]:
    """Parse JSON lines from LLM response, handling various formats."""
    results = []

    # Try to find JSON objects in the response
    # Handle both clean JSON lines and markdown code blocks
    response = response.strip()

    # Remove markdown code blocks if present
    response = re.sub(r'^```json?\s*', '', response, flags=re.MULTILINE)
    response = re.sub(r'^```\s*$', '', response, flags=re.MULTILINE)

    # Try line by line
    for line in response.split('\n'):
        line = line.strip()
        if not line or line.startswith('#') or line.startswith('//'):
            continue

        # Try to parse as JSON
        try:
            obj = json.loads(line)
            if isinstance(obj, dict) and 'title' in obj:
                results.append(obj)
        except json.JSONDecodeError:
            # Try to extract JSON from the line
            match = re.search(r'\{[^{}]+\}', line)
            if match:
                try:
                    obj = json.loads(match.group())
                    if isinstance(obj, dict) and 'title' in obj:
                        results.append(obj)
                except json.JSONDecodeError:
                    continue

    return results


def label_items(items: list[dict], system_prompt: str, model: str, progress: ProgressTracker) -> list[dict]:
    """Label a list of items using Ollama."""
    items_text = format_items_for_labeling(items)
    prompt = create_labeling_prompt(system_prompt, items_text, len(items))

    response = call_ollama(prompt, model)

    # Parse response
    results = parse_json_lines(response)

    if len(results) != len(items):
        # If we got fewer results, pad with None
        while len(results) < len(items):
            results.append(None)

    return results


def merge_labels(original: dict, labels: Optional[dict]) -> dict:
    """Merge LLM labels back into original item structure."""
    result = original.copy()

    if labels is None:
        # Mark as failed
        result["labels"] = {
            "relevant": None,
            "priority": None,
            "ak": None,
            "reaction_type": None
        }
        result["output"] = {
            "summary": None,
            "detailed_analysis": None,
            "argumentationskette": None
        }
        result["provenance"]["reasoning"] = "LABELING_FAILED"
        return result

    result["labels"] = {
        "relevant": labels.get("relevant"),
        "priority": labels.get("priority"),
        "ak": labels.get("ak"),
        "reaction_type": None
    }
    # Store summary, detailed_analysis and argumentationskette in output field
    result["output"] = {
        "summary": labels.get("summary"),
        "detailed_analysis": labels.get("detailed_analysis"),
        "argumentationskette": labels.get("argumentationskette")
    }
    result["provenance"]["reasoning"] = labels.get("reasoning", "")

    return result


def process_batch(batch_num: int, system_prompt: str, model: str,
                  progress: ProgressTracker, dry_run: bool = False) -> tuple[int, int]:
    """Process a single batch file. Returns (items_count, relevant_count)."""
    items = load_batch(batch_num)
    output_file = OUTPUT_DIR / f"batch_{batch_num:02d}_labeled.jsonl"

    if dry_run:
        return 0, 0

    all_results = []
    batch_relevant = 0

    # Process in smaller chunks for better accuracy
    for i in range(0, len(items), BATCH_SIZE):
        chunk = items[i:i + BATCH_SIZE]

        labels = label_items(chunk, system_prompt, model, progress)

        chunk_relevant = 0
        for item, label in zip(chunk, labels):
            merged = merge_labels(item, label)
            all_results.append(merged)
            if merged["labels"]["relevant"] is True:
                chunk_relevant += 1

        batch_relevant += chunk_relevant
        progress.update(len(chunk), chunk_relevant)
        progress.print_status()

    # Write results
    with open(output_file, 'w', encoding='utf-8') as f:
        for result in all_results:
            f.write(json.dumps(result, ensure_ascii=False) + '\n')

    progress.batch_complete()
    progress.print_status(force=True)

    return len(items), batch_relevant


def get_batch_count() -> int:
    """Count available batches."""
    return len(list(BATCHES_DIR.glob("batch_*.jsonl")))


def check_ollama_available(model: str) -> bool:
    """Check if Ollama is available and model is loaded."""
    try:
        response = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        response.raise_for_status()
        models = [m["name"] for m in response.json().get("models", [])]

        if model not in models:
            print(f"Model {model} not found. Available: {models}")
            return False
        return True
    except requests.exceptions.RequestException as e:
        print(f"Ollama not available: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Label batches using local Ollama LLM")
    parser.add_argument("--batch", type=int, help="Batch number to process (0-indexed)")
    parser.add_argument("--all", action="store_true", help="Process all batches")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Ollama model to use (default: {DEFAULT_MODEL})")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without executing")
    parser.add_argument("--resume", action="store_true", help="Skip batches that already have output")
    args = parser.parse_args()

    if args.batch is None and not args.all:
        parser.error("Either --batch N or --all must be specified")

    # Check Ollama
    print(f"Checking Ollama ({OLLAMA_URL})...")
    if not check_ollama_available(args.model):
        sys.exit(1)
    print(f"Model: {args.model}")

    # Ensure output directory exists
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Load prompt
    system_prompt = load_prompt()
    print(f"Prompt: {len(system_prompt)} chars")

    # Determine batches to process
    if args.all:
        batch_nums = list(range(get_batch_count()))
    else:
        batch_nums = [args.batch]

    # Filter if resuming
    if args.resume:
        existing = {int(f.stem.split('_')[1]) for f in OUTPUT_DIR.glob("batch_*_labeled.jsonl")}
        batch_nums = [b for b in batch_nums if b not in existing]
        if existing:
            print(f"Resume: skipping {len(existing)} done batches")

    # Count total items
    total_items = sum(count_batch_items(b) for b in batch_nums)
    print(f"Processing: {len(batch_nums)} batches, {total_items} items")
    print("=" * 70)

    # Initialize progress tracker
    progress = ProgressTracker(total_items, len(batch_nums))

    for batch_num in batch_nums:
        process_batch(batch_num, system_prompt, args.model, progress, args.dry_run)

    progress.print_final()


if __name__ == "__main__":
    main()
