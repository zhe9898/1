"""
core/errors.py 单元测试 — 统一错误码契约验证。

高 ROI：确保全系统错误格式 `{code, message, recovery_hint, details}` 不漂移。
"""

from __future__ import annotations

from fastapi import HTTPException

from backend.kernel.contracts.errors import (
    ZenErrorCode,
    ZenErrorDetails,
    ZenErrorResponse,
    ZenSuccessResponse,
    ok,
    zen,
)


class TestZenErrorFactory:
    """zen() 必须产出法典规定的 4 字段错误信封。"""

    def test_basic_error_envelope(self) -> None:
        exc = zen("ZEN-TEST-400", "test error", status_code=400)
        assert isinstance(exc, HTTPException)
        assert exc.status_code == 400
        detail = exc.detail
        assert detail["code"] == "ZEN-TEST-400"  # type: ignore[index]
        assert detail["message"] == "test error"  # type: ignore[index]
        assert "recovery_hint" in detail
        assert "details" in detail

    def test_with_recovery_hint(self) -> None:
        exc = zen("ZEN-TEST-400", "bad", recovery_hint="请刷新页面")
        assert exc.detail["recovery_hint"] == "请刷新页面"  # type: ignore[index]

    def test_with_details_dict(self) -> None:
        exc = zen("ZEN-X-500", "err", details={"asset_id": "abc"})
        assert exc.detail["details"]["asset_id"] == "abc"  # type: ignore[index]

    def test_with_zen_error_details_model(self) -> None:
        d = ZenErrorDetails(request_id="req-123")
        exc = zen("ZEN-X-500", "err", details=d)
        assert exc.detail["details"]["request_id"] == "req-123"  # type: ignore[index]

    def test_with_extra_details_merge(self) -> None:
        exc = zen(
            "ZEN-X-500",
            "err",
            details={"a": 1},
            extra_details={"b": 2},
        )
        assert exc.detail["details"]["a"] == 1  # type: ignore[comparison-overlap, index]
        assert exc.detail["details"]["b"] == 2  # type: ignore[comparison-overlap, index]

    def test_enum_code(self) -> None:
        exc = zen(ZenErrorCode.AUTH_FORBIDDEN, "forbidden", status_code=403)
        assert exc.detail["code"] == "ZEN-AUTH-403"  # type: ignore[index]
        assert exc.status_code == 403

    def test_default_status_code_is_400(self) -> None:
        exc = zen("ZEN-X-000", "x")
        assert exc.status_code == 400


class TestOkFactory:
    """ok() 必须产出法典规定的成功信封。"""

    def test_basic_success_envelope(self) -> None:
        result = ok({"items": [1, 2, 3]})
        assert result["code"] == "ZEN-OK-0"
        assert result["message"] == "ok"
        assert result["data"] == {"items": [1, 2, 3]}
        assert "recovery_hint" in result
        assert "details" in result

    def test_custom_message(self) -> None:
        result = ok(None, message="created", code="ZEN-OK-1")
        assert result["message"] == "created"
        assert result["code"] == "ZEN-OK-1"

    def test_with_details(self) -> None:
        result = ok(42, details={"count": 1})
        assert result["details"]["count"] == 1  # type: ignore[index]


class TestZenErrorResponse:
    """Pydantic 模型字段完整性。"""

    def test_all_fields_required(self) -> None:
        resp = ZenErrorResponse(code="ZEN-X", message="test")
        assert resp.code == "ZEN-X"
        assert resp.message == "test"
        assert resp.recovery_hint == ""
        assert resp.details == {}

    def test_model_dump_complete(self) -> None:
        resp = ZenErrorResponse(
            code="ZEN-Y",
            message="msg",
            recovery_hint="hint",
            details={"k": "v"},
        )
        d = resp.model_dump()
        assert set(d.keys()) == {"code", "message", "recovery_hint", "details"}


class TestZenSuccessResponse:
    """成功响应模型验证。"""

    def test_default_fields(self) -> None:
        resp = ZenSuccessResponse()
        assert resp.code == "ZEN-OK-0"
        assert resp.message == "ok"
        assert resp.data is None

    def test_with_data(self) -> None:
        resp = ZenSuccessResponse(data={"id": 1})
        d = resp.model_dump()
        assert d["data"] == {"id": 1}


class TestZenErrorCode:
    """错误码枚举完整性。"""

    def test_all_codes_start_with_zen(self) -> None:
        for code in ZenErrorCode:
            assert code.value.startswith("ZEN-"), f"{code.name} 的值 {code.value} 不以 ZEN- 开头"

    def test_auth_codes_exist(self) -> None:
        assert ZenErrorCode.AUTH_FORBIDDEN.value == "ZEN-AUTH-403"
        assert ZenErrorCode.AUTH_UNAUTHORIZED.value == "ZEN-AUTH-401"
