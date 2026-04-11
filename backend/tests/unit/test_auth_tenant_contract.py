from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.control_plane.adapters.auth_shared import bind_admin_scope, enforce_admin_scope, request_tenant_id
from backend.control_plane.adapters.models.auth import (
    PasswordLoginRequest,
    PinLoginRequest,
    WebAuthnLoginBeginRequest,
    WebAuthnLoginCompleteRequest,
    WebAuthnRegisterBeginRequest,
)
from backend.control_plane.auth.sessions import validate_session_claims
from backend.control_plane.auth.subject_authority import assert_token_subject_active


def test_request_tenant_id_requires_explicit_value() -> None:
    assert request_tenant_id(" tenant-a ") == "tenant-a"

    with pytest.raises(HTTPException) as exc_info:
        request_tenant_id(None)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["code"] == "ZEN-TENANT-4001"


@pytest.mark.parametrize(
    ("model", "kwargs"),
    [
        (PasswordLoginRequest, {"username": "admin", "password": "Password123!"}),
        (PinLoginRequest, {"pin": "12345678"}),
        (WebAuthnRegisterBeginRequest, {"username": "alice", "display_name": "Alice"}),
        (WebAuthnLoginBeginRequest, {"username": "alice"}),
        (WebAuthnLoginCompleteRequest, {"username": "alice", "credential": {"id": "cred-1"}}),
    ],
)
def test_auth_request_models_require_tenant_id(model, kwargs: dict[str, object]) -> None:
    assert model.model_fields["tenant_id"].is_required()

    with pytest.raises(ValidationError):
        model(**kwargs)


@pytest.mark.anyio
async def test_bind_admin_scope_rejects_missing_tenant_claim() -> None:
    db = AsyncMock()

    with pytest.raises(HTTPException) as exc_info:
        await bind_admin_scope(db, {"role": "admin"})

    assert exc_info.value.status_code == 403
    db.execute.assert_not_awaited()


def test_enforce_admin_scope_rejects_missing_tenant_claim() -> None:
    with pytest.raises(HTTPException) as exc_info:
        enforce_admin_scope({"role": "admin"}, "tenant-a", action="manage users")

    assert exc_info.value.status_code == 403


@pytest.mark.anyio
async def test_validate_session_claims_rejects_missing_tenant_claim() -> None:
    db = AsyncMock()

    with pytest.raises(HTTPException) as exc_info:
        await validate_session_claims(db, {"sub": "1", "sid": "session-1", "jti": "token-1"})

    assert exc_info.value.status_code == 401
    db.execute.assert_not_awaited()


@pytest.mark.anyio
async def test_assert_token_subject_active_rejects_missing_tenant_claim() -> None:
    db = AsyncMock()

    with pytest.raises(HTTPException) as exc_info:
        await assert_token_subject_active(db, {"sub": "1"})

    assert exc_info.value.status_code == 401
    db.execute.assert_not_awaited()
