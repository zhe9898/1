from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.kernel.contracts.tenant_claims import (
    MISSING_TENANT_CLAIM_CODE,
    current_user_tenant_id,
    normalize_tenant_claim,
    require_current_user_tenant_id,
)
from tools.tenant_claim_guard import tenant_claim_violations


def test_normalize_tenant_claim_trims_and_rejects_empty_values() -> None:
    assert normalize_tenant_claim("  tenant-a  ") == "tenant-a"
    assert normalize_tenant_claim("") is None
    assert normalize_tenant_claim("   ") is None
    assert normalize_tenant_claim(None) is None


def test_current_user_tenant_id_returns_normalized_claim() -> None:
    assert current_user_tenant_id({"tenant_id": " tenant-a "}) == "tenant-a"
    assert current_user_tenant_id({"tenant_id": ""}) is None
    assert current_user_tenant_id({}) is None


def test_require_current_user_tenant_id_rejects_missing_claim() -> None:
    with pytest.raises(HTTPException) as exc_info:
        require_current_user_tenant_id({})

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["code"] == MISSING_TENANT_CLAIM_CODE


def test_tenant_claim_guard_rejects_subscript_access(tmp_path) -> None:
    source_path = tmp_path / "backend" / "control_plane" / "adapters" / "demo.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(
        """
from __future__ import annotations


def handler(current_user: dict[str, str]) -> str:
    return current_user["tenant_id"]
""".strip(),
        encoding="utf-8",
    )

    violations = tenant_claim_violations(repo_root=tmp_path)

    assert violations == ["backend/control_plane/adapters/demo.py:5:direct current_user tenant claim access"]


def test_tenant_claim_guard_allows_contract_helper_usage(tmp_path) -> None:
    source_path = tmp_path / "backend" / "control_plane" / "adapters" / "demo.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(
        """
from __future__ import annotations

from backend.kernel.contracts.tenant_claims import require_current_user_tenant_id


def handler(current_user: dict[str, str]) -> str:
    return require_current_user_tenant_id(current_user)
""".strip(),
        encoding="utf-8",
    )

    assert tenant_claim_violations(repo_root=tmp_path) == []
