from __future__ import annotations

import pytest
from cryptography.exceptions import InvalidTag

from backend.extensions.connector_secret_service import ConnectorSecretService


def test_connector_secret_service_roundtrip_and_masked_view(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET_CURRENT", "test-secret-material-that-is-long-enough-1234567890")

    sealed = ConnectorSecretService.seal_config(
        {
            "headers": {"x-api-key": "top-secret"},
            "nested": [{"client_secret": "nested-secret"}],
            "timeout_ms": 1500,
        },
        tenant_id="tenant-a",
        connector_id="connector-a",
        kind="http",
        profile="manual",
    )

    assert sealed["format"] == ConnectorSecretService.ENVELOPE_FORMAT
    assert "ciphertext_b64" in sealed
    assert "nonce_b64" in sealed
    assert sealed["masked"] == {
        "headers": {"x-api-key": "********"},
        "nested": [{"client_secret": "********"}],
        "timeout_ms": 1500,
    }

    opened = ConnectorSecretService.open_config(
        sealed,
        tenant_id="tenant-a",
        connector_id="connector-a",
        kind="http",
        profile="manual",
    )
    assert opened == {
        "headers": {"x-api-key": "top-secret"},
        "nested": [{"client_secret": "nested-secret"}],
        "timeout_ms": 1500,
    }


def test_connector_secret_service_binds_associated_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET_CURRENT", "test-secret-material-that-is-long-enough-1234567890")

    sealed = ConnectorSecretService.seal_config(
        {"token": "secret"},
        tenant_id="tenant-a",
        connector_id="connector-a",
        kind="http",
        profile="manual",
    )

    with pytest.raises(InvalidTag):
        ConnectorSecretService.open_config(
            sealed,
            tenant_id="tenant-b",
            connector_id="connector-a",
            kind="http",
            profile="manual",
        )


def test_connector_secret_service_masks_plaintext_legacy_config() -> None:
    masked = ConnectorSecretService.masked_view(
        {
            "headers": {"x-api-key": "top-secret"},
            "refresh_token": "refresh-secret",
            "timeout_ms": 1500,
        }
    )

    assert masked == {
        "headers": {"x-api-key": "********"},
        "refresh_token": "********",
        "timeout_ms": 1500,
    }


def test_connector_secret_contract_declares_envelope_and_masking() -> None:
    contract = ConnectorSecretService.export_secret_contract()

    assert contract["format"] == ConnectorSecretService.ENVELOPE_FORMAT
    assert contract["cipher"] == "AES-256-GCM"
    assert contract["associated_data_fields"] == ["tenant_id", "connector_id", "kind", "profile"]
    assert contract["masked_value"] == "********"
