#!/usr/bin/env python3
"""
Batch classify items with LLM and compare to sklearn.

Usage:
    python scripts/batch_llm_classify.py --limit 100  # Process 100 items
    python scripts/batch_llm_classify.py              # Process all unprocessed
"""

import argparse
import json
import time
from pathlib import Path

import requests

OLLAMA_URL = "http://localhost:11434"
MODEL = "qwen3:14b-q8_0"

PROMPT_TEMPLATE = """Du bist ein Experte für Sozialpolitik in Hessen. Bewerte, ob dieser Artikel für die Liga der Freien Wohlfahrtspflege Hessen relevant ist.

Die Liga ist der Dachverband von: AWO, Caritas, Diakonie, DRK, Paritätischer, Jüdische Gemeinden.

RELEVANT wenn es um eines dieser Themen geht:
- Sozialpolitik in Hessen (Haushalte, Förderungen, Gesetze)
- Pflege, Gesundheit, Senioren (AK3)
- Migration, Flucht, Integration (AK2)
- Menschen mit Behinderungen, Inklusion (AK4)
- Kinder, Jugend, Kitas, Familie, Frauen (AK5)
- Wohnungslosigkeit, Sucht, Schulden (QAG)
- Wohlfahrtsverbände, Ehrenamt, Sozialpolitik allgemein (AK1)

NICHT RELEVANT:
- Allgemeine Politik ohne Sozialbezug
- Sport, Unterhaltung, Klatsch
- Reine Lokalnachrichten ohne Sozialbezug
- Wirtschaft ohne Sozialbezug

Titel: {title}

Inhalt: {content}

Antworte NUR mit JSON:
{{"relevant": true/false, "ak": "AK1/AK2/AK3/AK4/AK5/QAG oder null", "reasoning": "kurze Begründung"}}"""


def classify_with_llm(title: str, content: str) -> dict:
    """Classify single item with LLM."""
    prompt = PROMPT_TEMPLATE.format(
        title=title[:200],
        content=content[:4000]
    )

    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.3}
            },
            timeout=120
        )
        resp.raise_for_status()
        response_text = resp.json().get("response", "")

        # Parse JSON from response
        start = response_text.find("{")
        end = response_text.rfind("}") + 1
        if start >= 0 and end > start:
            result = json.loads(response_text[start:end])
            return {
                "llm_relevant": result.get("relevant"),
                "llm_ak": result.get("ak"),
                "reasoning": result.get("reasoning", "")
            }
    except Exception as e:
        return {"error": str(e)}

    return {"error": "parse_failed"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, help="Limit items to process")
    parser.add_argument("--output", default="data/llm_batch_results.json")
    args = parser.parse_args()

    # Load sklearn results
    print("Loading sklearn classifications...")
    with open("data/all_production_classified.json") as f:
        sklearn_results = json.load(f)

    # Load raw items for content
    print("Loading raw items...")
    with open("/tmp/all_items.json") as f:
        raw_items = {item["id"]: item for item in json.load(f)}

    # Filter to unprocessed items
    unprocessed = [r for r in sklearn_results if r["llm_relevance_score"] == 0]
    print(f"Unprocessed items: {len(unprocessed)}")

    if args.limit:
        unprocessed = unprocessed[:args.limit]
        print(f"Processing first {args.limit} items")

    results = []
    start_time = time.time()

    for i, item in enumerate(unprocessed):
        raw = raw_items.get(item["id"], {})
        title = raw.get("title", "")
        content = raw.get("content", "")

        print(f"[{i+1}/{len(unprocessed)}] ID {item['id']}: {title[:50]}...")

        llm_result = classify_with_llm(title, content)

        result = {
            "id": item["id"],
            "title": title[:80],
            "sklearn_relevant": item["sklearn_relevant"],
            "sklearn_ak": item["sklearn_ak"],
            "llm_relevant": llm_result.get("llm_relevant"),
            "llm_ak": llm_result.get("llm_ak"),
            "reasoning": llm_result.get("reasoning", ""),
            "error": llm_result.get("error")
        }
        results.append(result)

        # Show comparison
        sk = "REL" if item["sklearn_relevant"] else "IRR"
        llm = "REL" if llm_result.get("llm_relevant") else ("IRR" if llm_result.get("llm_relevant") == False else "???")
        match = "✓" if (item["sklearn_relevant"] == llm_result.get("llm_relevant")) else "✗"
        print(f"  {match} sklearn={sk}, LLM={llm}")

        # Save progress every 50 items
        if (i + 1) % 50 == 0:
            with open(args.output, "w") as f:
                json.dump(results, f, indent=2, ensure_ascii=False)
            elapsed = time.time() - start_time
            rate = (i + 1) / elapsed * 60
            print(f"  Progress saved. {rate:.1f} items/min")

    # Final save
    with open(args.output, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    # Summary
    elapsed = time.time() - start_time
    print(f"\n{'='*60}")
    print(f"Processed {len(results)} items in {elapsed/60:.1f} minutes")

    agree = sum(1 for r in results if r["sklearn_relevant"] == r.get("llm_relevant"))
    print(f"Agreement: {agree}/{len(results)} ({agree/len(results)*100:.1f}%)")

    sklearn_rel = sum(1 for r in results if r["sklearn_relevant"])
    llm_rel = sum(1 for r in results if r.get("llm_relevant"))
    print(f"sklearn RELEVANT: {sklearn_rel}")
    print(f"LLM RELEVANT: {llm_rel}")


if __name__ == "__main__":
    main()
