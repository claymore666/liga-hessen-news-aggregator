"""API endpoints for connector information."""

from fastapi import APIRouter

from models import ConnectorType
from schemas import ConnectorInfo

router = APIRouter()

# Connector metadata - will be populated by connector registry later
CONNECTOR_INFO: dict[ConnectorType, dict] = {
    ConnectorType.RSS: {
        "name": "RSS Feed",
        "description": "Subscribe to any RSS or Atom feed",
        "config_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "format": "uri", "description": "Feed URL"},
                "custom_title": {"type": "string", "description": "Custom name for the feed"},
            },
            "required": ["url"],
        },
    },
    ConnectorType.HTML: {
        "name": "HTML Scraper",
        "description": "Scrape news from websites using CSS selectors",
        "config_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "format": "uri", "description": "Page URL"},
                "item_selector": {"type": "string", "description": "CSS selector for items"},
                "title_selector": {"type": "string", "description": "CSS selector for title"},
                "content_selector": {"type": "string", "description": "CSS selector for content"},
                "link_selector": {"type": "string", "description": "CSS selector for link"},
            },
            "required": ["url", "item_selector"],
        },
    },
    ConnectorType.BLUESKY: {
        "name": "Bluesky",
        "description": "Follow Bluesky accounts via RSS",
        "config_schema": {
            "type": "object",
            "properties": {
                "handle": {
                    "type": "string",
                    "description": "Bluesky handle (e.g., @user.bsky.social)",
                },
            },
            "required": ["handle"],
        },
    },
    ConnectorType.TWITTER: {
        "name": "Twitter/X",
        "description": "Follow Twitter accounts via Nitter RSS proxy",
        "config_schema": {
            "type": "object",
            "properties": {
                "username": {"type": "string", "description": "Twitter username"},
                "include_retweets": {"type": "boolean", "default": True},
                "include_replies": {"type": "boolean", "default": False},
            },
            "required": ["username"],
        },
    },
    ConnectorType.MASTODON: {
        "name": "Mastodon",
        "description": "Follow Mastodon/Fediverse accounts",
        "config_schema": {
            "type": "object",
            "properties": {
                "handle": {
                    "type": "string",
                    "description": "Mastodon handle (e.g., @user@mastodon.social)",
                },
                "use_api": {"type": "boolean", "default": False},
                "api_token": {"type": "string", "description": "API token (optional)"},
            },
            "required": ["handle"],
        },
    },
    ConnectorType.LINKEDIN: {
        "name": "LinkedIn",
        "description": "Monitor LinkedIn profiles (limited availability)",
        "config_schema": {
            "type": "object",
            "properties": {
                "profile_url": {"type": "string", "format": "uri", "description": "Profile URL"},
            },
            "required": ["profile_url"],
        },
    },
    ConnectorType.PDF: {
        "name": "PDF Document",
        "description": "Extract text from PDF documents",
        "config_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "format": "uri", "description": "PDF URL"},
                "is_direct_link": {"type": "boolean", "default": True},
                "link_selector": {"type": "string", "description": "CSS selector for PDF links"},
            },
            "required": ["url"],
        },
    },
}


@router.get("/connectors", response_model=list[ConnectorInfo])
async def list_connectors() -> list[ConnectorInfo]:
    """List all available connector types."""
    return [
        ConnectorInfo(
            type=connector_type,
            name=info["name"],
            description=info["description"],
            config_schema=info["config_schema"],
        )
        for connector_type, info in CONNECTOR_INFO.items()
    ]


@router.get("/connectors/{connector_type}", response_model=ConnectorInfo)
async def get_connector(connector_type: ConnectorType) -> ConnectorInfo:
    """Get information about a specific connector type."""
    info = CONNECTOR_INFO[connector_type]
    return ConnectorInfo(
        type=connector_type,
        name=info["name"],
        description=info["description"],
        config_schema=info["config_schema"],
    )
