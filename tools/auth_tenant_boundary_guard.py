from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

from fastapi import HTTPException

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.control_plane.adapters.auth_shared import bind_admin_scope, enforce_admin_scope, request_tenant_id  # noqa: E402
from backend.control_plane.adapters.models.auth import (  # noqa: E402
    PasswordLoginRequest,
    PinLoginRequest,
    WebAuthnLoginBeginRequest,
    WebAuthnLoginCompleteRequest,
    WebAuthnRegisterBeginRequest,
)
from backend.control_plane.auth.authority_boundary import export_auth_boundary_contract  # noqa: E402
from backend.control_plane.auth.sessions import validate_session_claims  # noqa: E402
from backend.control_plane.auth.subject_authority import assert_token_subject_active  # noqa: E402

_TENANT_REQUIRED_MODELS = {
    "backend.control_plane.adapters.models.auth.PasswordLoginRequest": PasswordLoginRequest,
    "backend.control_plane.adapters.models.auth.PinLoginRequest": PinLoginRequest,
    "backend.control_plane.adapters.models.auth.WebAuthnRegisterBeginRequest": WebAuthnRegisterBeginRequest,
    "backend.control_plane.adapters.models.auth.WebAuthnLoginBeginRequest": WebAuthnLoginBeginRequest,
    "backend.control_plane.adapters.models.auth.WebAuthnLoginCompleteRequest": WebAuthnLoginCompleteRequest,
}


def _detail_code(exc: HTTPException) -> str | None:
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict):
        code = detail.get("code")
        return code if isinstance(code, str) else None
    return None


async def _async_violations() -> list[str]:
    violations: list[str] = []

    db = AsyncMock()
    try:
        await bind_admin_scope(db, {"role": "admin"})
        violations.append("backend.control_plane.adapters.auth_shared.bind_admin_scope:missing tenant claim must be rejected")
    except HTTPException as exc:
        if exc.status_code != 403:
            violations.append("backend.control_plane.adapters.auth_shared.bind_admin_scope:missing tenant claim must raise 403")

    try:
        enforce_admin_scope({"role": "admin"}, "tenant-a", action="admin scoped access")
        violations.append("backend.control_plane.adapters.auth_shared.enforce_admin_scope:missing tenant claim must be rejected")
    except HTTPException as exc:
        if exc.status_code != 403:
            violations.append("backend.control_plane.adapters.auth_shared.enforce_admin_scope:missing tenant claim must raise 403")

    session_db = AsyncMock()
    try:
        await validate_session_claims(session_db, {"sub": "1", "sid": "session-1", "jti": "token-1"})
        violations.append("backend.control_plane.auth.sessions.validate_session_claims:missing tenant claim must be rejected")
    except HTTPException as exc:
        if exc.status_code != 401:
            violations.append("backend.control_plane.auth.sessions.validate_session_claims:missing tenant claim must raise 401")
    if session_db.execute.await_count:
        violations.append("backend.control_plane.auth.sessions.validate_session_claims:missing tenant claim must fail before DB lookup")

    subject_db = AsyncMock()
    try:
        await assert_token_subject_active(subject_db, {"sub": "1"})
        violations.append("backend.control_plane.auth.subject_authority.assert_token_subject_active:missing tenant claim must be rejected")
    except HTTPException as exc:
        if exc.status_code != 401:
            violations.append("backend.control_plane.auth.subject_authority.assert_token_subject_active:missing tenant claim must raise 401")
    if subject_db.execute.await_count:
        violations.append("backend.control_plane.auth.subject_authority.assert_token_subject_active:missing tenant claim must fail before DB lookup")

    return violations


def auth_tenant_boundary_violations() -> list[str]:
    violations: list[str] = []
    for path, model in _TENANT_REQUIRED_MODELS.items():
        field = model.model_fields.get("tenant_id")
        if field is None or not field.is_required():
            violations.append(f"{path}:tenant_id must be an explicit required field")

    if request_tenant_id(" tenant-a ") != "tenant-a":
        violations.append("backend.control_plane.adapters.auth_shared.request_tenant_id:must normalize explicit tenant ids")
    try:
        request_tenant_id(None)
        violations.append("backend.control_plane.adapters.auth_shared.request_tenant_id:missing tenant_id must be rejected")
    except HTTPException as exc:
        if exc.status_code != 400 or _detail_code(exc) != "ZEN-TENANT-4001":
            violations.append("backend.control_plane.adapters.auth_shared.request_tenant_id:missing tenant_id must raise ZEN-TENANT-4001")

    violations.extend(asyncio.run(_async_violations()))
    return violations


def main() -> int:
    violations = auth_tenant_boundary_violations()
    if not violations:
        return 0
    print("auth tenant boundary violations detected:")
    print(export_auth_boundary_contract())
    for violation in violations:
        print(violation)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
