"""
Title Pre-filter Service using a small LLM (qwen3:8b).

Runs between the classifier and the full LLM to quickly reject obvious
false positives based on title alone. Uses ~0.12s per title vs ~15s for
the full LLM analysis.

VRAM constraint: The pre-filter model and main model don't fit in VRAM
simultaneously, so this service handles model switching (unload main →
run pre-filter → unload pre-filter → main auto-loads on next request).
"""

import json
import logging
import time
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# Prompt file path and cache
_PROMPT_PATH = Path("/app/prompts/title_prefilter.txt")
_prompt_cache: str | None = None
_prompt_mtime: float = 0.0


def _load_prompt() -> str:
    """Load the system prompt from file, with mtime-based caching."""
    global _prompt_cache, _prompt_mtime

    # Try container path first, fall back to local dev path
    prompt_path = _PROMPT_PATH
    if not prompt_path.exists():
        local_path = Path(__file__).parent.parent / "prompts" / "title_prefilter.txt"
        if local_path.exists():
            prompt_path = local_path
        else:
            raise FileNotFoundError(
                f"Title pre-filter prompt not found at {_PROMPT_PATH} or {local_path}"
            )

    current_mtime = prompt_path.stat().st_mtime
    if _prompt_cache is None or current_mtime != _prompt_mtime:
        _prompt_cache = prompt_path.read_text(encoding="utf-8").strip()
        _prompt_mtime = current_mtime
        logger.info(f"Loaded title pre-filter prompt from {prompt_path}")

    return _prompt_cache


async def unload_model(base_url: str, model: str) -> None:
    """Unload a model from VRAM via Ollama API."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            await client.post(
                f"{base_url}/api/generate",
                json={"model": model, "keep_alive": "0s"},
            )
        logger.debug(f"Unloaded model {model}")
    except Exception as e:
        logger.warning(f"Failed to unload model {model}: {e}")


async def check_title(
    base_url: str, model: str, title: str
) -> dict:
    """
    Check a single title for relevance using the pre-filter model.

    Returns:
        {"relevant": bool, "duration_ms": int}
    """
    prompt = _load_prompt()
    start = time.time()

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{base_url}/api/chat",
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": title},
                    ],
                    "stream": False,
                    "options": {
                        "temperature": 0,
                        "num_predict": 30,
                    },
                    "think": False,
                },
            )
            response.raise_for_status()
            data = response.json()

        duration_ms = int((time.time() - start) * 1000)
        content = data.get("message", {}).get("content", "").strip()

        # Parse JSON response
        relevant = _parse_response(content)

        return {"relevant": relevant, "duration_ms": duration_ms}

    except Exception as e:
        duration_ms = int((time.time() - start) * 1000)
        logger.warning(f"Title pre-filter error for '{title[:50]}...': {e}")
        # Default to relevant on error (never lose items)
        return {"relevant": True, "duration_ms": duration_ms, "error": str(e)}


def _parse_response(content: str) -> bool:
    """Parse the LLM response, defaulting to True (relevant) on any error."""
    try:
        # Try to extract JSON from response
        # Handle cases where LLM wraps JSON in markdown code blocks
        cleaned = content.strip()
        if "```" in cleaned:
            # Extract content between code blocks
            parts = cleaned.split("```")
            for part in parts:
                part = part.strip()
                if part.startswith("json"):
                    part = part[4:].strip()
                if part.startswith("{"):
                    cleaned = part
                    break

        parsed = json.loads(cleaned)
        result = parsed.get("relevant", True)
        if isinstance(result, bool):
            return result
        # Handle string "true"/"false"
        if isinstance(result, str):
            return result.lower() != "false"
        return True
    except (json.JSONDecodeError, AttributeError, KeyError):
        logger.warning(f"Could not parse pre-filter response: {content[:100]}")
        return True  # Default to relevant


async def run_prefilter_batch(
    base_url: str,
    items: list[dict],
    main_model: str,
    prefilter_model: str,
) -> dict:
    """
    Run the pre-filter on a batch of items with model switching.

    Args:
        base_url: Ollama API base URL
        items: List of dicts with "id" and "title" keys
        main_model: The main LLM model to unload before and after
        prefilter_model: The pre-filter model to use

    Returns:
        {"checked": N, "filtered": N, "results": [{"id": int, "relevant": bool, "duration_ms": int}, ...]}
    """
    if not items:
        return {"checked": 0, "filtered": 0, "results": []}

    logger.info(f"Title pre-filter: processing {len(items)} items")

    # Step 1: Unload main model to free VRAM
    await unload_model(base_url, main_model)

    results = []
    filtered = 0

    try:
        # Step 2: Process all titles through pre-filter model
        for item in items:
            result = await check_title(base_url, prefilter_model, item["title"])
            result["id"] = item["id"]
            results.append(result)

            if not result["relevant"]:
                filtered += 1
                logger.info(
                    f"Pre-filter rejected: [{item['id']}] {item['title'][:60]}... "
                    f"({result['duration_ms']}ms)"
                )
    finally:
        # Step 3: Always unload pre-filter model (even on error)
        await unload_model(base_url, prefilter_model)

    logger.info(
        f"Title pre-filter complete: {len(results)} checked, "
        f"{filtered} filtered out, {len(results) - filtered} passed"
    )

    return {
        "checked": len(results),
        "filtered": filtered,
        "results": results,
    }


async def prefilter_single(
    base_url: str, model: str, title: str
) -> dict:
    """
    Run title pre-filter on a single item without model switching.

    For API use — caller manages model loading/unloading.

    Returns:
        {"relevant": bool, "duration_ms": int}
    """
    return await check_title(base_url, model, title)
