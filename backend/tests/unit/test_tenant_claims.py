from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.kernel.contracts.tenant_claims import (
    MISSING_TENANT_CLAIM_CODE,
    current_user_tenant_id,
    normalize_tenant_claim,
    require_current_user_tenant_id,
)


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
