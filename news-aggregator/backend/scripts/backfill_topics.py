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

    async with async_session_maker() as db:
        # Find items from today with llm_analysis but no topics
        query = (
            select(Item)
            .options(selectinload(Item.channel).selectinload(Channel.source))
            .where(
                Item.published_at >= sql_text("CURRENT_DATE"),
                Item.similar_to_id.is_(None),
                Item.priority != "none",
            )
            .order_by(Item.published_at.desc())
        )
        result = await db.execute(query)
        items = result.scalars().all()

        candidates = []
        for item in items:
            llm = (item.metadata_ or {}).get("llm_analysis", {})
            if llm and not llm.get("topics"):
                candidates.append(item)

        logger.info(f"Found {len(candidates)} items to backfill topics for")

        for item in candidates:
            source_name = item.channel.source.name if item.channel and item.channel.source else "Unbekannt"
            date_str = item.published_at.strftime("%Y-%m-%d") if item.published_at else "Unbekannt"

            prompt = f"""Titel: {item.title}
Inhalt: {item.content[:6000]}
Quelle: {source_name}
Datum: {date_str}"""

            # Reconstruct conversation: system + user + assistant (using existing summary as stand-in)
            llm_analysis = item.metadata_.get("llm_analysis", {})
            # Build a fake assistant response from stored analysis
            assistant_json = json.dumps({
                "summary": item.summary or "",
                "relevant": True,
                "priority": llm_analysis.get("priority_suggestion"),
                "assigned_aks": llm_analysis.get("assigned_aks", []),
                "tags": llm_analysis.get("tags", []),
                "reasoning": llm_analysis.get("reasoning", ""),
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
                    logger.info(f"  [{item.id}] {item.title[:50]}... -> {topics}")
                else:
                    logger.warning(f"  [{item.id}] No topics returned")
            except Exception as e:
                logger.error(f"  [{item.id}] Failed: {e}")

        await db.commit()
        logger.info("Backfill complete")


if __name__ == "__main__":
    asyncio.run(backfill())
