"""
法典 1.2 协议驱动 UI + ADR 0010 统一 Envelope 契约：

GET /api/v1/capabilities 必须返回 SuccessEnvelope 包装后的
Dict[str, CapabilityItem]，每项含 status / enabled，可选 endpoint / models / reason。

本文件替代旧版根目录 tests/test_contract_capabilities.py（裸 body 断言已废弃）。
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

_client: TestClient | None = None


def _get_app() -> Any:
    """延迟导入 FastAPI app，避免未安装依赖时顶层报错。"""
    from backend.control_plane.app.entrypoint import app

    return app


def _get_client() -> TestClient:
    global _client
    if _client is None:
        _client = TestClient(_get_app())
    return _client


class TestCapabilitiesEnvelopeContract:
    """验证 /api/v1/capabilities 严格符合 ADR 0010 Envelope 契约。"""

    def test_response_is_envelope(self) -> None:
        """响应必须包裹在 {code, message, data} Envelope 中。"""
        client = _get_client()
        response = client.get("/api/v1/capabilities")
        assert response.status_code == 200, response.text

        envelope = response.json()
        assert isinstance(envelope, dict), "响应必须为 JSON 对象"
        assert envelope.get("code") == "ZEN-OK-0", f"Envelope code 必须为 ZEN-OK-0，实际: {envelope.get('code')!r}"
        assert "message" in envelope, "Envelope 必须含 message 字段"
        assert "data" in envelope, "Envelope 必须含 data 字段"

    def test_data_is_capability_matrix(self) -> None:
        """data 内层必须为 Dict[str, CapabilityItem]。"""
        client = _get_client()
        response = client.get("/api/v1/capabilities")
        assert response.status_code == 200

        data = response.json().get("data", {})
        assert isinstance(data, dict), "data 必须为 JSON 对象（能力矩阵）"

    def test_capability_item_required_fields(self) -> None:
        """每个 CapabilityItem 必须含 status 和 enabled。"""
        client = _get_client()
        response = client.get("/api/v1/capabilities")
        assert response.status_code == 200

        data = response.json().get("data", {})
        allowed_status = {"online", "offline", "pending_maintenance", "unknown"}

        for key, item in data.items():
            assert isinstance(key, str), f"键必须为字符串: {key!r}"
            assert isinstance(item, dict), f"{key!r} 的值必须为对象"
            assert "status" in item, f"{key!r} 缺少 status"
            assert "enabled" in item, f"{key!r} 缺少 enabled"
            assert item["status"] in allowed_status, f"{key!r}.status 非法: {item['status']!r}"
            assert isinstance(item["enabled"], bool), f"{key!r}.enabled 必须为布尔"

    def test_capability_item_optional_fields_type(self) -> None:
        """可选字段 endpoint / models / reason 类型校验。"""
        client = _get_client()
        response = client.get("/api/v1/capabilities")
        assert response.status_code == 200

        data = response.json().get("data", {})
        for key, item in data.items():
            if "endpoint" in item:
                assert item["endpoint"] is None or isinstance(item["endpoint"], str), f"{key!r}.endpoint 须为 str | null"
            if "models" in item:
                assert item["models"] is None or isinstance(item["models"], list), f"{key!r}.models 须为 list | null"
            if "reason" in item:
                assert item["reason"] is None or isinstance(item["reason"], str), f"{key!r}.reason 须为 str | null"
