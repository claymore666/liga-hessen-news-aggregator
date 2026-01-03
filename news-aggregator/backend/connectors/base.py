"""Base connector interface for all source connectors."""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, ClassVar

from pydantic import BaseModel, Field


class RawItem(BaseModel):
    """Normalized item format returned by all connectors."""

    external_id: str = Field(..., description="Unique ID from source")
    title: str = Field(..., description="Item title")
    content: str = Field(default="", description="Full text content")
    url: str = Field(..., description="Source URL")
    author: str | None = Field(default=None, description="Author name")
    published_at: datetime | None = Field(default=None, description="Publication date")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Connector-specific data")


class BaseConnector(ABC):
    """Abstract base class for all connectors.

    Each connector must:
    1. Define class attributes: connector_type, display_name, description, config_schema
    2. Implement fetch() to retrieve items from the source
    3. Implement validate() to test the configuration
    """

    # Connector metadata (override in subclass)
    connector_type: ClassVar[str]  # e.g., "rss", "twitter"
    display_name: ClassVar[str]  # e.g., "RSS Feed"
    description: ClassVar[str]  # Shown in UI
    config_schema: ClassVar[type[BaseModel]]  # Pydantic model for config

    @abstractmethod
    async def fetch(self, config: BaseModel) -> list[RawItem]:
        """Fetch items from the configured source.

        Args:
            config: Connector-specific configuration

        Returns:
            List of normalized RawItem objects
        """
        pass

    @abstractmethod
    async def validate(self, config: BaseModel) -> tuple[bool, str]:
        """Validate the configuration (e.g., test connection).

        Args:
            config: Connector-specific configuration

        Returns:
            Tuple of (success, message)
        """
        pass

    @classmethod
    def get_config_schema_json(cls) -> dict[str, Any]:
        """Return JSON Schema for frontend form generation."""
        return cls.config_schema.model_json_schema()
