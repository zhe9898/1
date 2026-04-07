"""
iac_core.exceptions — 统一异常层级。

核心库函数抛出具体异常，CLI 壳统一捕获后 sys.exit(1)。
这保证核心库可被单元测试、CI、installer 等第三方安全调用。
"""

from __future__ import annotations

from dataclasses import dataclass, field


class IaCError(Exception):
    """iac_core 所有异常的基类。"""


class ConfigLoadError(IaCError):
    """YAML 配置加载或解析失败。"""

    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"配置加载失败 [{path}]: {reason}")


class SchemaValidationError(IaCError):
    """Tier 1 必填字段缺失或类型错误。"""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(f"Schema 校验失败: {len(errors)} 条错误 — " + "; ".join(errors[:3]) + ("..." if len(errors) > 3 else ""))


class SecurityValidationError(IaCError):
    """Tier 2 安全/策略校验失败。"""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(f"安全校验失败: {len(errors)} 条错误 — " + "; ".join(errors[:3]) + ("..." if len(errors) > 3 else ""))


@dataclass(frozen=True)
class PolicyViolation:
    """策略引擎评估结果中的单条违规记录。"""

    rule_id: str
    severity: str  # "fail" | "warn"
    service: str
    message: str
    description: str = ""


class PolicyValidationError(IaCError):
    """策略引擎评估发现 severity=fail 的违规。"""

    def __init__(self, violations: list[PolicyViolation]) -> None:
        self.violations = violations
        super().__init__(
            f"策略校验失败: {len(violations)} 条违规 — "
            + "; ".join(f"[{v.rule_id}] {v.message}" for v in violations[:3])
            + ("..." if len(violations) > 3 else "")
        )


class MigrationError(IaCError):
    """版本迁移失败（缺口、循环或迁移函数异常）。"""

    def __init__(self, from_version: int, to_version: int, reason: str) -> None:
        self.from_version = from_version
        self.to_version = to_version
        self.reason = reason
        super().__init__(f"迁移 v{from_version}→v{to_version} 失败: {reason}")


class TemplateRenderError(IaCError):
    """Jinja2 模板渲染失败。"""

    def __init__(self, template_name: str, reason: str) -> None:
        self.template_name = template_name
        self.reason = reason
        super().__init__(f"模板渲染失败 [{template_name}]: {reason}")


@dataclass(frozen=True)
class LintResult:
    """config_lint 的结构化返回值。"""

    config: dict
    warnings: list[str] = field(default_factory=list)
    policy_violations: list[PolicyViolation] = field(default_factory=list)
