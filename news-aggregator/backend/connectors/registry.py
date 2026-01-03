"""Central registry for all available connectors."""

from typing import Any

from .base import BaseConnector


class ConnectorRegistry:
    """Central registry for all available connectors.

    Usage:
        @ConnectorRegistry.register
        class MyConnector(BaseConnector):
            connector_type = "my_connector"
            ...

        # Get a connector class
        connector_cls = ConnectorRegistry.get("my_connector")

        # List all connectors
        all_connectors = ConnectorRegistry.list_all()
    """

    _connectors: dict[str, type[BaseConnector]] = {}

    @classmethod
    def register(cls, connector_class: type[BaseConnector]) -> type[BaseConnector]:
        """Decorator to register a connector.

        Args:
            connector_class: The connector class to register

        Returns:
            The same connector class (for use as decorator)
        """
        cls._connectors[connector_class.connector_type] = connector_class
        return connector_class

    @classmethod
    def get(cls, connector_type: str) -> type[BaseConnector]:
        """Get connector class by type.

        Args:
            connector_type: The connector type identifier

        Returns:
            The connector class

        Raises:
            ValueError: If connector type is not registered
        """
        if connector_type not in cls._connectors:
            available = ", ".join(cls._connectors.keys())
            raise ValueError(
                f"Unknown connector: {connector_type}. Available: {available}"
            )
        return cls._connectors[connector_type]

    @classmethod
    def list_all(cls) -> list[dict[str, Any]]:
        """List all registered connectors with metadata.

        Returns:
            List of dicts with connector info (type, name, description, config_schema)
        """
        return [
            {
                "type": c.connector_type,
                "name": c.display_name,
                "description": c.description,
                "config_schema": c.config_schema.model_json_schema(),
            }
            for c in cls._connectors.values()
        ]

    @classmethod
    def get_types(cls) -> list[str]:
        """Get list of all registered connector types.

        Returns:
            List of connector type identifiers
        """
        return list(cls._connectors.keys())

    @classmethod
    def is_registered(cls, connector_type: str) -> bool:
        """Check if a connector type is registered.

        Args:
            connector_type: The connector type to check

        Returns:
            True if registered, False otherwise
        """
        return connector_type in cls._connectors
