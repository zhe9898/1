from __future__ import annotations

from unittest.mock import MagicMock

from backend.control_plane.adapters.auth_cookies import AUTH_COOKIE_NAME, clear_auth_cookie, get_auth_cookie_token, set_auth_cookie
from backend.control_plane.auth.cookie_policy import (
    COOKIE_DOMAIN,
    COOKIE_PATH,
    COOKIE_SAMESITE,
    COOKIE_SECURE,
    clear_http_only_cookie,
    read_request_cookie,
    set_http_only_cookie,
)
from tools.cookie_boundary_guard import cookie_boundary_violations


def test_read_request_cookie_trims_and_rejects_empty_values() -> None:
    request = MagicMock()
    request.cookies = {"demo": "  cookie-value  ", "blank": "   "}

    assert read_request_cookie(request, "demo") == "cookie-value"
    assert read_request_cookie(request, "blank") is None
    assert read_request_cookie(request, "missing") is None


def test_set_http_only_cookie_enforces_shared_policy() -> None:
    response = MagicMock()

    set_http_only_cookie(response, key="demo", value="value", max_age_seconds=0)

    response.set_cookie.assert_called_once_with(
        key="demo",
        value="value",
        max_age=1,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
        path=COOKIE_PATH,
        domain=COOKIE_DOMAIN,
    )


def test_clear_http_only_cookie_enforces_shared_policy() -> None:
    response = MagicMock()

    clear_http_only_cookie(response, key="demo")

    response.delete_cookie.assert_called_once_with(
        key="demo",
        path=COOKIE_PATH,
        domain=COOKIE_DOMAIN,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
    )


def test_auth_cookie_helpers_delegate_to_shared_cookie_policy() -> None:
    request = MagicMock()
    request.cookies = {AUTH_COOKIE_NAME: " token "}
    response = MagicMock()

    assert get_auth_cookie_token(request) == "token"

    set_auth_cookie(response, "token-value", max_age_seconds=120)
    clear_auth_cookie(response)

    response.set_cookie.assert_called_once()
    response.delete_cookie.assert_called_once()


def test_cookie_boundary_guard_rejects_raw_cookie_access(tmp_path) -> None:
    source_path = tmp_path / "backend" / "control_plane" / "adapters" / "demo.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(
        """
from __future__ import annotations


def bind(request, response) -> None:
    request.cookies.get("token")
    response.set_cookie("token", "value")
""".strip(),
        encoding="utf-8",
    )

    violations = cookie_boundary_violations(repo_root=tmp_path)

    assert sorted(violations) == [
        "backend/control_plane/adapters/demo.py:5:raw request cookies access bypasses cookie policy",
        "backend/control_plane/adapters/demo.py:6:raw response cookie mutation bypasses cookie policy",
    ]


def test_cookie_boundary_guard_allows_cookie_policy_entrypoints(tmp_path) -> None:
    source_path = tmp_path / "backend" / "control_plane" / "adapters" / "demo.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(
        """
from __future__ import annotations

from backend.control_plane.auth.cookie_policy import read_request_cookie, set_http_only_cookie


def bind(request, response) -> str | None:
    token = read_request_cookie(request, "token")
    set_http_only_cookie(response, key="token", value="value", max_age_seconds=60)
    return token
""".strip(),
        encoding="utf-8",
    )

    assert cookie_boundary_violations(repo_root=tmp_path) == []
