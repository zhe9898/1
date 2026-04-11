from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from backend.platform.http.webhooks import post_public_webhook, post_public_webhook_async


def _response(status_code: int) -> httpx.Response:
    request = httpx.Request("POST", "https://alerts.example/webhook")
    return httpx.Response(status_code, request=request)


def test_post_public_webhook_rejects_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    logger = MagicMock()
    monkeypatch.setattr("backend.platform.http.webhooks.httpx.post", lambda *args, **kwargs: _response(500))

    result = post_public_webhook(
        "https://alerts.example/webhook",
        {"level": "critical"},
        timeout=5.0,
        logger=logger,
        context="alert_delivery",
    )

    assert result is False
    logger.error.assert_called()


@pytest.mark.anyio
async def test_post_public_webhook_async_rejects_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    logger = MagicMock()
    client = AsyncMock()
    client.post.return_value = _response(500)
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False
    monkeypatch.setattr("backend.platform.http.webhooks.httpx.AsyncClient", lambda *args, **kwargs: client)

    result = await post_public_webhook_async(
        "https://alerts.example/webhook",
        {"level": "critical"},
        timeout=5.0,
        logger=logger,
        context="alert_delivery",
    )

    assert result is False
    logger.error.assert_called()
