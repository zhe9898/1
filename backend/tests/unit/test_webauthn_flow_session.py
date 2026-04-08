from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from backend.control_plane.auth.webauthn_flow_session import ensure_webauthn_flow_session, require_webauthn_flow_session


def _request(cookie_value: str | None = None) -> MagicMock:
    request = MagicMock()
    request.state = MagicMock()
    request.cookies = {} if cookie_value is None else {"zen70_webauthn_session": cookie_value}
    return request


def test_ensure_webauthn_flow_session_reuses_cookie_and_sets_response() -> None:
    request = _request("existing-session")
    response = MagicMock()

    session_id = ensure_webauthn_flow_session(response, request, ttl_seconds=300)

    assert session_id == "existing-session"
    response.set_cookie.assert_called_once()


def test_require_webauthn_flow_session_falls_back_to_request_state() -> None:
    request = _request()
    request.state.webauthn_flow_session_id = "state-session"

    assert require_webauthn_flow_session(request) == "state-session"


def test_require_webauthn_flow_session_rejects_missing_binding() -> None:
    request = _request()
    request.state.webauthn_flow_session_id = None

    with pytest.raises(HTTPException) as exc:
        require_webauthn_flow_session(request)

    assert exc.value.status_code == 400
