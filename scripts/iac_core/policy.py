"""
iac_core.policy — 声明式策略引擎。

将 Tier 2 安全/可靠性规则从硬编码 Python if 抽离为外部 YAML 策略文件，
实现"改一处策略文件、零改编译器代码"的运维红线管理。

策略文件格式 (iac/policy/core.yaml):
    policy_version: 1
    rules:
      - id: NET-001
        tier: 2
        selector: { service_names: [postgres, redis] }
        assert: { networks_exclude: [frontend_net] }
        severity: fail
        description: "..."

内置 assert 类型:
    networks_exclude    — 服务的 networks 不得包含指定网络
    volumes_not_empty   — 服务必须挂载至少一个 volume
    ulimits_nofile_min  — ulimits.nofile.soft/hard >= 阈值
    oom_score_adj       — oom_score_adj 必须等于指定值
    field_exists        — 服务中必须存在指定字段
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from scripts.iac_core.exceptions import PolicyValidationError, PolicyViolation

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 策略加载
# ---------------------------------------------------------------------------


def load_policy(path: Path) -> dict[str, Any]:
    """
    加载策略 YAML 文件。

    Args:
        path: 策略文件路径。

    Returns:
        解析后的策略字典。

    Raises:
        FileNotFoundError: 策略文件不存在。
        yaml.YAMLError: YAML 格式非法。
    """
    if not path.exists():
        msg = f"策略文件不存在: {path}"
        raise FileNotFoundError(msg)
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        msg = f"策略文件根节点必须为字典: {path}"
        raise ValueError(msg)
    return data


def load_default_policy() -> dict[str, Any]:
    """
    加载项目默认策略文件 (iac/policy/core.yaml)。

    按优先级搜索:
    1. {project_root}/iac/policy/core.yaml
    2. 内置 fallback (硬编码最小策略)

    Returns:
        策略字典。
    """
    # 从当前文件定位项目根: scripts/iac_core/policy.py → scripts/iac_core → scripts → root
    project_root = Path(__file__).resolve().parent.parent.parent
    policy_path = project_root / "iac" / "policy" / "core.yaml"
    if policy_path.exists():
        logger.debug("Loading policy from %s", policy_path)
        return load_policy(policy_path)

    logger.info("使用内置 fallback 策略（iac/policy/core.yaml 不存在）")
    return _builtin_fallback_policy()


def _builtin_fallback_policy() -> dict[str, Any]:
    """
    内置最小策略 fallback。

    当 iac/policy/core.yaml 未部署时使用，确保核心安全规则不被绕过。
    policy_version=2 与外部 core.yaml 保持一致。
    """
    return {
        "policy_version": 2,
        "rules": [
            {
                "id": "NET-001",
                "tier": 2,
                "description": "数据库/缓存服务禁止接入 frontend_net（法典 §3.3）",
                "selector": {"service_names": ["postgres", "redis", "docker-proxy"]},
                "assert": {"networks_exclude": ["frontend_net"]},
                "severity": "fail",
            },
            {
                "id": "DATA-001",
                "tier": 2,
                "description": "有状态服务必须挂载持久卷",
                "selector": {"service_names": ["postgres", "redis"]},
                "assert": {"volumes_not_empty": True},
                "severity": "fail",
            },
            {
                "id": "SEC-001",
                "tier": 2,
                "description": "read_only: true 必须配套 tmpfs（法典 §3.4）",
                "selector": {},
                "assert": {"read_only_requires_tmpfs": True},
                "severity": "fail",
            },
            {
                "id": "SYS-001",
                "tier": 2,
                "description": "核心服务 ulimits.nofile >= 65536（法典 §3.3）",
                "selector": {"service_names": ["gateway", "redis"]},
                "assert": {"ulimits_nofile_min": 65536},
                "severity": "warn",
            },
            {
                "id": "SYS-002",
                "tier": 2,
                "description": "核心服务 oom_score_adj == -999（法典 §3.3）",
                "selector": {
                    "service_names": [
                        "gateway",
                        "redis",
                        "watchdog",
                        "docker-proxy",
                    ],
                },
                "assert": {"oom_score_adj": -999},
                "severity": "warn",
            },
        ],
    }


# ---------------------------------------------------------------------------
# 策略评估引擎
# ---------------------------------------------------------------------------

# assert 处理器注册表
_ASSERT_HANDLERS: dict[str, Any] = {}


def _register_assert(name: str) -> None:
    """装饰器：注册 assert 处理器。"""

    def decorator(fn) -> None:  # type: ignore[no-untyped-def]
        _ASSERT_HANDLERS[name] = fn
        return fn  # type: ignore[no-any-return]

    return decorator  # type: ignore[return-value]


@_register_assert("networks_exclude")  # type: ignore[func-returns-value]
def _assert_networks_exclude(
    svc: dict[str, Any],
    service_name: str,
    params: list[str],
    rule: dict[str, Any],
) -> PolicyViolation | None:
    """服务的 networks 不得包含指定网络。"""
    nets = svc.get("networks") or []
    for forbidden in params:
        if forbidden in nets:
            return PolicyViolation(
                rule_id=rule["id"],
                severity=rule.get("severity", "fail"),
                service=service_name,
                message=f"services.{service_name}.networks 包含 {forbidden}",
                description=rule.get("description", ""),
            )
    return None


@_register_assert("volumes_not_empty")  # type: ignore[func-returns-value]
def _assert_volumes_not_empty(
    svc: dict[str, Any],
    service_name: str,
    params: bool,
    rule: dict[str, Any],
) -> PolicyViolation | None:
    """服务必须挂载至少一个 volume。"""
    if params and not (svc.get("volumes") or []):
        return PolicyViolation(
            rule_id=rule["id"],
            severity=rule.get("severity", "fail"),
            service=service_name,
            message=f"services.{service_name}.volumes 为空 — 有状态服务必须挂载持久卷",
            description=rule.get("description", ""),
        )
    return None


@_register_assert("ulimits_nofile_min")  # type: ignore[func-returns-value]
def _assert_ulimits_nofile_min(
    svc: dict[str, Any],
    service_name: str,
    params: int,
    rule: dict[str, Any],
) -> PolicyViolation | None:
    """ulimits.nofile.soft/hard >= 阈值。编译器兜底注入，此处仅校验源头声明。"""
    ulimits = (svc.get("ulimits") or {}).get("nofile") or {}
    if isinstance(ulimits, dict):
        soft = ulimits.get("soft", 0)
        hard = ulimits.get("hard", 0)
        if soft < params or hard < params:
            return PolicyViolation(
                rule_id=rule["id"],
                severity=rule.get("severity", "warn"),
                service=service_name,
                message=(f"services.{service_name}.ulimits.nofile " f"(soft={soft}, hard={hard}) < {params}，编译器将自动注入"),
                description=rule.get("description", ""),
            )
    elif not ulimits:
        # 未声明 ulimits — 编译器兜底
        return PolicyViolation(  # type: ignore[unreachable]
            rule_id=rule["id"],
            severity=rule.get("severity", "warn"),
            service=service_name,
            message=(f"services.{service_name} 未声明 ulimits.nofile，" f"编译器将自动注入 >= {params}"),
            description=rule.get("description", ""),
        )
    return None


@_register_assert("oom_score_adj")  # type: ignore[func-returns-value]
def _assert_oom_score_adj(
    svc: dict[str, Any],
    service_name: str,
    params: int,
    rule: dict[str, Any],
) -> PolicyViolation | None:
    """oom_score_adj 必须等于指定值。编译器兜底注入。"""
    actual = svc.get("oom_score_adj")
    if actual is None:
        return PolicyViolation(
            rule_id=rule["id"],
            severity=rule.get("severity", "warn"),
            service=service_name,
            message=(f"services.{service_name} 未声明 oom_score_adj，" f"编译器将自动注入 {params}"),
            description=rule.get("description", ""),
        )
    if actual != params:
        return PolicyViolation(
            rule_id=rule["id"],
            severity=rule.get("severity", "fail"),
            service=service_name,
            message=(f"services.{service_name}.oom_score_adj={actual}，" f"策略要求 {params}"),
            description=rule.get("description", ""),
        )
    return None


@_register_assert("field_exists")  # type: ignore[func-returns-value]
def _assert_field_exists(
    svc: dict[str, Any],
    service_name: str,
    params: str,
    rule: dict[str, Any],
) -> PolicyViolation | None:
    """服务中必须存在指定字段（dotted path）。"""
    keys = str(params).split(".")
    node: Any = svc
    for k in keys:
        if isinstance(node, dict):
            node = node.get(k)
        else:
            node = None
            break
    if node is None:
        return PolicyViolation(
            rule_id=rule["id"],
            severity=rule.get("severity", "fail"),
            service=service_name,
            message=f"services.{service_name} 缺失字段: {params}",
            description=rule.get("description", ""),
        )
    return None


@_register_assert("read_only_requires_tmpfs")  # type: ignore[func-returns-value]
def _assert_read_only_requires_tmpfs(
    svc: dict[str, Any],
    service_name: str,
    params: bool,
    rule: dict[str, Any],
) -> PolicyViolation | None:
    """
    read_only: true 必须配套 tmpfs 声明。

    法典 §3.4: 安全容器规范要求根文件系统只读 (read_only: true)，
    但许多服务需要写入 /tmp，所以必须同时声明 tmpfs。
    """
    if not params:
        return None
    is_read_only = svc.get("read_only") is True
    # 也检查 security.apply_baseline（编译器会注入 read_only + tmpfs）
    security = svc.get("security") or {}
    if security.get("apply_baseline"):
        # apply_baseline 已含 tmpfs 注入，跳过
        return None
    if is_read_only and not svc.get("tmpfs"):
        return PolicyViolation(
            rule_id=rule["id"],
            severity=rule.get("severity", "fail"),
            service=service_name,
            message=(f"services.{service_name} 声明了 read_only: true" f" 但未声明 tmpfs — 容器将无法写入任何文件"),
            description=rule.get("description", ""),
        )
    return None


@_register_assert("field_recommended")  # type: ignore[func-returns-value]
def _assert_field_recommended(
    svc: dict[str, Any],
    service_name: str,
    params: str,
    rule: dict[str, Any],
) -> PolicyViolation | None:
    """
    Tier 3 建议项：检查服务是否声明了推荐字段。

    与 field_exists 类似，但语义更轻：仅产出 warn 级别违规，
    编译器对缺失字段自动兜底注入。
    """
    keys = str(params).split(".")
    node: Any = svc
    for k in keys:
        if isinstance(node, dict):
            node = node.get(k)
        else:
            node = None
            break
    if node is None:
        return PolicyViolation(
            rule_id=rule["id"],
            severity=rule.get("severity", "warn"),
            service=service_name,
            message=(f"services.{service_name} 未声明 {params}" f"（编译器兜底注入默认值）"),
            description=rule.get("description", ""),
        )
    return None


# 策略引擎支持的最低 policy_version
_MIN_POLICY_VERSION = 1


def _validate_policy_version(policy: dict[str, Any]) -> None:
    """
    校验 policy_version 字段。

    Args:
        policy: 策略字典。

    Raises:
        PolicyValidationError: policy_version 缺失或低于最低要求。
    """
    version = policy.get("policy_version")
    if version is None:
        raise PolicyValidationError(
            [
                PolicyViolation(
                    rule_id="POLICY-VERSION",
                    severity="fail",
                    service="(global)",
                    message="策略文件缺失 policy_version 字段",
                    description="策略文件必须声明 policy_version",
                ),
            ]
        )
    try:
        ver_int = int(version)
    except (ValueError, TypeError) as exc:
        raise PolicyValidationError(
            [
                PolicyViolation(
                    rule_id="POLICY-VERSION",
                    severity="fail",
                    service="(global)",
                    message=f"policy_version 无法解析为整数: {version}",
                    description="policy_version 必须为正整数",
                ),
            ]
        ) from exc
    if ver_int < _MIN_POLICY_VERSION:
        raise PolicyValidationError(
            [
                PolicyViolation(
                    rule_id="POLICY-VERSION",
                    severity="fail",
                    service="(global)",
                    message=f"policy_version={ver_int} 低于最低要求 {_MIN_POLICY_VERSION}",
                    description="请升级策略文件",
                ),
            ]
        )


def evaluate_policy(
    config: dict[str, Any],
    policy: dict[str, Any],
) -> list[PolicyViolation]:
    """
    对配置执行策略评估。

    Args:
        config: 解析后的 system.yaml 配置。
        policy: 策略字典（load_policy 加载）。

    Returns:
        PolicyViolation 列表（可能为空）。

    Raises:
        PolicyValidationError: policy_version 不合法。
    """
    _validate_policy_version(policy)

    violations: list[PolicyViolation] = []
    services = config.get("services") or {}
    rules = policy.get("rules") or []

    for rule in rules:
        rule_id = rule.get("id", "UNKNOWN")
        selector = rule.get("selector") or {}
        assertions = rule.get("assert") or {}

        # 目标服务匹配
        target_names: list[str] = selector.get("service_names") or []
        if not target_names:
            # 无 selector = 全服务扫描
            target_names = list(services.keys())

        for svc_name in target_names:
            svc = services.get(svc_name)
            if not isinstance(svc, dict):
                continue
            if svc.get("enabled") is False:
                continue
            if svc.get("runtime") == "host":
                continue

            # 逐条 assert 评估
            for assert_type, assert_params in assertions.items():
                handler = _ASSERT_HANDLERS.get(assert_type)
                if handler is None:
                    logger.warning(
                        "策略规则 %s 使用了未知的 assert 类型: %s",
                        rule_id,
                        assert_type,
                    )
                    continue
                violation = handler(svc, svc_name, assert_params, rule)
                if violation is not None:
                    violations.append(violation)

    return violations


def evaluate_and_enforce(
    config: dict[str, Any],
    policy: dict[str, Any] | None = None,
) -> list[PolicyViolation]:
    """
    评估策略并对 fail 级违规抛出 PolicyValidationError。

    warn 级违规仅 logger.warning，不抛异常。

    Args:
        config: 解析后的配置。
        policy: 策略字典；None 时加载默认策略。

    Returns:
        所有违规列表（包括 warn 级）。

    Raises:
        PolicyValidationError: 存在 severity=fail 的违规。
    """
    if policy is None:
        policy = load_default_policy()

    violations = evaluate_policy(config, policy)

    fail_violations = [v for v in violations if v.severity == "fail"]
    warn_violations = [v for v in violations if v.severity == "warn"]

    for w in warn_violations:
        logger.warning("[policy] [%s] %s: %s", w.rule_id, w.service, w.message)

    if fail_violations:
        raise PolicyValidationError(fail_violations)

    return violations
