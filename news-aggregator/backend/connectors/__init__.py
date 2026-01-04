"""Connector system for fetching items from various sources.

This module provides a pluggable connector system for fetching news
from different sources (RSS, social media, websites, documents).

Usage:
    from connectors import ConnectorRegistry, RawItem

    # Get a connector class
    connector_cls = ConnectorRegistry.get("rss")

    # Create instance and fetch items
    connector = connector_cls()
    config = connector_cls.config_schema(url="https://example.com/feed.xml")
    items = await connector.fetch(config)

    # List all available connectors
    all_connectors = ConnectorRegistry.list_all()
"""

from .base import BaseConnector, RawItem
from .registry import ConnectorRegistry

# Import all connectors to register them
from .rss import RSSConnector
from .html import HTMLConnector
from .bluesky import BlueskyConnector
from .twitter import TwitterConnector
from .pdf import PDFConnector
from .mastodon import MastodonConnector
from .x_scraper import XScraperConnector
from .instagram import InstagramConnector
from .telegram import TelegramConnector

__all__ = [
    # Base classes
    "BaseConnector",
    "RawItem",
    "ConnectorRegistry",
    # Connectors
    "RSSConnector",
    "HTMLConnector",
    "BlueskyConnector",
    "TwitterConnector",
    "PDFConnector",
    "MastodonConnector",
    "XScraperConnector",
    "InstagramConnector",
    "TelegramConnector",
]
