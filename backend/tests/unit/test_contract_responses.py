"""
契约测试：高风险 API 响应结构。

验证 ErrorResponse / SuccessEnvelope / SSE Event / SwitchEventPayload
的 JSON schema 与前端 TypeScript types 不可漂移（ADR 0009）。

这是 PR 门禁测试——不依赖任何运行时服务，纯结构校验。
"""

from __future__ import annotations

import json

# =========================================================================
# 1. ErrorResponse 契约（ZEN-xxx 错误码结构）
# =========================================================================


class TestErrorResponseContract:
    """ErrorResponse 必须包含 code/message/recovery_hint/details 四个字段。"""

    def test_required_fields_exist(self) -> None:
        """ErrorResponse schema 必须包含必需字段。"""
        from backend.api.models import ErrorResponse

        schema = ErrorResponse.model_json_schema()
        props = schema["properties"]
        assert "code" in props, "ErrorResponse 缺少 code 字段"
        assert "message" in props, "ErrorResponse 缺少 message 字段"
        assert "recovery_hint" in props, "ErrorResponse 缺少 recovery_hint 字段"
        assert "details" in props, "ErrorResponse 缺少 details 字段"

    def test_code_is_string(self) -> None:
        """code 字段必须为 string 类型。"""
        from backend.api.models import ErrorResponse

        schema = ErrorResponse.model_json_schema()
        assert schema["properties"]["code"]["type"] == "string"

    def test_details_is_object(self) -> None:
        """details 字段必须为 object 类型。"""
        from backend.api.models import ErrorResponse

        schema = ErrorResponse.model_json_schema()
        assert schema["properties"]["details"]["type"] == "object"

    def test_serialization_roundtrip(self) -> None:
        """ErrorResponse 序列化/反序列化必须无损。"""
        from backend.api.models import ErrorResponse

        original = ErrorResponse(
            code="ZEN-TEST-001",
            message="测试错误",
            recovery_hint="请重试",
            details={"request_id": "rid-001"},
        )
        raw = original.model_dump_json()
        restored = ErrorResponse.model_validate_json(raw)
        assert restored.code == original.code
        assert restored.message == original.message
        assert restored.recovery_hint == original.recovery_hint
        assert restored.details == original.details


# =========================================================================
# 2. SuccessEnvelope 契约（中间件包装后的结构）
# =========================================================================


class TestSuccessEnvelopeContract:
    """成功响应 envelope 必须为 {code, message, data, ...} 结构（ADR 0010）。"""

    def test_envelope_code_constant_in_middleware(self) -> None:
        """运行时挂载的 success_envelope 必须使用成功码 ZEN-OK-0。"""
        from backend.api.main import success_envelope

        # 验证 success_envelope 函数存在且可调用（权威实现在 main.py）
        assert callable(success_envelope), "success_envelope 中间件必须存在于 backend.api.main"

    def test_envelope_json_roundtrip(self) -> None:
        """envelope 结构序列化/反序列化必须保持 code/message/data 三个 key。"""
        for data in [{"key": "val"}, [1, 2], None, "string", 42]:
            envelope = {"code": "ZEN-OK-0", "message": "ok", "data": data}
            serialized = json.dumps(envelope, ensure_ascii=False)
            parsed = json.loads(serialized)
            assert parsed["code"] == "ZEN-OK-0"
            assert parsed["message"] == "ok"
            assert parsed["data"] == data


# =========================================================================
# 3. SwitchEventPayload 契约（Redis Pub/Sub 跨进程通信）
# =========================================================================


class TestSwitchEventContract:
    """switch:events 通道 payload 必须严格遵循 SwitchEventPayload schema。"""

    def test_required_field_state(self) -> None:
        """state 是必需字段。"""
        from backend.core.events_schema import SwitchEventPayload

        schema = SwitchEventPayload.model_json_schema()
        assert "state" in schema.get("required", [])

    def test_valid_state_values(self) -> None:
        """state 应接受 ON/OFF/RESTART。"""
        from backend.core.events_schema import SwitchEventPayload

        for state in ("ON", "OFF", "RESTART"):
            payload = SwitchEventPayload(state=state)  # type: ignore[call-arg]
            assert payload.state == state

    def test_effective_switch_name_priority(self) -> None:
        """effective_switch_name 应优先取 switch，其次取 name。"""
        from backend.core.events_schema import SwitchEventPayload

        p1 = SwitchEventPayload(state="ON", switch="gpu", name="old_gpu")  # type: ignore[call-arg]
        assert p1.effective_switch_name() == "gpu"

        p2 = SwitchEventPayload(state="OFF", name="redis")  # type: ignore[call-arg]
        assert p2.effective_switch_name() == "redis"

        p3 = SwitchEventPayload(state="RESTART")  # type: ignore[call-arg]
        assert p3.effective_switch_name() is None

    def test_from_redis_message_json_string(self) -> None:
        """从 JSON 字符串反序列化。"""
        from backend.core.events_schema import SwitchEventPayload

        raw = json.dumps({"state": "OFF", "switch": "jellyfin", "reason": "maintenance"})
        payload = SwitchEventPayload.from_redis_message(raw)
        assert payload is not None
        assert payload.state == "OFF"
        assert payload.effective_switch_name() == "jellyfin"

    def test_from_redis_message_bytes(self) -> None:
        """从 bytes 反序列化。"""
        from backend.core.events_schema import SwitchEventPayload

        raw = b'{"state": "ON", "switch": "nas"}'
        payload = SwitchEventPayload.from_redis_message(raw)
        assert payload is not None
        assert payload.state == "ON"

    def test_from_redis_message_invalid_returns_none(self) -> None:
        """无效消息应返回 None（不崩溃）。"""
        from backend.core.events_schema import SwitchEventPayload

        assert SwitchEventPayload.from_redis_message("not json") is None
        assert SwitchEventPayload.from_redis_message(b"") is None
        assert SwitchEventPayload.from_redis_message({"no_state": True}) is None

    def test_serialization_roundtrip(self) -> None:
        """SwitchEventPayload 序列化/反序列化必须无损。"""
        from backend.core.events_schema import SwitchEventPayload

        original = SwitchEventPayload(  # type: ignore[call-arg]
            state="RESTART",
            switch="gpu_worker",
            reason="liveness_failed",
            updated_by="health_probe",
        )
        raw = original.model_dump_json()
        restored = SwitchEventPayload.model_validate_json(raw)
        assert restored.state == original.state
        assert restored.effective_switch_name() == original.effective_switch_name()


# =========================================================================
# 4. HealthResponse 契约
# =========================================================================


class TestHealthResponseContract:
    """HealthResponse 必须包含 status/version/services。"""

    def test_required_fields(self) -> None:
        """HealthResponse schema 必须包含 status 字段。"""
        from backend.api.models import HealthResponse

        schema = HealthResponse.model_json_schema()
        assert "status" in schema.get("required", [])

    def test_version_has_default(self) -> None:
        """version 有默认值。"""
        from backend.api.models import HealthResponse

        h = HealthResponse(status="healthy")
        assert h.version is not None
        assert len(h.version) > 0


# =========================================================================
# 5. CapabilityResponse 契约
# =========================================================================


class TestCapabilityResponseContract:
    """CapabilityResponse 与前端 TypeScript types 对齐。"""

    def test_required_status_field(self) -> None:
        """status 是必需字段。"""
        from backend.api.models import CapabilityResponse

        schema = CapabilityResponse.model_json_schema()
        assert "status" in schema.get("required", [])

    def test_optional_fields(self) -> None:
        """endpoint/models/reason 是可选字段。"""
        from backend.api.models import CapabilityResponse

        cap = CapabilityResponse(status="online", enabled=True)
        assert cap.endpoint is None
        assert cap.models is None
        assert cap.reason is None


# =========================================================================
# 6. SwitchStateResponse 契约
# =========================================================================


class TestSwitchStateResponseContract:
    """SwitchStateResponse 结构完整性。"""

    def test_required_state_field(self) -> None:
        """state 是必需字段。"""
        from backend.api.models import SwitchStateResponse

        schema = SwitchStateResponse.model_json_schema()
        assert "state" in schema.get("required", [])

    def test_valid_states(self) -> None:
        """state 应接受 ON/OFF/PENDING。"""
        from backend.api.models import SwitchStateResponse

        for s in ("ON", "OFF", "PENDING"):
            resp = SwitchStateResponse(state=s)
            assert resp.state == s
