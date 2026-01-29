"""Backfill topics for items that have LLM analysis but no topics.

Run inside the backend container:
    python scripts/backfill_topics.py
"""

import asyncio
import json
import logging
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def backfill():
    from database import async_session_maker
    from models import Item, Channel, Source
    from services.processor import create_processor_from_settings, ANALYSIS_SYSTEM_PROMPT
    from sqlalchemy import select, text as sql_text
    from sqlalchemy.orm import selectinload
    from sqlalchemy.orm.attributes import flag_modified

    processor = await create_processor_from_settings()
    if not processor:
        logger.error("LLM processor not available (is Ollama running?)")
        return

    # Get candidate IDs first (light query)
    async with async_session_maker() as db:
        query = (
            select(Item.id)
            .where(
                Item.published_at >= sql_text("CURRENT_DATE - INTERVAL '30 days'"),
                Item.similar_to_id.is_(None),
                Item.priority != "none",
            )
            .order_by(Item.published_at.desc())
        )
        result = await db.execute(query)
        all_ids = [row[0] for row in result.fetchall()]

    logger.info(f"Found {len(all_ids)} candidate items, filtering those needing topics...")

    # Process one at a time, committing each to avoid losing work
    processed = 0
    skipped = 0
    errors = 0

    for item_id in all_ids:
        async with async_session_maker() as db:
            result = await db.execute(
                select(Item)
                .where(Item.id == item_id)
                .options(selectinload(Item.channel).selectinload(Channel.source))
            )
            item = result.scalar_one_or_none()
            if not item:
                continue

            llm = (item.metadata_ or {}).get("llm_analysis", {})
            if not llm or llm.get("topics"):
                skipped += 1
                continue

            source_name = item.channel.source.name if item.channel and item.channel.source else "Unbekannt"
            date_str = item.published_at.strftime("%Y-%m-%d") if item.published_at else "Unbekannt"

            prompt = f"""Titel: {item.title}
Inhalt: {item.content[:6000]}
Quelle: {source_name}
Datum: {date_str}"""

            assistant_json = json.dumps({
                "summary": item.summary or "",
                "relevant": True,
                "priority": llm.get("priority_suggestion"),
                "assigned_aks": llm.get("assigned_aks", []),
                "tags": llm.get("tags", []),
                "reasoning": llm.get("reasoning", ""),
            }, ensure_ascii=False)

            conversation = [
                {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": assistant_json},
            ]

            try:
                topics = await processor.extract_topics(conversation)
                if topics:
                    item.metadata_["llm_analysis"]["topics"] = topics
                    flag_modified(item, "metadata_")
                    await db.commit()
                    processed += 1
                    logger.info(f"  [{processed}] [{item.id}] {item.title[:50]}... -> {topics}")
                else:
                    skipped += 1
                    logger.warning(f"  [{item.id}] No topics returned")
            except Exception as e:
                errors += 1
                logger.error(f"  [{item.id}] Failed: {e}")

    logger.info(f"Backfill complete: {processed} processed, {skipped} skipped, {errors} errors")


if __name__ == "__main__":
    asyncio.run(backfill())
