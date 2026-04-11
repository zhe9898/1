from __future__ import annotations

from fastapi import Response

_IDENTITY_CACHE_CONTROL = "no-store, private"


def _merge_vary(existing: str | None, value: str) -> str:
    parts = [item.strip() for item in (existing or "").split(",") if item.strip()]
    lowered = {item.lower() for item in parts}
    if value.lower() not in lowered:
        parts.append(value)
    return ", ".join(parts)


def apply_identity_no_store_headers(response: Response) -> None:
    """Mark identity-scoped responses as non-cacheable across browsers and proxies."""
    response.headers["Cache-Control"] = _IDENTITY_CACHE_CONTROL
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["Vary"] = _merge_vary(response.headers.get("Vary"), "Cookie")
