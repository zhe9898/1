"""
iac_core.secrets — 密钥幂等生成/加载/轮转。

直接代理 scripts.compiler.secrets_manager（唯一来源），
核心库统一导出入口。
"""

from __future__ import annotations

# 唯一来源: scripts/compiler/secrets_manager.py
# 核心库统一从此处 re-export
from scripts.compiler.secrets_manager import (
    DEFAULT_VALUES,
    GENERATE_IF_MISSING,
    SECRET_KEYS,
    TUNNEL_TOKEN_PLACEHOLDER,
    generate_secrets,
    resolve_env_default,
)

__all__ = [
    "DEFAULT_VALUES",
    "GENERATE_IF_MISSING",
    "SECRET_KEYS",
    "TUNNEL_TOKEN_PLACEHOLDER",
    "generate_secrets",
    "resolve_env_default",
]
