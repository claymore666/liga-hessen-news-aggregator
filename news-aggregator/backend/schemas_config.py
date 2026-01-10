"""Pydantic schemas for configuration export/import API."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from models import ConnectorType, Priority, RuleType

# Version for export format compatibility
CONFIG_FORMAT_VERSION = "1.0"


class BaseSchema(BaseModel):
    """Base schema with common configuration."""

    model_config = ConfigDict(from_attributes=True)


# === Channel Config Export ===


class ChannelConfigExport(BaseModel):
    """Channel data for export (excludes operational metadata)."""

    name: str | None = None
    connector_type: str
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    fetch_interval_minutes: int = 30
    # Note: last_fetch_at, last_error, source_identifier excluded (operational metadata)


# === Source Config Export ===


class SourceConfigExport(BaseModel):
    """Source data for export (excludes operational metadata)."""

    name: str
    description: str | None = None
    is_stakeholder: bool = False
    enabled: bool = True
    channels: list[ChannelConfigExport] = Field(default_factory=list)
    # Note: id, created_at, updated_at excluded


# === Rule Config Export ===


class RuleConfigExport(BaseModel):
    """Rule data for export."""

    name: str
    description: str | None = None
    rule_type: str
    pattern: str
    priority_boost: int = 0
    target_priority: str | None = None
    enabled: bool = True
    order: int = 0
    # Note: id, created_at, updated_at, matches excluded


# === Setting Config Export ===


class SettingConfigExport(BaseModel):
    """Setting data for export."""

    key: str
    value: Any
    description: str | None = None


# === Full Config Export ===


class ConfigExport(BaseModel):
    """Complete configuration export format."""

    version: str = CONFIG_FORMAT_VERSION
    instance_identifier: str | None = None
    exported_at: datetime
    sources: list[SourceConfigExport] = Field(default_factory=list)
    rules: list[RuleConfigExport] = Field(default_factory=list)
    settings: list[SettingConfigExport] = Field(default_factory=list)


# === Validation Types ===


class ValidationError(BaseModel):
    """Single validation error or warning."""

    path: str  # e.g., "sources[0].channels[1].config"
    message: str
    severity: Literal["error", "warning"] = "error"


class ConfigValidationResult(BaseModel):
    """Result of configuration validation."""

    valid: bool
    errors: list[ValidationError] = Field(default_factory=list)
    warnings: list[ValidationError] = Field(default_factory=list)
    summary: dict[str, int] = Field(default_factory=dict)


# === Import Types ===


class ConfigImportResult(BaseModel):
    """Result of configuration import."""

    success: bool
    message: str
    imported: dict[str, int] = Field(default_factory=dict)  # counts of imported items
    skipped: dict[str, int] = Field(default_factory=dict)   # counts of skipped items (merge mode)
    errors: list[str] = Field(default_factory=list)


# === Sensitive Data Handling ===

# Keys in channel config that should be redacted in exports
SENSITIVE_CONFIG_KEYS = {
    "api_key",
    "api_secret",
    "password",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "cookie",
    "cookies",
    "auth",
    "authorization",
    "credentials",
    "private_key",
}

# Settings keys that should never be exported
SENSITIVE_SETTING_KEYS = {
    "api_key",
    "api_secret",
    "password",
    "secret",
    "token",
    "openrouter_api_key",
}


def redact_sensitive_config(config: dict[str, Any]) -> dict[str, Any]:
    """Redact sensitive values from channel config.

    Args:
        config: Channel configuration dictionary

    Returns:
        Config with sensitive values replaced with "<REDACTED>"
    """
    redacted = {}
    for key, value in config.items():
        key_lower = key.lower()
        is_sensitive = any(s in key_lower for s in SENSITIVE_CONFIG_KEYS)
        if is_sensitive and value:
            redacted[key] = "<REDACTED>"
        elif isinstance(value, dict):
            redacted[key] = redact_sensitive_config(value)
        else:
            redacted[key] = value
    return redacted


def is_sensitive_setting(key: str) -> bool:
    """Check if a setting key is sensitive.

    Args:
        key: Setting key to check

    Returns:
        True if the setting should not be exported
    """
    key_lower = key.lower()
    return any(s in key_lower for s in SENSITIVE_SETTING_KEYS)
