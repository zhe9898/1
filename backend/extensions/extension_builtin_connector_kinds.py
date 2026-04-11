"""Builtin published connector kind catalog."""

from __future__ import annotations

from backend.extensions.connector_kind_registry import HttpConnectorConfig, MqttConnectorConfig, WebhookConnectorConfig

from .extension_contracts import ConnectorKindSpec


def build_core_connector_kinds() -> tuple[ConnectorKindSpec, ...]:
    return (
        ConnectorKindSpec("http", config_schema=HttpConnectorConfig, description="HTTP connector runtime."),
        ConnectorKindSpec("mqtt", config_schema=MqttConnectorConfig, description="MQTT connector runtime."),
        ConnectorKindSpec("webhook", config_schema=WebhookConnectorConfig, description="Webhook connector runtime."),
    )
