"""Connector for liga-hessen.de press releases.

liga-hessen.de runs on TYPO3 and offers no RSS feed. This connector scrapes
the press release listing and fetches each linked detail page for the real
publication date and full body content.

Schema.org metadata is present on both pages, so the selectors are stable:
- Listing: `div.article[itemscope]` items with `h3 a[itemprop=url]` and
  `[itemprop=headline]`
- Detail: `time[itemprop=datePublished]` and `[itemprop=articleBody]`
"""

import asyncio
import logging
from datetime import datetime
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field, HttpUrl

from .base import BaseConnector, RawItem
from .registry import ConnectorRegistry

logger = logging.getLogger(__name__)

DEFAULT_LIST_URL = "https://www.liga-hessen.de/veroeffentlichungen/pressemeldungen"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) liga-hessen-news-aggregator"
DETAIL_FETCH_CONCURRENCY = 4


class LigaHessenConfig(BaseModel):
    """Configuration for the liga-hessen.de press release connector."""

    list_url: HttpUrl = Field(
        default=DEFAULT_LIST_URL,
        description="Listing page URL (defaults to /veroeffentlichungen/pressemeldungen)",
    )
    max_items: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Maximum items to extract from the listing page",
    )


@ConnectorRegistry.register
class LigaHessenConnector(BaseConnector):
    """Press release scraper for liga-hessen.de (TYPO3)."""

    connector_type = "liga_hessen"
    display_name = "Liga Hessen Pressemeldungen"
    description = "Scrapes press releases directly from liga-hessen.de"
    config_schema = LigaHessenConfig

    async def fetch(self, config: LigaHessenConfig) -> list[RawItem]:
        list_url = str(config.list_url)

        async with httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            response = await client.get(list_url)
            response.raise_for_status()

            articles = _parse_listing(response.text, list_url, config.max_items)
            if not articles:
                logger.warning("No press releases found on %s", list_url)
                return []

            semaphore = asyncio.Semaphore(DETAIL_FETCH_CONCURRENCY)

            async def enrich(article: dict) -> RawItem | None:
                async with semaphore:
                    return await _fetch_detail(client, article)

            results = await asyncio.gather(
                *(enrich(a) for a in articles), return_exceptions=True
            )

        items: list[RawItem] = []
        for article, result in zip(articles, results):
            if isinstance(result, Exception):
                logger.warning(
                    "liga_hessen: detail fetch failed for %s: %s",
                    article["url"], result,
                )
                # Fallback: use listing-only data
                items.append(_listing_only_item(article))
            elif result is not None:
                items.append(result)
        return items

    async def validate(self, config: LigaHessenConfig) -> tuple[bool, str]:
        try:
            async with httpx.AsyncClient(
                timeout=10.0,
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT},
            ) as client:
                response = await client.get(str(config.list_url))
                response.raise_for_status()
            articles = _parse_listing(response.text, str(config.list_url), config.max_items)
            if not articles:
                return False, "No press release items found on the listing page"
            return True, f"Found {len(articles)} press releases"
        except httpx.TimeoutException:
            return False, "Connection timeout"
        except httpx.HTTPStatusError as e:
            return False, f"HTTP error: {e.response.status_code}"
        except Exception as e:
            return False, f"Error: {e}"


def _parse_listing(html: str, base_url: str, max_items: int) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    out: list[dict] = []
    for el in soup.select("div.article[itemscope]"):
        link_el = el.select_one("h3 a[itemprop=url], h3 a")
        if not link_el or not link_el.get("href"):
            continue
        title_el = el.select_one("[itemprop=headline]") or link_el
        title = title_el.get_text(strip=True)
        if not title:
            continue
        url = urljoin(base_url, link_el["href"])
        teaser_el = el.select_one("[itemprop=description], p")
        teaser = teaser_el.get_text(" ", strip=True) if teaser_el else ""
        out.append({"title": title, "url": url, "teaser": teaser})
        if len(out) >= max_items:
            break
    return out


async def _fetch_detail(client: httpx.AsyncClient, article: dict) -> RawItem | None:
    response = await client.get(article["url"])
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "lxml")

    body_el = soup.select_one("[itemprop=articleBody]") or soup.select_one(".news-text-wrap")
    body = body_el.get_text("\n", strip=True) if body_el else article.get("teaser", "")
    if not body and article.get("teaser"):
        body = article["teaser"]

    published_at: datetime | None = None
    time_el = soup.select_one("time[itemprop=datePublished]")
    if time_el:
        dt_attr = time_el.get("datetime", "").strip()
        if dt_attr:
            try:
                published_at = datetime.fromisoformat(dt_attr)
            except ValueError:
                logger.debug("Could not parse datetime %r", dt_attr)

    return RawItem(
        external_id=_slug_from_url(article["url"]),
        title=article["title"],
        content=body,
        url=article["url"],
        published_at=published_at,
        metadata={
            "connector": "liga_hessen",
            "teaser": article.get("teaser"),
        },
    )


def _listing_only_item(article: dict) -> RawItem:
    return RawItem(
        external_id=_slug_from_url(article["url"]),
        title=article["title"],
        content=article.get("teaser", ""),
        url=article["url"],
        published_at=None,
        metadata={
            "connector": "liga_hessen",
            "teaser": article.get("teaser"),
            "detail_fetch": "failed",
        },
    )


def _slug_from_url(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    slug = path.rsplit("/", 1)[-1] if path else url
    return slug or url
