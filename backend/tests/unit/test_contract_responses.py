from __future__ import annotations

import json


class TestErrorResponseContract:
    def test_required_fields_exist(self) -> None:
        from backend.api.models import ErrorResponse

        schema = ErrorResponse.model_json_schema()
        props = schema["properties"]
        assert "code" in props
        assert "message" in props
        assert "recovery_hint" in props
        assert "details" in props

    def test_code_is_string(self) -> None:
        from backend.api.models import ErrorResponse

        schema = ErrorResponse.model_json_schema()
        assert schema["properties"]["code"]["type"] == "string"

    def test_details_is_object(self) -> None:
        from backend.api.models import ErrorResponse

        schema = ErrorResponse.model_json_schema()
        assert schema["properties"]["details"]["type"] == "object"

    def test_serialization_roundtrip(self) -> None:
        from backend.api.models import ErrorResponse

        original = ErrorResponse(
            code="ZEN-TEST-001",
            message="test error",
            recovery_hint="retry",
            details={"request_id": "rid-001"},
        )
        restored = ErrorResponse.model_validate_json(original.model_dump_json())
        assert restored.code == original.code
        assert restored.message == original.message
        assert restored.recovery_hint == original.recovery_hint
        assert restored.details == original.details


class TestSuccessEnvelopeContract:
    def test_envelope_code_constant_in_main_entry(self) -> None:
        from backend.control_plane.app.response_envelope import success_envelope

        assert callable(success_envelope)

    def test_envelope_json_roundtrip(self) -> None:
        for data in [{"key": "val"}, [1, 2], None, "string", 42]:
            envelope = {"code": "ZEN-OK-0", "message": "ok", "data": data}
            parsed = json.loads(json.dumps(envelope, ensure_ascii=False))
            assert parsed["code"] == "ZEN-OK-0"
            assert parsed["message"] == "ok"
            assert parsed["data"] == data


class TestSwitchEventContract:
    def test_required_field_state(self) -> None:
        from backend.kernel.contracts.events_schema import SwitchEventPayload

        schema = SwitchEventPayload.model_json_schema()
        assert "state" in schema.get("required", [])

    def test_valid_state_values(self) -> None:
        from backend.kernel.contracts.events_schema import SwitchEventPayload

        for state in ("ON", "OFF", "RESTART"):
            payload = SwitchEventPayload(state=state)
            assert payload.state == state

    def test_effective_switch_name_priority(self) -> None:
        from backend.kernel.contracts.events_schema import SwitchEventPayload

        p1 = SwitchEventPayload(state="ON", switch="gpu", name="old_gpu")
        assert p1.effective_switch_name() == "gpu"

        p2 = SwitchEventPayload(state="OFF", name="redis")
        assert p2.effective_switch_name() == "redis"

        p3 = SwitchEventPayload(state="RESTART")
        assert p3.effective_switch_name() is None

    def test_from_redis_message_json_string(self) -> None:
        from backend.kernel.contracts.events_schema import SwitchEventPayload

        raw = json.dumps({"state": "OFF", "switch": "jellyfin", "reason": "maintenance"})
        payload = SwitchEventPayload.from_redis_message(raw)
        assert payload is not None
        assert payload.state == "OFF"
        assert payload.effective_switch_name() == "jellyfin"

    def test_from_redis_message_bytes(self) -> None:
        from backend.kernel.contracts.events_schema import SwitchEventPayload

        raw = b'{"state": "ON", "switch": "nas"}'
        payload = SwitchEventPayload.from_redis_message(raw)
        assert payload is not None
        assert payload.state == "ON"

    def test_from_redis_message_invalid_returns_none(self) -> None:
        from backend.kernel.contracts.events_schema import SwitchEventPayload

        assert SwitchEventPayload.from_redis_message("not json") is None
        assert SwitchEventPayload.from_redis_message(b"") is None
        assert SwitchEventPayload.from_redis_message({"no_state": True}) is None

    def test_serialization_roundtrip(self) -> None:
        from backend.kernel.contracts.events_schema import SwitchEventPayload

        original = SwitchEventPayload(
            state="RESTART",
            switch="gpu_worker",
            reason="liveness_failed",
            updated_by="health_probe",
        )
        restored = SwitchEventPayload.model_validate_json(original.model_dump_json())
        assert restored.state == original.state
        assert restored.effective_switch_name() == original.effective_switch_name()


class TestSwitchStateEventContract:
    def test_valid_state_values(self) -> None:
        from backend.kernel.contracts.events_schema import SwitchStateEventPayload

        for state in ("ON", "OFF", "PENDING"):
            payload = SwitchStateEventPayload(state=state)
            assert payload.state == state

    def test_serialization_roundtrip(self) -> None:
        from backend.kernel.contracts.events_schema import SwitchStateEventPayload

        original = SwitchStateEventPayload(
            state="PENDING",
            switch="media",
            reason="syncing",
            updated_by="sentinel",
        )
        restored = SwitchStateEventPayload.model_validate_json(original.model_dump_json())
        assert restored.state == "PENDING"
        assert restored.effective_switch_name() == "media"


class TestHardwareStateEventContract:
    def test_required_fields(self) -> None:
        from backend.kernel.contracts.events_schema import HardwareStateEventPayload

        schema = HardwareStateEventPayload.model_json_schema()
        assert "path" in schema.get("required", [])
        assert "state" in schema.get("required", [])

    def test_serialization_roundtrip(self) -> None:
        from backend.kernel.contracts.events_schema import HardwareStateEventPayload

        original = HardwareStateEventPayload(path="/mnt/media", state="online", reason="mounted", uuid="disk-1", timestamp=123.0)
        restored = HardwareStateEventPayload.model_validate_json(original.model_dump_json())
        assert restored.path == "/mnt/media"
        assert restored.state == "online"
        assert restored.uuid == "disk-1"


class TestHealthResponseContract:
    def test_required_fields(self) -> None:
        from backend.api.models import HealthResponse

        schema = HealthResponse.model_json_schema()
        assert "status" in schema.get("required", [])

    def test_version_has_default(self) -> None:
        from backend.api.models import HealthResponse

        health = HealthResponse(status="healthy")
        assert health.version is not None
        assert len(health.version) > 0


class TestCapabilityResponseContract:
    def test_required_status_field(self) -> None:
        from backend.api.models import CapabilityResponse

        schema = CapabilityResponse.model_json_schema()
        assert "status" in schema.get("required", [])

    def test_optional_fields(self) -> None:
        from backend.api.models import CapabilityResponse

        capability = CapabilityResponse(status="online", enabled=True)
        assert capability.endpoint is None
        assert capability.models is None
        assert capability.reason is None


class TestSwitchStateResponseContract:
    def test_required_state_field(self) -> None:
        from backend.api.models import SwitchStateResponse

        schema = SwitchStateResponse.model_json_schema()
        assert "state" in schema.get("required", [])

    def test_valid_states(self) -> None:
        from backend.api.models import SwitchStateResponse

        for state in ("ON", "OFF", "PENDING"):
            response = SwitchStateResponse(state=state)
            assert response.state == state
