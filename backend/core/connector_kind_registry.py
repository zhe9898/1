"""Connector kind registry and config schema validation.

Provides schema registration and validation for connector configurations
to ensure type safety and prevent business logic coupling in the platform kernel.
"""

from __future__ import annotations

from typing import Any, cast

from pydantic import BaseModel, ValidationError

# ============================================================================
# Connector Kind Registry
# ============================================================================

# Registry mapping connector kind to config schema validator
_CONNECTOR_KIND_REGISTRY: dict[str, type[BaseModel]] = {}

# Registry mapping connector kind to extension/discovery metadata
_CONNECTOR_KIND_METADATA_REGISTRY: dict[str, dict[str, Any]] = {}


def register_connector_kind(
    kind: str,
    *,
    config_schema: type[BaseModel] | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Register a connector kind with config schema.

    Args:
        kind: Connector kind identifier (e.g., "http", "mqtt", "webhook")
        config_schema: Pydantic model for config validation

    Examples:
        >>> class HttpConnectorConfig(BaseModel):
        ...     url: str
        ...     method: str = "POST"
        >>> register_connector_kind("http", config_schema=HttpConnectorConfig)
    """
    if config_schema is not None:
        _CONNECTOR_KIND_REGISTRY[kind] = config_schema

    if metadata is not None:
        _CONNECTOR_KIND_METADATA_REGISTRY[kind] = dict(metadata)
    else:
        _CONNECTOR_KIND_METADATA_REGISTRY.setdefault(kind, {"source": "core"})


def unregister_connector_kind(kind: str) -> None:
    """Unregister a connector kind.

    Args:
        kind: Connector kind identifier
    """
    _CONNECTOR_KIND_REGISTRY.pop(kind, None)
    _CONNECTOR_KIND_METADATA_REGISTRY.pop(kind, None)


def is_connector_kind_registered(kind: str) -> bool:
    """Check if a connector kind is registered.

    Args:
        kind: Connector kind identifier

    Returns:
        True if registered, False otherwise
    """
    return kind in _CONNECTOR_KIND_REGISTRY or kind in _CONNECTOR_KIND_METADATA_REGISTRY


def get_registered_connector_kinds() -> list[str]:
    """Get list of all registered connector kinds.

    Returns:
        List of connector kind identifiers
    """
    return sorted(set(_CONNECTOR_KIND_REGISTRY.keys()) | set(_CONNECTOR_KIND_METADATA_REGISTRY.keys()))


def validate_connector_config(kind: str, config: dict[str, Any]) -> dict[str, Any]:
    """Validate connector config against registered schema.

    Args:
        kind: Connector kind identifier
        config: Connector config dictionary

    Returns:
        Validated config dictionary

    Raises:
        ValueError: If validation fails or kind not registered

    Examples:
        >>> register_connector_kind("http", config_schema=HttpConnectorConfig)
        >>> validate_connector_config("http", {"url": "https://api.example.com"})
        {'url': 'https://api.example.com', 'method': 'POST'}
    """
    schema = _CONNECTOR_KIND_REGISTRY.get(kind)
    if schema is None:
        # No schema registered - allow any config (backward compatibility)
        return config

    try:
        validated = schema(**config)
        return cast(dict[str, Any], validated.model_dump(mode="python"))
    except ValidationError as e:
        error_details = e.errors()
        raise ValueError(f"Connector config validation failed for kind '{kind}': " f"{len(error_details)} error(s) - {error_details[0]['msg']}") from e


# ============================================================================
# Built-in Connector Kinds
# ============================================================================


class HttpConnectorConfig(BaseModel):
    """Config schema for http connector kind."""

    url: str
    method: str = "POST"
    headers: dict[str, str] = {}
    timeout: int = 30
    verify_ssl: bool = True


class MqttConnectorConfig(BaseModel):
    """Config schema for mqtt connector kind."""

    broker: str
    port: int = 1883
    topic: str
    qos: int = 1
    username: str | None = None
    password: str | None = None
    client_id: str | None = None


class WebhookConnectorConfig(BaseModel):
    """Config schema for webhook connector kind."""

    url: str
    method: str = "POST"
    headers: dict[str, str] = {}
    secret: str | None = None
    timeout: int = 30


# Register built-in connector kinds
register_connector_kind("http", config_schema=HttpConnectorConfig)
register_connector_kind("mqtt", config_schema=MqttConnectorConfig)
register_connector_kind("webhook", config_schema=WebhookConnectorConfig)


# ============================================================================
# Connector Kind Discovery
# ============================================================================


def get_connector_kind_info(kind: str) -> dict[str, Any]:
    """Get information about a registered connector kind.

    Args:
        kind: Connector kind identifier

    Returns:
        Dictionary with kind information
    """
    config_schema = _CONNECTOR_KIND_REGISTRY.get(kind)
    metadata = dict(_CONNECTOR_KIND_METADATA_REGISTRY.get(kind, {}))

    return {
        "kind": kind,
        "has_config_schema": config_schema is not None,
        "config_schema": config_schema.model_json_schema() if config_schema else None,
        "metadata": metadata,
    }


def list_connector_kinds() -> list[dict[str, Any]]:
    """List all registered connector kinds with their schemas.

    Returns:
        List of connector kind information dictionaries
    """
    return [get_connector_kind_info(kind) for kind in get_registered_connector_kinds()]
