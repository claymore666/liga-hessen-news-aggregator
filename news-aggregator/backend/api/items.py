"""API endpoints for news items."""

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db, async_session_maker
from models import Channel, Item, Priority, Source
from schemas import ItemListResponse, ItemResponse, ItemUpdate

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/items", response_model=ItemListResponse)
async def list_items(
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    source_id: int | None = None,
    channel_id: int | None = None,
    priority: str | None = Query(None, description="Filter by priority (single value or comma-separated: high,medium)"),
    is_read: bool | None = None,
    is_starred: bool | None = None,
    is_archived: bool | None = Query(None, description="Filter by archive status (default: exclude archived)"),
    since: datetime | None = None,
    until: datetime | None = None,
    search: str | None = None,
    relevant_only: bool = Query(True, description="Exclude LOW priority items (not Liga-relevant)"),
    connector_type: str | None = Query(None, description="Filter by connector type (rss, x_scraper, etc.)"),
    assigned_ak: str | None = Query(None, description="Filter by Arbeitskreis (comma-separated: AK1,AK2,AK3)"),
    sort_by: str = Query("date", description="Sort by: date, priority, source"),
    sort_order: str = Query("desc", description="Sort order: asc, desc"),
) -> ItemListResponse:
    """List items with filtering and pagination.

    By default, only shows relevant items (high, medium, low priority).
    Set relevant_only=false to include all items including NONE priority.
    By default, archived items are excluded. Set is_archived=true to show only archived,
    or is_archived=false to explicitly exclude them.
    """
    query = select(Item).options(
        selectinload(Item.channel).selectinload(Channel.source)
    )

    # Apply filters
    if channel_id is not None:
        query = query.where(Item.channel_id == channel_id)
    elif source_id is not None:
        # Filter by source through channel
        query = query.join(Channel).where(Channel.source_id == source_id)
    if priority is not None:
        # Support comma-separated priority values
        priority_values = [p.strip() for p in priority.split(",") if p.strip()]
        if len(priority_values) == 1:
            query = query.where(Item.priority == priority_values[0])
        elif len(priority_values) > 1:
            query = query.where(Item.priority.in_(priority_values))
    elif relevant_only:
        # Exclude LOW priority items (not Liga-relevant)
        query = query.where(Item.priority != Priority.NONE)
    if is_read is not None:
        query = query.where(Item.is_read == is_read)
    if is_starred is not None:
        query = query.where(Item.is_starred == is_starred)
    if is_archived is not None:
        query = query.where(Item.is_archived == is_archived)
    else:
        # By default, exclude archived items
        query = query.where(Item.is_archived == False)  # noqa: E712
    if since is not None:
        query = query.where(Item.published_at >= since)
    if until is not None:
        query = query.where(Item.published_at <= until)
    if search:
        search_pattern = f"%{search}%"
        query = query.where(
            (Item.title.ilike(search_pattern)) | (Item.content.ilike(search_pattern))
        )
    if connector_type is not None:
        # Filter by connector type through channel
        if channel_id is None and source_id is None:
            query = query.join(Channel)
        query = query.where(Channel.connector_type == connector_type)
    if assigned_ak is not None:
        # Support comma-separated AK values
        ak_values = [ak.strip() for ak in assigned_ak.split(",") if ak.strip()]
        if len(ak_values) == 1:
            # Filter by assigned_ak column (or fall back to metadata for legacy items)
            query = query.where(
                (Item.assigned_ak == ak_values[0]) |
                (func.json_extract(Item.metadata_, "$.llm_analysis.assigned_ak") == ak_values[0])
            )
        elif len(ak_values) > 1:
            # Multiple AKs - use IN clause
            from sqlalchemy import or_
            query = query.where(
                or_(
                    Item.assigned_ak.in_(ak_values),
                    func.json_extract(Item.metadata_, "$.llm_analysis.assigned_ak").in_(ak_values)
                )
            )

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total = await db.scalar(count_query) or 0

    # Apply ordering based on sort_by parameter
    if sort_by == "priority":
        # Priority order: high > medium > low > none
        priority_order = case(
            (Item.priority == "high", 1),
            (Item.priority == "medium", 2),
            (Item.priority == "low", 3),
            (Item.priority == "none", 4),
            else_=5,
        )
        if sort_order == "asc":
            query = query.order_by(priority_order.desc(), Item.id.desc())
        else:
            query = query.order_by(priority_order.asc(), Item.id.desc())
    elif sort_by == "source":
        # Need to join Channel and Source if not already joined
        if channel_id is None and source_id is None and connector_type is None:
            query = query.join(Channel, isouter=True).join(Source, isouter=True)
        elif connector_type is not None:
            query = query.join(Source, Channel.source_id == Source.id, isouter=True)
        else:
            query = query.join(Source, Channel.source_id == Source.id, isouter=True)
        if sort_order == "asc":
            query = query.order_by(Source.name.asc(), Item.id.desc())
        else:
            query = query.order_by(Source.name.desc(), Item.id.desc())
    else:
        # Default: sort by date
        if sort_order == "asc":
            query = query.order_by(Item.published_at.asc(), Item.id.asc())
        else:
            query = query.order_by(Item.published_at.desc(), Item.id.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    items = result.scalars().all()

    total_pages = (total + page_size - 1) // page_size

    return ItemListResponse(
        items=[ItemResponse.model_validate(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get("/items/retry-queue")
async def get_retry_queue_stats(
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get statistics about items waiting for LLM retry processing.

    Returns counts by retry priority (high, unknown, edge_case).
    Items without retry_priority metadata are counted as "unknown".
    """
    from sqlalchemy import literal_column

    # Count by retry priority, using COALESCE to treat NULL as 'unknown'
    priority_expr = func.coalesce(
        func.json_extract(Item.metadata_, "$.retry_priority"),
        literal_column("'unknown'")
    ).label("priority")

    query = select(
        priority_expr,
        func.count().label("count"),
    ).where(
        Item.needs_llm_processing == True  # noqa: E712
    ).group_by(priority_expr)

    result = await db.execute(query)
    rows = result.fetchall()

    by_priority = {str(row[0]).strip('"'): row[1] for row in rows}
    total = sum(by_priority.values())

    return {
        "total": total,
        "by_priority": by_priority,
        "order": ["high", "unknown", "edge_case", "low"],
    }


@router.post("/items/retry-queue/process")
async def trigger_retry_processing(
    background_tasks: BackgroundTasks,
    batch_size: int = Query(10, ge=1, le=50, description="Number of items to process"),
) -> dict:
    """Manually trigger LLM retry processing.

    Processes items marked with needs_llm_processing=True, prioritized by
    classifier confidence (high > unknown > edge_case).
    """
    from services.scheduler import retry_llm_processing

    background_tasks.add_task(retry_llm_processing, batch_size)

    return {
        "status": "started",
        "batch_size": batch_size,
        "message": "Retry processing started in background. Check logs for progress.",
    }


@router.get("/items/{item_id}", response_model=ItemResponse)
async def get_item(
    item_id: int,
    db: AsyncSession = Depends(get_db),
) -> ItemResponse:
    """Get a single item by ID."""
    query = (
        select(Item)
        .where(Item.id == item_id)
        .options(selectinload(Item.channel).selectinload(Channel.source))
    )
    result = await db.execute(query)
    item = result.scalar_one_or_none()

    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")

    return ItemResponse.model_validate(item)


@router.patch("/items/{item_id}", response_model=ItemResponse)
async def update_item(
    item_id: int,
    update: ItemUpdate,
    db: AsyncSession = Depends(get_db),
) -> ItemResponse:
    """Update an item (read status, starred, notes, content, summary, priority).

    When priority or assigned_ak is changed, the item is marked as manually reviewed.
    This creates verified training data for classification.
    """
    query = (
        select(Item)
        .where(Item.id == item_id)
        .options(selectinload(Item.channel).selectinload(Channel.source))
    )
    result = await db.execute(query)
    item = result.scalar_one_or_none()

    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")

    update_data = update.model_dump(exclude_unset=True)
    manually_reviewed = False

    # Handle priority conversion from string to enum
    if "priority" in update_data:
        priority_str = update_data.pop("priority")
        if priority_str:
            priority_map = {
                "high": Priority.HIGH,
                "medium": Priority.MEDIUM,
                "low": Priority.LOW,
                "none": Priority.NONE,
            }
            if priority_str.lower() in priority_map:
                new_priority = priority_map[priority_str.lower()]
                if item.priority != new_priority:
                    item.priority = new_priority
                    manually_reviewed = True
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid priority: {priority_str}. Must be high, medium, low, or none."
                )

    # Check if assigned_ak is being changed
    if "assigned_ak" in update_data:
        new_ak = update_data.get("assigned_ak")
        if item.assigned_ak != new_ak:
            manually_reviewed = True

    for key, value in update_data.items():
        setattr(item, key, value)

    # Mark as manually reviewed if priority or AK was changed
    if manually_reviewed:
        item.is_manually_reviewed = True
        item.reviewed_at = datetime.utcnow()

    await db.flush()
    await db.refresh(item)

    return ItemResponse.model_validate(item)


@router.post("/items/{item_id}/read")
async def mark_as_read(
    item_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Mark an item as read."""
    query = select(Item).where(Item.id == item_id)
    result = await db.execute(query)
    item = result.scalar_one_or_none()

    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")

    item.is_read = True
    return {"status": "ok"}


@router.post("/items/{item_id}/archive")
async def toggle_archive(
    item_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str | bool]:
    """Toggle archive status of an item."""
    query = select(Item).where(Item.id == item_id)
    result = await db.execute(query)
    item = result.scalar_one_or_none()

    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")

    item.is_archived = not item.is_archived
    return {"status": "ok", "is_archived": item.is_archived}


@router.post("/items/{item_id}/reprocess")
async def reprocess_single_item(
    item_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Reprocess a single item through the LLM.

    Forces reprocessing even if item already has LLM analysis.
    Runs in background and returns immediately.
    """
    # Verify item exists
    query = select(Item).where(Item.id == item_id)
    result = await db.execute(query)
    item = result.scalar_one_or_none()

    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")

    background_tasks.add_task(_reprocess_items_task, [item_id], force=True)

    return {
        "status": "started",
        "item_id": item_id,
        "message": "Reprocessing in background. Check logs for progress.",
    }


async def _refetch_item_task(item_id: int):
    """Background task to re-fetch an item and extract linked articles."""
    from connectors import ConnectorRegistry
    from services.article_extractor import ArticleExtractor

    async with async_session_maker() as db:
        try:
            result = await db.execute(
                select(Item)
                .where(Item.id == item_id)
                .options(selectinload(Item.channel).selectinload(Channel.source))
            )
            item = result.scalar_one_or_none()
            if not item:
                logger.error(f"Item {item_id} not found for re-fetch")
                return

            connector_type = item.channel.connector_type
            if connector_type not in ("x_scraper", "linkedin"):
                logger.warning(f"Re-fetch only supported for x_scraper/linkedin, got {connector_type}")
                return

            # Get the tweet/post URL
            post_url = item.url
            if not post_url:
                logger.warning(f"Item {item_id} has no URL")
                return

            extractor = ArticleExtractor()
            original_text = item.metadata_.get("original_tweet_text") or item.metadata_.get("original_post_text") or item.content
            links = []

            # Method 1: Extract from stored text
            links = extractor.extract_urls_from_text(original_text)

            # Method 2: Check for t.co links in text
            import re
            tco_links = re.findall(r'https?://t\.co/\w+', original_text)
            for tco in tco_links:
                resolved = await extractor.resolve_redirect(tco)
                if resolved and resolved != tco and "t.co" not in resolved:
                    if resolved not in links:
                        links.append(resolved)

            # Method 3: If no links found, scrape the tweet/post page to extract card links
            if not links and post_url and ("x.com" in post_url or "twitter.com" in post_url):
                logger.info(f"No links in text, scraping tweet page for card links: {post_url}")
                card_links = await _scrape_tweet_for_links(post_url)
                links.extend(card_links)

            # Try to fetch first valid article
            for link_url in links[:3]:
                try:
                    article = await extractor.fetch_article(link_url)
                    if article and article.is_article:
                        # Update item content with article
                        author = item.author or "Unknown"
                        combined_content = f"""Tweet von @{author}:
{original_text}

---

Verlinkter Artikel von {article.source_domain}:
{article.title or 'Unbekannter Titel'}

{article.content[:4000]}"""

                        item.content = combined_content
                        item.metadata_ = {
                            **item.metadata_,
                            "extracted_links": links,
                            "linked_articles": [{
                                "url": article.url,
                                "title": article.title,
                                "domain": article.source_domain,
                                "content_length": len(article.content),
                            }],
                            "refetched_at": datetime.utcnow().isoformat(),
                        }

                        await db.flush()
                        await db.commit()
                        logger.info(f"Re-fetched item {item_id} with article from {article.source_domain}")

                        # Now reprocess through LLM
                        await _reprocess_items_task([item_id], force=True)
                        return

                except Exception as e:
                    logger.debug(f"Failed to fetch article from {link_url}: {e}")

            logger.warning(f"No valid articles found for item {item_id}")

        except Exception as e:
            logger.error(f"Error re-fetching item {item_id}: {e}")


async def _scrape_tweet_for_links(tweet_url: str) -> list[str]:
    """Scrape a tweet page to extract article links from cards.

    Args:
        tweet_url: Full URL to the tweet

    Returns:
        List of article URLs found in the tweet's cards
    """
    import json
    import random
    from pathlib import Path
    from playwright.async_api import async_playwright
    from playwright_stealth import Stealth

    links = []
    cookie_file = Path("/app/data/x_cookies.json")

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )

            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0",
                viewport={"width": 1920, "height": 1080},
                locale="de-DE",
            )

            # Load cookies if available
            if cookie_file.exists():
                with open(cookie_file) as f:
                    cookies = json.load(f)
                await context.add_cookies(cookies)
                logger.debug("Loaded X.com cookies for refetch")

            page = await context.new_page()
            stealth = Stealth()
            await stealth.apply_stealth_async(page)

            await page.goto(tweet_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)  # Wait for JS to render

            # Extract links from tweet cards
            skip_domains = ("x.com", "twitter.com", "t.co", "pic.twitter.com", "pbs.twimg.com")

            # Find all links in the tweet
            all_links = await page.query_selector_all('article a[href*="://"]')
            for link_el in all_links:
                href = await link_el.get_attribute("href")
                if href:
                    # Skip internal X links
                    is_internal = any(d in href for d in skip_domains)
                    if not is_internal and href.startswith("http"):
                        if href not in links:
                            links.append(href)

            # Also check for t.co links and resolve them
            tco_links = await page.query_selector_all('a[href*="t.co"]')
            for link_el in tco_links:
                href = await link_el.get_attribute("href")
                if href and "t.co" in href:
                    from services.article_extractor import ArticleExtractor
                    extractor = ArticleExtractor()
                    resolved = await extractor.resolve_redirect(href)
                    if resolved and "t.co" not in resolved:
                        if resolved not in links:
                            links.append(resolved)

            await browser.close()

    except Exception as e:
        logger.warning(f"Error scraping tweet for links: {e}")

    logger.info(f"Scraped {len(links)} links from tweet: {links}")
    return links


@router.post("/items/{item_id}/refetch")
async def refetch_item(
    item_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Re-fetch an item to extract linked article content.

    For x_scraper and linkedin items, this will:
    1. Extract any article links from the tweet/post text
    2. Resolve t.co shortened URLs
    3. Fetch article content from the linked URLs
    4. Update the item with the article content
    5. Reprocess through the LLM for better analysis

    Runs in background and returns immediately.
    """
    # Verify item exists and is from a supported connector
    query = (
        select(Item)
        .where(Item.id == item_id)
        .options(selectinload(Item.channel))
    )
    result = await db.execute(query)
    item = result.scalar_one_or_none()

    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")

    connector_type = item.channel.connector_type
    if connector_type not in ("x_scraper", "linkedin"):
        raise HTTPException(
            status_code=400,
            detail=f"Re-fetch only supported for x_scraper/linkedin items, got {connector_type}"
        )

    background_tasks.add_task(_refetch_item_task, item_id)

    return {
        "status": "started",
        "item_id": item_id,
        "connector_type": connector_type,
        "message": "Re-fetching article links in background. Check logs for progress.",
    }


@router.post("/items/mark-all-read")
async def mark_all_as_read(
    source_id: int | None = None,
    channel_id: int | None = None,
    before: datetime | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    """Mark multiple items as read."""
    query = select(Item).where(Item.is_read == False)  # noqa: E712

    if channel_id is not None:
        query = query.where(Item.channel_id == channel_id)
    elif source_id is not None:
        query = query.join(Channel).where(Channel.source_id == source_id)
    if before is not None:
        query = query.where(Item.published_at <= before)

    result = await db.execute(query)
    items = result.scalars().all()

    for item in items:
        item.is_read = True

    return {"marked": len(items)}


# Background task for reprocessing
async def _reprocess_items_task(item_ids: list[int], force: bool):
    """Background task to reprocess items through LLM."""
    from services.processor import create_processor_from_settings

    processor = await create_processor_from_settings()
    processed = 0
    errors = 0

    async with async_session_maker() as db:
        for item_id in item_ids:
            try:
                result = await db.execute(
                    select(Item)
                    .where(Item.id == item_id)
                    .options(selectinload(Item.channel).selectinload(Channel.source))
                )
                item = result.scalar_one_or_none()
                if not item:
                    continue

                # Skip if already processed (unless force)
                if not force and item.metadata_.get("llm_analysis"):
                    continue

                # Run LLM analysis
                analysis = await processor.analyze(item)

                # Update item
                if analysis.get("summary"):
                    item.summary = analysis["summary"]

                # Set detailed analysis
                if analysis.get("detailed_analysis"):
                    item.detailed_analysis = analysis["detailed_analysis"]

                # New model returns "priority", old model used "priority_suggestion"
                # Map LLM output to new priority system: critical→high, high→medium, medium→low, low→none
                llm_priority = analysis.get("priority") or analysis.get("priority_suggestion")
                if llm_priority == "critical":
                    item.priority = Priority.HIGH
                elif llm_priority == "high":
                    item.priority = Priority.MEDIUM
                elif llm_priority == "medium":
                    item.priority = Priority.LOW
                else:
                    # null or "low" = NONE (not relevant)
                    item.priority = Priority.NONE

                # Store analysis metadata
                item.metadata_ = {
                    **item.metadata_,
                    "llm_analysis": {
                        "relevance_score": analysis.get("relevance_score", 0.5),
                        "priority_suggestion": llm_priority,
                        "assigned_ak": analysis.get("assigned_ak"),
                        "tags": analysis.get("tags", []),
                        "reasoning": analysis.get("reasoning"),
                    },
                }

                await db.flush()
                processed += 1

                if processed % 10 == 0:
                    logger.info(f"Reprocessed {processed}/{len(item_ids)} items")

            except Exception as e:
                logger.error(f"Error reprocessing item {item_id}: {e}")
                errors += 1

        await db.commit()

    logger.info(f"Reprocessing complete: {processed} processed, {errors} errors")


@router.post("/items/reprocess")
async def reprocess_items(
    background_tasks: BackgroundTasks,
    source_id: int | None = Query(None, description="Only reprocess items from this source"),
    channel_id: int | None = Query(None, description="Only reprocess items from this channel"),
    connector_type: str | None = Query(None, description="Only reprocess items from this connector type (e.g., x_scraper, rss)"),
    priority: Priority | None = Query(None, description="Only reprocess items with this priority"),
    exclude_low: bool = Query(False, description="Exclude LOW priority items (reprocess only relevant items)"),
    limit: int = Query(100, ge=1, le=1000, description="Max items to reprocess"),
    force: bool = Query(False, description="Reprocess even if already has LLM analysis"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Reprocess items through the LLM for priority and summary.

    Runs in background. Check logs for progress.
    """
    query = select(Item.id).order_by(Item.published_at.desc())

    # Join with Channel if we need to filter by source or connector type
    needs_channel_join = source_id is not None or connector_type is not None

    if channel_id is not None:
        query = query.where(Item.channel_id == channel_id)
    elif needs_channel_join:
        query = query.join(Channel)
        if source_id is not None:
            query = query.where(Channel.source_id == source_id)
        if connector_type is not None:
            query = query.where(Channel.connector_type == connector_type)

    # Filter by priority
    if priority is not None:
        query = query.where(Item.priority == priority)
    elif exclude_low:
        query = query.where(Item.priority != Priority.NONE)

    # When not forcing, only select items without LLM analysis
    # Use SQLite JSON extract to check if key exists
    if not force:
        query = query.where(
            func.json_extract(Item.metadata_, "$.llm_analysis").is_(None)
        )

    query = query.limit(limit)
    result = await db.execute(query)
    item_ids = [row[0] for row in result.fetchall()]

    if not item_ids:
        return {"status": "no items to process", "count": 0}

    background_tasks.add_task(_reprocess_items_task, item_ids, force)

    return {
        "status": "started",
        "count": len(item_ids),
        "force": force,
        "message": "Reprocessing in background. Check logs for progress.",
    }
