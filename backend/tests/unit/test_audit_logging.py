from __future__ import annotations

from fastapi import Request

from backend.core.audit_logging import extract_client_info


def _request(*, xff: str | None, client_host: str) -> Request:
    headers = []
    if xff is not None:
        headers.append((b"x-forwarded-for", xff.encode("utf-8")))
    headers.append((b"user-agent", b"pytest-agent"))
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": headers,
        "client": (client_host, 12345),
        "server": ("testserver", 80),
        "scheme": "http",
    }
    return Request(scope)


def test_extract_client_info_ignores_xff_when_proxy_not_trusted(monkeypatch) -> None:
    monkeypatch.setenv("TRUSTED_PROXY_CIDRS", "10.0.0.0/8")

    request = _request(xff="1.2.3.4", client_host="203.0.113.9")
    ip, user_agent = extract_client_info(request)

    assert ip == "203.0.113.9"
    assert user_agent == "pytest-agent"


def test_extract_client_info_uses_xff_when_proxy_is_trusted(monkeypatch) -> None:
    monkeypatch.setenv("TRUSTED_PROXY_CIDRS", "10.0.0.0/8")

    request = _request(xff="1.2.3.4, 5.6.7.8", client_host="10.1.2.3")
    ip, _ = extract_client_info(request)

    assert ip == "1.2.3.4"
