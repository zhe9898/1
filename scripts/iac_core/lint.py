"""
iac_core.lint — 三层 Schema 校验 (ADR 0011)。

Tier 1 (FAIL):     必填硬门槛 — 缺失时抛出 SchemaValidationError
Tier 2 (SECURITY):  安全/策略 — 由 policy engine 驱动，违规时抛出 PolicyValidationError
Tier 3 (WARN):      建议项 — 输出 logging.warning，不抛异常

核心库函数不调用 sys.exit(1)，由 CLI 壳统一捕获异常。

用法:
    from scripts.iac_core.lint import config_lint
    result = config_lint(Path("system.yaml"))
    # result.config  — 解析后的配置
    # result.warnings — Tier 3 warning 消息列表
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from scripts.iac_core.exceptions import (
    ConfigLoadError,
    LintResult,
    PolicyViolation,
    SchemaValidationError,
)
from scripts.iac_core.host_service_contracts import validate_host_service_contract

logger = logging.getLogger(__name__)


# ===========================================================================
# Tier 1: 必填硬门槛（缺失 → SchemaValidationError）
# ===========================================================================

REQUIRED_SCHEMA: dict[str, Any] = {
    "services": {
        "postgres": {"image": str},
        "redis": {"image": str},
        "gateway": {},
    },
    "network": {"domain": str},
    # deployment.profile must be declared to drive profile/pack resolution
    "deployment": {"profile": str},
}
"""
Tier 1 必填 Schema。

叶子节点为 type 时校验类型；为 None 时仅校验存在性。
嵌套 dict 递归校验。

注意:
- version 由 _check_version_semantic() 独立校验（存在性+语义），不在此 dict 中重复。
- services.<name>.image 与 services.<name>.build 是二选一关系，
  由 _check_image_or_build() 强制校验。
- caddy 在某些部署场景下可选，移入 RECOMMENDED_FIELDS。
"""


def _validate_schema(
    data: dict[str, Any],
    schema: dict[str, Any],
    path: str = "",
) -> list[str]:
    """
    递归校验 data 是否满足 schema 定义的必填字段。

    Returns:
        错误消息列表；空列表表示通过。
    """
    errors: list[str] = []
    for key, expected in schema.items():
        full_path = f"{path}.{key}" if path else key
        value = data.get(key)

        if value is None:
            errors.append(f"缺失必填字段: {full_path}")
            continue

        if isinstance(expected, dict):
            if not isinstance(value, dict):
                errors.append(f"{full_path} 应为 dict，实际为 {type(value).__name__}")
            else:
                errors.extend(_validate_schema(value, expected, full_path))
        elif isinstance(expected, type):
            if not isinstance(value, expected):
                errors.append(f"{full_path} 应为 {expected.__name__}，" f"实际为 {type(value).__name__}")
    return errors


def _check_version_semantic(data: dict[str, Any]) -> list[str]:
    """
    版本校验：必须存在且 >= 2.0（语义比较）。

    同时承担存在性 + 语义双重校验，避免 REQUIRED_SCHEMA 重复检查。
    Requires x.y format (e.g. "2.0") not just "2".
    """
    version = data.get("version")
    if version is None:
        return ["缺失必填字段: version（法典 §1.2 要求 V2.0+）"]
    version_str = str(version).strip()
    # Require at least major.minor format
    if "." not in version_str:
        return [f"版本号格式无效: '{version_str}'（必须为 x.y 格式，如 '2.0'）"]
    try:
        version_tuple = tuple(int(x) for x in version_str.split("."))
    except (ValueError, TypeError):
        return [f"无法解析版本号: {version_str}（应为 x.y 格式）"]
    if version_tuple < (2, 0):
        return [f"版本 {version_str} 低于最低要求 2.0"]
    return []


def _check_image_or_build(data: dict[str, Any]) -> list[str]:
    """每个 service 必须有 image 或 build（二选一）。"""
    errors: list[str] = []
    services = data.get("services") or {}
    for name, svc in services.items():
        if not isinstance(svc, dict):
            continue
        if svc.get("enabled") is False:
            continue
        if svc.get("runtime") == "host":
            errors.extend(validate_host_service_contract(name, svc))
            continue
        has_image = bool(svc.get("image"))
        has_build = bool(svc.get("build"))
        if not has_image and not has_build:
            errors.append(f"services.{name}: 必须提供 image 或 build（二选一）")
    return errors


# 合法的 restart 策略值
_VALID_RESTART_POLICIES: frozenset[str] = frozenset(
    {
        "unless-stopped",
        "always",
        "on-failure",
        "no",
    }
)


def _check_backend_net_internal(data: dict[str, Any]) -> list[str]:
    """
    network.planes.backend_net.internal 必须为 true。

    法典 §3.3: backend_net 必须声明 internal: true，
    数据库端口仅限网关访问，严禁暴露到外部网络。
    """
    network = data.get("network") or {}
    planes = network.get("planes") or {}
    backend = planes.get("backend_net")

    if not isinstance(backend, dict):
        # planes 未定义 → 编译器 extract_networks 会兜底，降为 warn 级
        return []

    if not backend.get("internal"):
        return ["network.planes.backend_net.internal 必须为 true" "（法典 §3.3: 数据库网段强制隔离）"]
    return []


# ===========================================================================
# Tier 3: 建议项（仅 warn，不抛异常）
# ===========================================================================

RECOMMENDED_FIELDS: list[str] = [
    "capabilities.storage.media_path",
    "sentinel.mount_container_map",
    "sentinel.watch_targets",
    "sentinel.switch_container_map",
]


def _collect_warnings(data: dict[str, Any]) -> list[str]:
    """
    收集所有 Tier 3 建议项警告消息。

    返回 warning 字符串列表（不直接 print/log，交给调用方决定输出方式）。
    """
    warnings: list[str] = []

    # 推荐字段
    for dotted in RECOMMENDED_FIELDS:
        keys = dotted.split(".")
        node: Any = data
        for k in keys:
            if isinstance(node, dict):
                node = node.get(k)
            else:
                node = None
                break
        if node is None:
            warnings.append(f"推荐字段 {dotted} 未配置，部分功能可能不可用。")

    # healthcheck 完整性
    required_keys = frozenset({"interval", "timeout", "retries", "start_period"})
    services = data.get("services") or {}
    for name, svc in services.items():
        if not isinstance(svc, dict) or svc.get("enabled") is False:
            continue
        hc = svc.get("healthcheck")
        if isinstance(hc, dict) and hc.get("test"):
            missing = required_keys - set(hc.keys())
            if missing:
                warnings.append(f"services.{name}.healthcheck 缺失 " f"{', '.join(sorted(missing))}，编译器将使用默认值。")

    # stop_grace_period
    for name, svc in services.items():
        if not isinstance(svc, dict) or svc.get("enabled") is False:
            continue
        if svc.get("runtime") == "host":
            continue
        if not svc.get("stop_grace_period"):
            warnings.append(f"services.{name} 未声明 stop_grace_period，" f"编译器将使用默认值。")

    # deploy.resources.limits
    for name, svc in services.items():
        if not isinstance(svc, dict) or svc.get("enabled") is False:
            continue
        deploy = svc.get("deploy")
        if not isinstance(deploy, dict):
            continue
        resources = deploy.get("resources")
        if isinstance(resources, dict) and not resources.get("limits"):
            warnings.append(f"services.{name}.deploy.resources.limits " "未声明 CPU/MEM 限制。")

    # GPU 超时保护
    cap = (data.get("capabilities") or {}).get("gpu")
    if cap:
        gateway_env = ((data.get("services") or {}).get("gateway") or {}).get("environment") or {}
        if "MULTIMODAL_TIMEOUT_SECONDS" not in gateway_env:
            warnings.append("capabilities.gpu 已声明但 " "services.gateway.environment.MULTIMODAL_TIMEOUT_SECONDS 未配置，" "GPU 推理可能无超时保护。")

    # 备份字段
    restic = (data.get("services") or {}).get("restic")
    if isinstance(restic, dict) and restic.get("enabled") is not False:
        backup = data.get("backup") or {}
        required = ("s3_endpoint", "s3_bucket", "retention_days")
        missing_backup = [f for f in required if not backup.get(f)]
        if missing_backup:
            warnings.append(f"services.restic 已启用但 backup 缺失 " f"{', '.join(missing_backup)} — 备份可能无法正常执行。")

    # 日志 + container_name + networks + restart（合并 per-service 汇总，降噪）
    svc_missing_fields: dict[str, list[str]] = {}
    for name, svc in services.items():
        if not isinstance(svc, dict) or svc.get("enabled") is False:
            continue
        gaps: list[str] = []
        if svc.get("runtime") != "host" and not svc.get("logging"):
            gaps.append("logging")
        if svc.get("runtime") != "host" and not svc.get("container_name"):
            gaps.append("container_name")
        nets = svc.get("networks")
        if svc.get("runtime") != "host" and (not nets or (isinstance(nets, list) and len(nets) == 0)):
            gaps.append("networks")
        restart = svc.get("restart")
        if not restart:
            gaps.append("restart")
        elif str(restart) not in _VALID_RESTART_POLICIES:
            gaps.append(f"restart={restart}(非常规)")
        if gaps:
            svc_missing_fields[name] = gaps

    for svc_name, gaps in svc_missing_fields.items():
        warnings.append(f"services.{svc_name}: 未声明 {', '.join(gaps)}" f"（编译器兜底注入）")

    return warnings


# ===========================================================================
# 主入口
# ===========================================================================


def config_lint(
    path: Path,
    *,
    policy_violations: list[PolicyViolation] | None = None,
) -> LintResult:
    """
    三层 config-lint：校验 YAML + Schema + 策略 + 建议项。

    Tier 1 错误 → 抛出 SchemaValidationError
    Tier 2 错误 → 由 policy engine 外部传入（若有 fail 级违规，由调用方处理）
    Tier 3 警告 → 收集返回，不抛异常

    Args:
        path: system.yaml 文件路径。
        policy_violations: 外部 policy engine 评估结果（可选）。

    Returns:
        LintResult(config, warnings, policy_violations)。

    Raises:
        ConfigLoadError: 文件读取或 YAML 解析失败。
        SchemaValidationError: Tier 1 必填字段校验失败。
    """
    # 1. 文件读取
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ConfigLoadError(str(path), str(e)) from e

    # 2. YAML 解析
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ConfigLoadError(str(path), f"YAML 格式非法: {e}") from e

    if not isinstance(data, dict):
        raise ConfigLoadError(
            str(path),
            f"根节点必须为字典，实际为 {type(data).__name__}",
        )

    # ===== Tier 1: 必填硬门槛 =====
    errors: list[str] = []
    errors.extend(_validate_schema(data, REQUIRED_SCHEMA))
    errors.extend(_check_version_semantic(data))
    errors.extend(_check_image_or_build(data))
    errors.extend(_check_backend_net_internal(data))

    if errors:
        raise SchemaValidationError(errors)

    # ===== Tier 3: 建议项 (warn only) =====
    # 注意: 此处仅收集不输出——由 compiler.py 统一 logger.warning 避免双重日志
    warnings = _collect_warnings(data)

    return LintResult(
        config=data,
        warnings=warnings,
        policy_violations=policy_violations or [],
    )
