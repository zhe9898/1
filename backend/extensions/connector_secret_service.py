from __future__ import annotations

import os
from collections.abc import Mapping

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from backend.kernel.contracts.security_defaults import DEFAULT_INSECURE_SECRET
from backend.platform.logging.redaction import REDACTED_VALUE, sanitize_sensitive_data
from backend.platform.security.secret_envelope import AesGcmEnvelopeService, SecretKeyMaterial


class ConnectorSecretService:
    ENVELOPE_FORMAT = "zen70.connector.secret.v1"
    CURRENT_KEY_ENV = "CONNECTOR_SECRET_CURRENT_KEY"
    CURRENT_KEY_VERSION_ENV = "CONNECTOR_SECRET_CURRENT_KEY_VERSION"
    PREVIOUS_KEYS_ENV = "CONNECTOR_SECRET_PREVIOUS_KEYS"
    DERIVED_KEY_VERSION = "jwt-derived-v1"

    @classmethod
    def seal_config(
        cls,
        config: Mapping[str, object] | None,
        *,
        tenant_id: str,
        connector_id: str,
        kind: str,
        profile: str,
    ) -> dict[str, object]:
        normalized = cls._normalize_config(config)
        if not normalized:
            return {}
        return AesGcmEnvelopeService.seal_json(
            normalized,
            key_material=cls._current_key_material(),
            associated_data=cls._associated_data(
                tenant_id=tenant_id,
                connector_id=connector_id,
                kind=kind,
                profile=profile,
            ),
            envelope_format=cls.ENVELOPE_FORMAT,
            extra_fields={"masked": sanitize_sensitive_data(normalized, masked_value=REDACTED_VALUE)},
        )

    @classmethod
    def open_config(
        cls,
        stored_config: Mapping[str, object] | None,
        *,
        tenant_id: str,
        connector_id: str,
        kind: str,
        profile: str,
    ) -> dict[str, object]:
        normalized = cls._normalize_config(stored_config)
        if not normalized:
            return {}
        if not cls.is_encrypted_envelope(normalized):
            return normalized
        return AesGcmEnvelopeService.open_json(
            normalized,
            keyring=cls._keyring(),
            associated_data=cls._associated_data(
                tenant_id=tenant_id,
                connector_id=connector_id,
                kind=kind,
                profile=profile,
            ),
            envelope_format=cls.ENVELOPE_FORMAT,
        )

    @classmethod
    def masked_view(cls, stored_config: Mapping[str, object] | None) -> dict[str, object]:
        normalized = cls._normalize_config(stored_config)
        if not normalized:
            return {}
        if cls.is_encrypted_envelope(normalized):
            masked = normalized.get("masked")
            return cls._normalize_config(masked if isinstance(masked, Mapping) else None)
        sanitized = sanitize_sensitive_data(normalized, masked_value=REDACTED_VALUE)
        return sanitized if isinstance(sanitized, dict) else {}

    @classmethod
    def is_encrypted_envelope(cls, value: Mapping[str, object] | None) -> bool:
        return AesGcmEnvelopeService.is_envelope(value, expected_format=cls.ENVELOPE_FORMAT)

    @classmethod
    def export_secret_contract(cls) -> dict[str, object]:
        return {
            "format": cls.ENVELOPE_FORMAT,
            "current_key_env": cls.CURRENT_KEY_ENV,
            "current_key_version_env": cls.CURRENT_KEY_VERSION_ENV,
            "previous_keys_env": cls.PREVIOUS_KEYS_ENV,
            "derived_fallback_version": cls.DERIVED_KEY_VERSION,
            "cipher": "AES-256-GCM",
            "associated_data_fields": ["tenant_id", "connector_id", "kind", "profile"],
            "masked_value": REDACTED_VALUE,
        }

    @classmethod
    def _current_key_material(cls) -> SecretKeyMaterial:
        explicit_key = os.getenv(cls.CURRENT_KEY_ENV, "").strip()
        version = os.getenv(cls.CURRENT_KEY_VERSION_ENV, "v1").strip() or "v1"
        if explicit_key:
            return SecretKeyMaterial(version=version, key_bytes=AesGcmEnvelopeService.decode_secret_key(explicit_key))
        return SecretKeyMaterial(version=cls.DERIVED_KEY_VERSION, key_bytes=cls._derive_fallback_key())

    @classmethod
    def _keyring(cls) -> dict[str, SecretKeyMaterial]:
        current = cls._current_key_material()
        keyring = {current.version: current}
        raw_previous = os.getenv(cls.PREVIOUS_KEYS_ENV, "").strip()
        if not raw_previous:
            return keyring
        for item in raw_previous.split(","):
            version, separator, encoded_key = item.strip().partition("=")
            if separator != "=" or not version.strip() or not encoded_key.strip():
                raise ValueError("CONNECTOR_SECRET_PREVIOUS_KEYS entries must use key_version=base64_or_hex_key")
            keyring[version.strip()] = SecretKeyMaterial(
                version=version.strip(),
                key_bytes=AesGcmEnvelopeService.decode_secret_key(encoded_key.strip()),
            )
        return keyring

    @classmethod
    def _derive_fallback_key(cls) -> bytes:
        seed = (os.getenv("JWT_SECRET_CURRENT") or os.getenv("JWT_SECRET") or "").strip() or DEFAULT_INSECURE_SECRET
        if os.getenv("ZEN70_ENV", "").strip().lower() == "production":
            if seed == DEFAULT_INSECURE_SECRET or len(seed) < 32:
                raise RuntimeError("Connector secret encryption requires CONNECTOR_SECRET_CURRENT_KEY or a production-grade JWT secret")
        return HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"zen70.connector.secret.salt",
            info=b"zen70.connector.secret.envelope",
        ).derive(seed.encode("utf-8"))

    @staticmethod
    def _associated_data(
        *,
        tenant_id: str,
        connector_id: str,
        kind: str,
        profile: str,
    ) -> dict[str, str]:
        return {
            "tenant_id": tenant_id,
            "connector_id": connector_id,
            "kind": kind,
            "profile": profile,
        }

    @staticmethod
    def _normalize_config(config: Mapping[str, object] | None) -> dict[str, object]:
        if not isinstance(config, Mapping):
            return {}
        return dict(config)
