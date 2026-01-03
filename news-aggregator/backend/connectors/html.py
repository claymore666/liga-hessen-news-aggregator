"""HTML scraper connector for website news extraction."""

import hashlib
from datetime import datetime
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field, HttpUrl

from .base import BaseConnector, RawItem
from .registry import ConnectorRegistry


class HTMLConfig(BaseModel):
    """Configuration for HTML scraper connector."""

    url: HttpUrl = Field(..., description="Page URL to scrape")
    item_selector: str = Field(..., description="CSS selector for news items")
    title_selector: str = Field(
        default="h2, h3, a", description="CSS selector for title (relative to item)"
    )
    content_selector: str | None = Field(
        default=None, description="CSS selector for content (relative to item)"
    )
    link_selector: str | None = Field(
        default=None, description="CSS selector for link (relative to item)"
    )
    date_selector: str | None = Field(
        default=None, description="CSS selector for date (relative to item)"
    )
    date_format: str | None = Field(
        default=None, description="strptime format for date parsing"
    )


@ConnectorRegistry.register
class HTMLConnector(BaseConnector):
    """HTML scraper connector.

    Extracts news items from any website using CSS selectors.
    """

    connector_type = "html"
    display_name = "HTML Scraper"
    description = "Scrape news from websites using CSS selectors"
    config_schema = HTMLConfig

    async def fetch(self, config: HTMLConfig) -> list[RawItem]:
        """Fetch items by scraping HTML page.

        Args:
            config: HTML scraper configuration

        Returns:
            List of RawItem objects from the page
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                str(config.url),
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                },
                follow_redirects=True,
            )
            response.raise_for_status()

        soup = BeautifulSoup(response.text, "lxml")
        items = []
        base_url = str(config.url)

        for element in soup.select(config.item_selector):
            # Extract title
            title_el = element.select_one(config.title_selector)
            if not title_el:
                continue

            title = title_el.get_text(strip=True)
            if not title:
                continue

            # Generate external ID from title hash
            external_id = hashlib.md5(title.encode()).hexdigest()[:16]

            # Extract link
            link = base_url
            if config.link_selector:
                link_el = element.select_one(config.link_selector)
                if link_el:
                    href = link_el.get("href")
                    if href:
                        link = urljoin(base_url, href)
            elif title_el.get("href"):
                link = urljoin(base_url, title_el["href"])
            elif title_el.name == "a":
                link = urljoin(base_url, title_el.get("href", ""))

            # Extract content
            content = ""
            if config.content_selector:
                content_el = element.select_one(config.content_selector)
                if content_el:
                    content = content_el.get_text(strip=True)

            # Extract date
            published_at = None
            if config.date_selector and config.date_format:
                date_el = element.select_one(config.date_selector)
                if date_el:
                    date_text = date_el.get_text(strip=True)
                    try:
                        published_at = datetime.strptime(date_text, config.date_format)
                    except ValueError:
                        pass

            items.append(
                RawItem(
                    external_id=external_id,
                    title=title,
                    content=content,
                    url=link,
                    published_at=published_at or datetime.now(),
                    metadata={
                        "source_url": base_url,
                        "connector": "html",
                    },
                )
            )

        return items

    async def validate(self, config: HTMLConfig) -> tuple[bool, str]:
        """Validate HTML scraper configuration.

        Args:
            config: Configuration to validate

        Returns:
            Tuple of (success, message)
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    str(config.url),
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                    },
                    follow_redirects=True,
                )
                response.raise_for_status()

            soup = BeautifulSoup(response.text, "lxml")
            items = soup.select(config.item_selector)

            if not items:
                return False, f"No items found with selector: {config.item_selector}"

            return True, f"Found {len(items)} items"

        except httpx.TimeoutException:
            return False, "Connection timeout"
        except httpx.HTTPStatusError as e:
            return False, f"HTTP error: {e.response.status_code}"
        except Exception as e:
            return False, f"Error: {str(e)}"
