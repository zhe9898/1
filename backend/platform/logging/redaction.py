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
_JSON_SECRET_PATTERN = re.compile(
    r'(?i)("(?:password|passwd|pwd|secret|token|access_token|refresh_token|api_key|apikey|client_secret)"\s*:\s*")[^"]+(")'
)
_INLINE_SECRET_PATTERN = re.compile(r"(?i)\b(password|passwd|pwd|secret|token|access_token|refresh_token|api_key|apikey|client_secret)\b\s*([=:])\s*([^\s,;]+)")
_AUTHORIZATION_PATTERN = re.compile(r"(?i)\bauthorization\b\s*:\s*bearer\s+[^\s,;]+")
_EMAIL_PATTERN = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")


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


def redact_sensitive_text(value: str) -> str:
    redacted = _JSON_SECRET_PATTERN.sub(r"\1[REDACTED]\2", value)
    redacted = _AUTHORIZATION_PATTERN.sub("authorization: Bearer [REDACTED]", redacted)
    redacted = _INLINE_SECRET_PATTERN.sub(r"\1\2[REDACTED]", redacted)
    redacted = _EMAIL_PATTERN.sub("[REDACTED_EMAIL]", redacted)
    return redacted
