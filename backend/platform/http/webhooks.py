from __future__ import annotations

import hashlib
import hmac
import logging
import time
from collections.abc import Mapping

import httpx

from backend.platform.security.normalization import normalize_public_network_url


def normalize_sha256_signature(value: str, *, field_name: str) -> str:
    normalized = str(value or "").strip()
    if normalized.lower().startswith("sha256="):
        normalized = normalized.split("=", 1)[1].strip()
    if len(normalized) != 64 or any(char not in "0123456789abcdefABCDEF" for char in normalized):
        raise ValueError(f"{field_name} must be a sha256 signature")
    return normalized.lower()


def verify_timestamped_hmac_sha256(
    *,
    secret: str,
    body: bytes,
    signature: str,
    timestamp: str,
    tolerance_seconds: int,
    now: int | None = None,
) -> None:
    normalized_secret = str(secret or "").strip()
    if not normalized_secret:
        raise ValueError("secret must not be empty")

    try:
        timestamp_value = int(str(timestamp or "").strip())
    except (TypeError, ValueError) as exc:
        raise ValueError("timestamp must be a unix timestamp") from exc

    current_timestamp = now if now is not None else int(time.time())
    if timestamp_value > current_timestamp + 30:
        raise ValueError("timestamp is too far in the future")
    if current_timestamp - timestamp_value > max(tolerance_seconds, 1):
        raise ValueError("signature timestamp has expired")

    normalized_signature = normalize_sha256_signature(signature, field_name="signature")
    signed_payload = str(timestamp_value).encode("utf-8") + b"." + body
    expected_signature = hmac.new(
        normalized_secret.encode("utf-8"),
        signed_payload,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(normalized_signature, expected_signature):
        raise ValueError("signature mismatch")


def post_public_webhook(
    url: str,
    payload: Mapping[str, object],
    *,
    timeout: float,
    logger: logging.Logger,
    context: str,
) -> bool:
    try:
        normalized_url = normalize_public_network_url(url, field_name="webhook_url", allowed_schemes={"http", "https"})
    except ValueError as exc:
        logger.error("%s_invalid_webhook_url: %s", context, exc)
        return False
    try:
        httpx.post(normalized_url, json=dict(payload), timeout=timeout)
        return True
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
        logger.error("%s_webhook_delivery_failed: %s", context, exc)
        return False


async def post_public_webhook_async(
    url: str,
    payload: Mapping[str, object],
    *,
    timeout: float,
    logger: logging.Logger,
    context: str,
    headers: Mapping[str, str] | None = None,
) -> bool:
    try:
        normalized_url = normalize_public_network_url(url, field_name="webhook_url", allowed_schemes={"http", "https"})
    except ValueError as exc:
        logger.error("%s_invalid_webhook_url: %s", context, exc)
        return False
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            await client.post(normalized_url, json=dict(payload), headers=dict(headers or {}))
        return True
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
        logger.error("%s_webhook_delivery_failed: %s", context, exc)
        return False
