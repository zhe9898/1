from __future__ import annotations

import base64
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


@dataclass(frozen=True, slots=True)
class SecretKeyMaterial:
    version: str
    key_bytes: bytes


class AesGcmEnvelopeService:
    @staticmethod
    def is_envelope(value: Mapping[str, object] | None, *, expected_format: str) -> bool:
        if not isinstance(value, Mapping):
            return False
        return str(value.get("format") or "") == expected_format

    @staticmethod
    def seal_json(
        payload: Mapping[str, object],
        *,
        key_material: SecretKeyMaterial,
        associated_data: Mapping[str, str],
        envelope_format: str,
        extra_fields: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        payload_dict: dict[str, object] = {str(key): value for key, value in payload.items()}
        plaintext = json.dumps(payload_dict, sort_keys=True, separators=(",", ":")).encode("utf-8")
        nonce = os.urandom(12)
        ciphertext = AESGCM(key_material.key_bytes).encrypt(
            nonce,
            plaintext,
            AesGcmEnvelopeService._encode_associated_data(associated_data),
        )
        envelope: dict[str, object] = {
            "format": envelope_format,
            "key_version": key_material.version,
            "nonce_b64": AesGcmEnvelopeService.encode_b64(nonce),
            "ciphertext_b64": AesGcmEnvelopeService.encode_b64(ciphertext),
        }
        if extra_fields:
            envelope.update(dict(extra_fields))
        return envelope

    @staticmethod
    def open_json(
        envelope: Mapping[str, object],
        *,
        keyring: Mapping[str, SecretKeyMaterial],
        associated_data: Mapping[str, str],
        envelope_format: str,
    ) -> dict[str, object]:
        if not AesGcmEnvelopeService.is_envelope(envelope, expected_format=envelope_format):
            raise ValueError(f"Expected secret envelope format '{envelope_format}'")
        key_version = str(envelope.get("key_version") or "").strip()
        key_material = keyring.get(key_version)
        if key_material is None:
            raise ValueError(f"Secret key version '{key_version}' is not available")
        plaintext = AESGCM(key_material.key_bytes).decrypt(
            AesGcmEnvelopeService.decode_b64_field(envelope, "nonce_b64"),
            AesGcmEnvelopeService.decode_b64_field(envelope, "ciphertext_b64"),
            AesGcmEnvelopeService._encode_associated_data(associated_data),
        )
        payload = json.loads(plaintext.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Secret envelope payload must decode to a JSON object")
        return {str(key): value for key, value in payload.items()}

    @staticmethod
    def encode_b64(value: bytes) -> str:
        return base64.b64encode(value).decode("ascii")

    @staticmethod
    def decode_b64(raw: str) -> bytes:
        padding = "=" * (-len(raw) % 4)
        return base64.b64decode(raw + padding, validate=True)

    @staticmethod
    def decode_b64_field(envelope: Mapping[str, object], key: str) -> bytes:
        value = envelope.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Secret envelope field '{key}' is required")
        return AesGcmEnvelopeService.decode_b64(value.strip())

    @staticmethod
    def decode_secret_key(raw: str) -> bytes:
        for decoder in (AesGcmEnvelopeService.decode_b64, bytes.fromhex):
            try:
                key_bytes = decoder(raw)
            except (ValueError, TypeError):
                continue
            if len(key_bytes) == 32:
                return key_bytes
        raise ValueError("Secret keys must decode to exactly 32 bytes (base64 or hex)")

    @staticmethod
    def _encode_associated_data(associated_data: Mapping[str, str]) -> bytes:
        return json.dumps(dict(associated_data), sort_keys=True, separators=(",", ":")).encode("utf-8")
