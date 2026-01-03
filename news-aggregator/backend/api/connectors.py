"""API endpoints for connector information."""

from typing import Any

from fastapi import APIRouter, HTTPException

from connectors import ConnectorRegistry
from models import ConnectorType
from schemas import ConnectorInfo, ValidationResult

router = APIRouter()


def _connector_type_to_enum(connector_type: str) -> ConnectorType:
    """Convert connector type string to enum value."""
    try:
        return ConnectorType(connector_type)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown connector type: {connector_type}",
        )


@router.get("/connectors", response_model=list[ConnectorInfo])
async def list_connectors() -> list[ConnectorInfo]:
    """List all available connector types."""
    connectors = ConnectorRegistry.list_all()
    return [
        ConnectorInfo(
            type=_connector_type_to_enum(c["type"]),
            name=c["name"],
            description=c["description"],
            config_schema=c["config_schema"],
        )
        for c in connectors
    ]


@router.get("/connectors/{connector_type}", response_model=ConnectorInfo)
async def get_connector(connector_type: ConnectorType) -> ConnectorInfo:
    """Get information about a specific connector type."""
    try:
        connector_cls = ConnectorRegistry.get(connector_type.value)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail=f"Connector not found: {connector_type.value}",
        )

    return ConnectorInfo(
        type=connector_type,
        name=connector_cls.display_name,
        description=connector_cls.description,
        config_schema=connector_cls.get_config_schema_json(),
    )


@router.post("/connectors/{connector_type}/validate", response_model=ValidationResult)
async def validate_connector_config(
    connector_type: ConnectorType,
    config: dict[str, Any],
) -> ValidationResult:
    """Validate a connector configuration.

    Tests that the configuration is valid and can connect to the source.
    """
    try:
        connector_cls = ConnectorRegistry.get(connector_type.value)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail=f"Connector not found: {connector_type.value}",
        )

    # Validate config against schema
    try:
        validated_config = connector_cls.config_schema(**config)
    except Exception as e:
        return ValidationResult(valid=False, message=f"Invalid configuration: {str(e)}")

    # Test the connection
    connector = connector_cls()
    valid, message = await connector.validate(validated_config)

    return ValidationResult(valid=valid, message=message)
