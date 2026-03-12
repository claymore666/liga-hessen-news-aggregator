"""Daily newsletter digest generation and delivery.

Collects high/medium priority items, sends them to the LLM for editorial
curation, then renders and sends an HTML+text email digest.
"""

import json
import logging
import time
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from zoneinfo import ZoneInfo

from config import settings
from database import async_session_maker
from models import Channel, Digest, DigestStatus, Item, LLMPrompt, Priority, Source

logger = logging.getLogger(__name__)

BERLIN_TZ = ZoneInfo("Europe/Berlin")

# Hardcoded fallback system prompt for digest generation
DEFAULT_DIGEST_SYSTEM_PROMPT = """\
Du erstellst einen täglichen Nachrichtenüberblick für die Liga der Freien Wohlfahrtspflege Hessen.

Die Liga ist der Zusammenschluss der sechs Spitzenverbände der Freien Wohlfahrtspflege in Hessen \
(AWO, Caritas, Diakonie, DRK, Paritätischer, ZWST/Jüdische Gemeinden). Thematische Schwerpunkte: \
Sozialpolitik, Migration, Gesundheit/Pflege, Eingliederungshilfe, Kinder/Jugend/Familie, Digitalisierung, Wohnen.

Du erhältst eine nummerierte Liste von Nachrichtenartikeln mit Titel, Quelle, Priorität und Zusammenfassung. \
Artikel mit [BEREITS BERICHTET] wurden in früheren Digests erwähnt — nur aufnehmen, wenn es eine \
wesentliche neue Entwicklung gibt.

Erstelle einen strukturierten JSON-Output mit exakt diesem Schema:

{
  "editorial_intro": "1-2 Sätze, die den Nachrichtentag zusammenfassen",
  "urgent": [
    {
      "item_ref": 1,
      "headline": "Kurze Überschrift (nah am Original)",
      "context": "2-3 Sätze: Was genau ist passiert? Welche Fakten, Zahlen, Entscheidungen?"
    }
  ],
  "top_stories": [
    {
      "item_ref": 2,
      "headline": "Kurze Überschrift (nah am Original)",
      "context": "2-3 Sätze: Was ist passiert und welche Bedeutung hat es?"
    }
  ],
  "further_news": [
    {
      "item_ref": 5,
      "headline": "Einzeiler mit Quelle"
    }
  ]
}

Regeln:
- "urgent": Nur Artikel, die SOFORTIGE Reaktion erfordern (Gesetzesfristen, Budgetkürzungen). \
Oft leer — das ist normal.
- "top_stories": 3-5 wichtigste Artikel. Beschreibe WAS passiert ist — Fakten, Zahlen, \
Entscheidungen, Hintergründe. KEINE Handlungsempfehlungen, keine Vorschläge was die Liga \
tun sollte/könnte/müsste.
- "further_news": Restliche Artikel als Einzeiler. Kann leer sein bei wenig Artikeln.
- Headlines: Bleib nah am Originaltitel, erfinde keine neuen Überschriften.
- Stil: Journalistisch-informativ, wie ein Pressespiegel. Berichte was ist, nicht was sein sollte.
- item_ref verweist auf die Nummer in eckigen Klammern [N] aus der Eingabe.
- Antworte NUR mit validem JSON, kein Markdown, kein Text drumherum.
"""


async def collect_digest_items(
    db,
    since: datetime,
    until: datetime,
) -> list[Item]:
    """Query candidate items for the digest.

    Returns high/medium priority, non-duplicate items ordered by priority_score.
    """
    query = (
        select(Item)
        .join(Channel, Item.channel_id == Channel.id)
        .options(selectinload(Item.channel).selectinload(Channel.source))
        .where(
            Item.fetched_at >= since,
            Item.fetched_at < until,
            Item.similar_to_id.is_(None),
            Item.priority.in_([Priority.HIGH, Priority.MEDIUM]),
        )
        .order_by(Item.priority_score.desc())
        .limit(settings.digest_max_items)
    )
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_recently_covered_item_ids(db, lookback_days: int) -> set[int]:
    """Load item IDs from recent digests for dedup marking."""
    cutoff = datetime.utcnow() - timedelta(days=lookback_days)
    query = select(Digest.item_ids).where(
        Digest.date >= cutoff,
        Digest.status.in_([DigestStatus.GENERATED, DigestStatus.SENT]),
    )
    result = await db.execute(query)
    covered = set()
    for (ids,) in result.all():
        if ids:
            covered.update(ids)
    return covered


def build_llm_prompt(items: list[Item], covered_ids: set[int]) -> str:
    """Format items into a numbered list for the LLM."""
    lines = []
    for i, item in enumerate(items, 1):
        source_name = item.channel.source.name if item.channel and item.channel.source else "Unbekannt"
        priority_val = item.priority.value if hasattr(item.priority, "value") else str(item.priority)
        summary = (item.summary or "")[:150]
        line = f"[{i}] {item.title} | {source_name} | {priority_val}"
        if summary:
            line += f"\n    {summary}"
        if item.id in covered_ids:
            line += "\n    [BEREITS BERICHTET]"
        lines.append(line)
    return "\n\n".join(lines)


async def _create_llm_service():
    """Build an LLMService using the same pattern as create_processor_from_settings."""
    from services.llm import LLMService, OllamaProvider, OpenRouterProvider

    providers = []
    providers.append(
        OllamaProvider(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            timeout=settings.ollama_timeout,
        )
    )
    if settings.openrouter_api_key:
        providers.append(
            OpenRouterProvider(
                api_key=settings.openrouter_api_key,
                model=settings.openrouter_model,
                timeout=settings.openrouter_timeout,
            )
        )
    return LLMService(providers)


async def _get_digest_prompt(db) -> tuple[str, str | None, int | None]:
    """Load the digest system prompt from DB, fall back to hardcoded default.

    Returns (system_prompt, model_name, version).
    """
    try:
        prompt = await db.scalar(
            select(LLMPrompt)
            .where(LLMPrompt.model == "digest-daily", LLMPrompt.active == True)  # noqa: E712
            .order_by(LLMPrompt.version.desc())
        )
        if prompt:
            return prompt.system_prompt, prompt.model, prompt.version
    except Exception as e:
        logger.warning(f"Could not load digest prompt from DB: {e}")

    return DEFAULT_DIGEST_SYSTEM_PROMPT, None, None


def _parse_llm_response(text: str) -> dict:
    """Parse the LLM JSON response, stripping markdown fences if present."""
    text = text.strip()
    if text.startswith("```"):
        # Strip ```json ... ``` fences
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return json.loads(text)


async def generate_digest(db) -> int:
    """Generate a digest for today. Returns the digest ID."""
    now_berlin = datetime.now(BERLIN_TZ)
    cutoff = now_berlin.replace(
        hour=settings.digest_cutoff_hour,
        minute=settings.digest_cutoff_minute,
        second=0,
        microsecond=0,
    )
    cutoff_utc = cutoff.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)

    # Check if a digest already exists for this cutoff
    existing = await db.scalar(
        select(Digest).where(Digest.date == cutoff_utc)
    )
    if existing:
        logger.info(f"Digest already exists for {cutoff_utc.isoformat()} (id={existing.id})")
        return existing.id

    # Determine 'since': last digest date or 24h ago
    last_digest = await db.scalar(
        select(Digest)
        .where(Digest.status.in_([DigestStatus.GENERATED, DigestStatus.SENT]))
        .order_by(Digest.date.desc())
    )
    if last_digest:
        since = last_digest.date
    else:
        since = cutoff_utc - timedelta(hours=24)

    logger.info(f"Generating digest: since={since.isoformat()}, until={cutoff_utc.isoformat()}")

    # Collect items
    items = await collect_digest_items(db, since, cutoff_utc)

    if not items and settings.digest_skip_empty:
        logger.info("No items for digest, skipping (digest_skip_empty=true)")
        from services.digest_email import render_digest_html, render_digest_text

        empty_content = {"editorial_intro": "Heute keine relevanten Meldungen.", "urgent": [], "top_stories": [], "further_news": []}
        digest = Digest(
            date=cutoff_utc,
            status=DigestStatus.GENERATED,
            item_ids=[],
            content=empty_content,
            html_body=render_digest_html(empty_content, now_berlin.date(), 0),
            text_body=render_digest_text(empty_content, now_berlin.date(), 0),
        )
        db.add(digest)
        await db.commit()
        await db.refresh(digest)
        return digest.id

    # Dedup check
    covered_ids = await get_recently_covered_item_ids(db, settings.digest_lookback_days)

    # Build LLM prompt
    user_prompt = build_llm_prompt(items, covered_ids)
    system_prompt, prompt_model, prompt_version = await _get_digest_prompt(db)

    # Call LLM
    llm_service = await _create_llm_service()
    start_ms = time.monotonic()
    try:
        response = await llm_service.complete(
            prompt=user_prompt,
            system=system_prompt,
            temperature=0.4,
        )
        content = _parse_llm_response(response.text)
    except Exception as e:
        logger.error(f"Digest LLM call failed: {e}")
        digest = Digest(
            date=cutoff_utc,
            status=DigestStatus.FAILED,
            item_ids=[item.id for item in items],
            error_message=str(e),
        )
        db.add(digest)
        await db.commit()
        await db.refresh(digest)
        return digest.id

    generation_ms = int((time.monotonic() - start_ms) * 1000)

    # Map item_refs back to real item IDs in content
    ref_to_item = {i + 1: item for i, item in enumerate(items)}
    for section in ("urgent", "top_stories", "further_news"):
        for entry in content.get(section, []):
            ref = entry.get("item_ref")
            if ref and ref in ref_to_item:
                entry["item_id"] = ref_to_item[ref].id
                entry["url"] = ref_to_item[ref].url
                entry["source"] = (
                    ref_to_item[ref].channel.source.name
                    if ref_to_item[ref].channel and ref_to_item[ref].channel.source
                    else None
                )
                entry["assigned_aks"] = ref_to_item[ref].assigned_aks or []

    # Render email bodies
    from services.digest_email import render_digest_html, render_digest_text

    digest_date = now_berlin.date()
    html_body = render_digest_html(content, digest_date, len(items))
    text_body = render_digest_text(content, digest_date, len(items))

    digest = Digest(
        date=cutoff_utc,
        status=DigestStatus.GENERATED,
        item_ids=[item.id for item in items],
        content=content,
        html_body=html_body,
        text_body=text_body,
        llm_model=settings.ollama_model,
        llm_prompt_version=prompt_version,
        generation_time_ms=generation_ms,
    )
    db.add(digest)
    await db.commit()
    await db.refresh(digest)

    logger.info(
        f"Digest generated: id={digest.id}, items={len(items)}, "
        f"generation_time={generation_ms}ms"
    )
    return digest.id


async def send_digest(db, digest_id: int) -> bool:
    """Send a generated digest via email. Returns True on success."""
    digest = await db.get(Digest, digest_id)
    if not digest:
        raise ValueError(f"Digest {digest_id} not found")
    if digest.status == DigestStatus.SENT:
        logger.warning(f"Digest {digest_id} already sent")
        return True
    if digest.status == DigestStatus.FAILED and not digest.html_body:
        raise ValueError(f"Digest {digest_id} failed generation, cannot send")

    # Determine recipients
    recipients = [r.strip() for r in settings.digest_recipients.split(",") if r.strip()]
    if not recipients:
        raise ValueError("No digest recipients configured (DIGEST_RECIPIENTS)")

    # Skip empty digests
    if settings.digest_skip_empty and not digest.item_ids:
        logger.info(f"Skipping send for empty digest {digest_id}")
        digest.status = DigestStatus.SENT
        digest.sent_at = datetime.utcnow()
        digest.recipients = recipients
        await db.commit()
        return True

    from services.digest_email import send_digest_email

    success, message = send_digest_email(
        html_body=digest.html_body,
        text_body=digest.text_body,
        recipients=recipients,
        date=digest.date,
    )

    if success:
        digest.status = DigestStatus.SENT
        digest.sent_at = datetime.utcnow()
        digest.recipients = recipients
        logger.info(f"Digest {digest_id} sent to {len(recipients)} recipients")
    else:
        digest.status = DigestStatus.FAILED
        digest.error_message = message
        logger.error(f"Digest {digest_id} send failed: {message}")

    await db.commit()
    return success


async def generate_and_send_digest() -> None:
    """Scheduled entrypoint: generate + send digest with own DB session."""
    async with async_session_maker() as db:
        try:
            digest_id = await generate_digest(db)
            digest = await db.get(Digest, digest_id)

            if digest and digest.status == DigestStatus.GENERATED:
                await send_digest(db, digest_id)
        except Exception as e:
            logger.error(f"Digest generation/send failed: {e}", exc_info=True)
