# 法典 5.1.2：测试数据由工厂生成，禁用硬编码。
# 供单元/集成测试复用，避免手写 dict 或模型字面量。
from __future__ import annotations

import factory

from backend.workers.alert_manager import AlertPayload


class AlertPayloadFactory(factory.Factory):
    """AlertPayload 测试数据工厂；可覆盖任意字段。"""

    class Meta:
        model = AlertPayload

    level = "info"
    title = factory.Sequence(lambda n: f"Alert title {n}")
    message = factory.Sequence(lambda n: f"Alert message {n}")
    source = "ZEN70_Sentinel"


class MockUserFactory:
    """JWT 解析后的 user 字典工厂，用于依赖注入 mock。"""

    @staticmethod
    def build(sub: str = "family_admin", role: str = "admin", **kwargs: str) -> dict:
        out: dict = {"sub": sub, "role": role}
        out.update(kwargs)
        return out
