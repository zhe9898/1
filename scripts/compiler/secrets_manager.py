#!/usr/bin/env python3
"""
配置编译器包：密钥幂等解析模块。

从 compiler.py 拆解而来，负责敏感凭证生成、加载与轮转。
"""

from __future__ import annotations

import os
import re
import secrets
import string
from pathlib import Path

# ---------------------------------------------------------------------------
# URL-safe 密码生成（法典 §1.2 + §3.4）
# 排除 @ : / ? # [ ] = + 等 URI 保留字符，确保可安全嵌入 DSN/URI
# ---------------------------------------------------------------------------
_URL_SAFE_ALPHABET = string.ascii_letters + string.digits + "-_"


def _generate_url_safe_password(length: int = 32) -> str:
    """生成 URL-safe 密码，可安全嵌入 postgresql:// 等 DSN 而无需编码。"""
    return "".join(secrets.choice(_URL_SAFE_ALPHABET) for _ in range(length))


# ---------------------------------------------------------------------------
# 敏感密钥清单（法典 7.1.2）：需生成或从外部注入，严禁硬编码默认密码
# ---------------------------------------------------------------------------
SECRET_KEYS = (
    "POSTGRES_PASSWORD",
    "JWT_SECRET_CURRENT",
    "JWT_SECRET_PREVIOUS",
    "REDIS_PASSWORD",
    "REDIS_ACL_GATEWAY_CREDENTIAL",
    "REDIS_ACL_READONLY_CREDENTIAL",
    "GF_ADMIN_PASSWORD",
    "CLOUDFLARED_TUNNEL_TOKEN",
    "TUNNEL_TOKEN",
    "HEADSCALE_PRIVATE_KEY",
    "AI_BACKEND_URL",
)
"""必须解析的敏感键名；缺失时按需生成或留空。"""

# 缺失时自动生成（32 位高强随机）；其余仅保留已有值
GENERATE_IF_MISSING = (
    "POSTGRES_PASSWORD",
    "JWT_SECRET_CURRENT",
    "REDIS_ACL_GATEWAY_CREDENTIAL",
    "REDIS_ACL_READONLY_CREDENTIAL",
    "GF_ADMIN_PASSWORD",
)

# 特殊默认值映射（Gateway Kernel 默认不假设本机 AI 服务）
DEFAULT_VALUES = {"AI_BACKEND_URL": ""}

TUNNEL_TOKEN_PLACEHOLDER = "your_cloudflare_token_here_replace_me"
"""TUNNEL_TOKEN 缺失时注入的占位符，严禁硬编码真实 token。"""


def _resolve_env_default(val: str | None, default: str) -> str:
    """配置为占位符（${VAR}）时返回 default，否则返回原值。"""
    if not val or (isinstance(val, str) and val.startswith("${") and val.endswith("}")):
        return default
    return str(val)


# 公开别名供 iac_core 导出使用
resolve_env_default = _resolve_env_default


def _parse_env_file(path: Path) -> dict[str, str]:
    """解析 .env 格式文件，返回 KEY -> value 字典。忽略空行、注释、占位符。"""
    result: dict[str, str] = {}
    if not path.exists():
        return result
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return result
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        # 去除首尾引号
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        if val and not (val.startswith("${") and val.endswith("}")):  # 非占位符
            result[key] = val
    return result


def _load_existing_secrets(project_root: Path) -> dict[str, str]:
    """
    从已有 .env 和 ENV_FILE 加载凭证（全基于项目根，跨平台）。
    优先级：project_root/.env > project_root/ENV_FILE（相对路径）> os.environ。
    """
    merged: dict[str, str] = {}
    sources: list[Path] = []
    env_path = project_root / ".env"
    if env_path.exists():
        sources.append(env_path)
    env_file = os.environ.get("ENV_FILE")
    if env_file:
        p = Path(env_file)
        if not p.is_absolute():
            p = (project_root / p).resolve()
        else:
            p = p.resolve()
        if p.exists() and p not in sources:
            sources.append(p)
    for path in sources:
        for k, v in _parse_env_file(path).items():
            if k in SECRET_KEYS and v and k not in merged:
                merged[k] = v
    for k in SECRET_KEYS:
        if k not in merged and k in os.environ and os.environ[k]:
            merged[k] = os.environ[k]
    return merged


def generate_secrets(project_root: Path, base_env: dict) -> dict:
    """
    防腐凭证中心：幂等解析敏感凭证。
    - POSTGRES_PASSWORD：已有则保留，绝对不覆盖。
    - TUNNEL_TOKEN：.env 中缺失时自动注入占位符。
    返回合并后的 env 字典，供 .env.j2 渲染。
    """
    existing = _load_existing_secrets(project_root)
    out = dict(base_env)

    # Guard: if .env is missing but PostgreSQL data directory exists, the
    # database was already initialized with the old password. Generating a new
    # one will break connectivity. Warn loudly so the operator can restore .env.
    env_path = project_root / ".env"
    pg_data_exists = (project_root / "volumes" / "postgres").exists() or (project_root / "postgres_data").exists()
    if not env_path.exists() and pg_data_exists and not existing.get("POSTGRES_PASSWORD"):
        import logging as _logging

        _logging.getLogger("iac_core.secrets").warning(
            "[IAC-SECRETS] WARNING: .env not found but PostgreSQL data directory exists. "
            "Generating a NEW POSTGRES_PASSWORD will break database connectivity. "
            "Restore .env from backup before proceeding, or reset the database intentionally."
        )

    # --- 密码生成：URL-safe 字符集，可安全嵌入 DSN ---
    out["postgres_password"] = existing.get("POSTGRES_PASSWORD") or (_generate_url_safe_password(32) if "POSTGRES_PASSWORD" in GENERATE_IF_MISSING else "")
    out["jwt_secret_current"] = existing.get("JWT_SECRET_CURRENT") or (_generate_url_safe_password(48) if "JWT_SECRET_CURRENT" in GENERATE_IF_MISSING else "")

    # 法典 §3.4 双轨轮转
    if base_env.get("_rotate_jwt"):
        # --rotate-jwt 模式：PREVIOUS ← 旧 CURRENT，CURRENT ← 全新随机
        old_current = existing.get("JWT_SECRET_CURRENT", "")
        out["jwt_secret_previous"] = old_current or out["jwt_secret_current"]
        out["jwt_secret_current"] = _generate_url_safe_password(48)
        print("[JWT-ROTATE] CURRENT→PREVIOUS 降级完成，新密钥已生成")
    else:
        # 正常模式：PREVIOUS 冷启动时 = CURRENT（保证旧 token 不会立即失效）
        out["jwt_secret_previous"] = existing.get("JWT_SECRET_PREVIOUS") or out["jwt_secret_current"]
    out["redis_acl_gateway_credential"] = existing.get("REDIS_ACL_GATEWAY_CREDENTIAL") or (
        _generate_url_safe_password(32) if "REDIS_ACL_GATEWAY_CREDENTIAL" in GENERATE_IF_MISSING else ""
    )
    out["redis_acl_readonly_credential"] = existing.get("REDIS_ACL_READONLY_CREDENTIAL") or (
        _generate_url_safe_password(32) if "REDIS_ACL_READONLY_CREDENTIAL" in GENERATE_IF_MISSING else ""
    )
    out["redis_password"] = out["redis_acl_gateway_credential"]

    # TUNNEL_TOKEN：IaC 唯一事实来源 — system.yaml 优先，.env 作为幂等回退
    # base_env["tunnel_token"] 来自 system.yaml→prepare_env()
    tunnel_val = base_env.get("tunnel_token") or ""
    if not tunnel_val or tunnel_val in ("your_cloudflare_token_here", TUNNEL_TOKEN_PLACEHOLDER):
        # system.yaml 无有效值时，回退到已有 .env
        tunnel_val = existing.get("TUNNEL_TOKEN") or existing.get("CLOUDFLARED_TUNNEL_TOKEN") or ""
    if not tunnel_val or tunnel_val in ("your_cloudflare_token_here", TUNNEL_TOKEN_PLACEHOLDER):
        tunnel_val = TUNNEL_TOKEN_PLACEHOLDER
    out["tunnel_token"] = tunnel_val

    # AI_BACKEND_URL
    # Legacy cleanup: historical default "http://ollama:11434" should not remain implicit in gateway-kernel.
    ai_backend_existing = (existing.get("AI_BACKEND_URL") or "").strip()
    if ai_backend_existing == "http://ollama:11434" and not str(base_env.get("ai_backend_url") or "").strip():
        ai_backend_existing = ""
    out["ai_backend_url"] = ai_backend_existing or str(base_env.get("ai_backend_url") or DEFAULT_VALUES.get("AI_BACKEND_URL", ""))

    # GF_ADMIN_PASSWORD（法典 §7.1.2：严禁硬编码 admin）
    out["gf_admin_password"] = existing.get("GF_ADMIN_PASSWORD") or (_generate_url_safe_password(16) if "GF_ADMIN_PASSWORD" in GENERATE_IF_MISSING else "")

    # --- 法典 §1.2: POSTGRES_DSN 从原子变量自动构造 ---
    pg_host = out.get("postgres_host", "postgres")
    pg_port = out.get("postgres_port", 5432)
    out["postgres_dsn"] = f"postgresql://{out['postgres_user']}:{out['postgres_password']}" f"@{pg_host}:{pg_port}/{out['postgres_db']}"

    return out
