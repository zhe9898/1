from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any


REDACTED_VALUE = "********"
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_SENSITIVE_KEY_TOKENS = frozenset(
    {
        "accesskey",
        "accesstoken",
        "apikey",
        "authorization",
        "challenge",
        "clientsecret",
        "credential",
        "passphrase",
        "password",
        "pinhash",
        "privatekey",
        "refreshkey",
        "refreshtoken",
        "secret",
        "signingkey",
        "token",
        "webhooksecret",
    }
)


def normalize_sensitive_key(key: object) -> str:
    raw = str(key or "").strip().lower()
    return _NON_ALNUM_RE.sub("", raw)


def is_sensitive_key(key: object) -> bool:
    normalized = normalize_sensitive_key(key)
    if not normalized:
        return False
    return any(token in normalized for token in _SENSITIVE_KEY_TOKENS)


def mask_secret_value(_value: object, *, masked_value: str = REDACTED_VALUE) -> str:
    return masked_value


def sanitize_sensitive_data(value: Any, *, masked_value: str = REDACTED_VALUE) -> Any:
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for key, child in value.items():
            if is_sensitive_key(key):
                sanitized[str(key)] = mask_secret_value(child, masked_value=masked_value)
            else:
                sanitized[str(key)] = sanitize_sensitive_data(child, masked_value=masked_value)
        return sanitized
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [sanitize_sensitive_data(item, masked_value=masked_value) for item in value]
    return value


def contains_sensitive_data(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if is_sensitive_key(key) or contains_sensitive_data(child):
                return True
        return False
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(contains_sensitive_data(item) for item in value)
    return False
