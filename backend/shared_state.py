"""
跨模块共享状态：避免 middleware ↔ background_tasks 循环引用。

所有需要被多个模块读写的运行时状态集中在此模块，
任何模块只需 `from backend.shared_state import xxx` 即可安全访问。
"""

from __future__ import annotations

# SRE 微服务 Readiness 状态（background_tasks 写入，middleware 读取）
service_readiness: dict[str, bool] = {}

# SRE Liveness 连续失败计数
service_liveness_fails: dict[str, int] = {}
