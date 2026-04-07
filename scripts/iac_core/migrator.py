"""
iac_core.migrator — 注册表式版本迁移。

设计:
- 每个迁移步骤是纯函数（输入 cfg → 输出 (cfg, change_log)），便于测试和回滚。
- @register_migration 装饰器自动注册到全局 _MIGRATIONS 注册表。
- 启动时（调用 migrate_config 时）校验迁移图：检测缺口和循环。
- 支持 dry_run=True 输出每步变更摘要但不实际修改。
"""

from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from scripts.iac_core.exceptions import MigrationError

logger = logging.getLogger(__name__)

CURRENT_CONFIG_VERSION = 2

# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------

MigrationFn = Callable[[dict[str, Any]], tuple[dict[str, Any], str]]


@dataclass(frozen=True)
class MigrationStep:
    """单个迁移步骤的元数据。"""

    from_version: int
    to_version: int
    fn: MigrationFn
    description: str


_MIGRATIONS: dict[int, MigrationStep] = {}


def register_migration(
    from_v: int,
    to_v: int,
    description: str = "",
) -> Callable[[MigrationFn], MigrationFn]:
    """
    装饰器：注册一个版本迁移步骤。

    Args:
        from_v: 源版本号。
        to_v: 目标版本号（必须 > from_v）。
        description: 迁移摘要，供日志和 dry-run 输出。

    Raises:
        ValueError: from_v >= to_v 或重复注册。

    Usage:
        @register_migration(from_v=1, to_v=2, description="Add backup_tier")
        def _migrate_v1_to_v2(cfg: dict) -> tuple[dict, str]:
            ...
    """
    if to_v <= from_v:
        msg = f"无效迁移: v{from_v}→v{to_v}（目标版本必须大于源版本）"
        raise ValueError(msg)
    if from_v in _MIGRATIONS:
        msg = f"重复注册迁移 v{from_v}→（已存在 v{from_v}→v{_MIGRATIONS[from_v].to_version}）"
        raise ValueError(msg)

    def decorator(fn: MigrationFn) -> MigrationFn:
        _MIGRATIONS[from_v] = MigrationStep(
            from_version=from_v,
            to_version=to_v,
            fn=fn,
            description=description or fn.__doc__ or "",
        )
        return fn

    return decorator


# ---------------------------------------------------------------------------
# 迁移图校验
# ---------------------------------------------------------------------------


def _validate_migration_graph(
    start: int,
    target: int,
) -> list[MigrationStep]:
    """
    校验从 start 到 target 的迁移路径完整性。

    Returns:
        有序的迁移步骤列表。

    Raises:
        MigrationError: 路径不存在（缺口）或存在循环。
    """
    if start >= target:
        return []

    path: list[MigrationStep] = []
    visited: set[int] = set()
    current = start

    while current < target:
        if current in visited:
            raise MigrationError(
                start,
                target,
                f"检测到循环: v{current} 被重复访问",
            )
        visited.add(current)

        step = _MIGRATIONS.get(current)
        if step is None:
            raise MigrationError(
                start,
                target,
                f"迁移路径断裂: 无 v{current}→v{current + 1} 的迁移函数",
            )
        path.append(step)
        current = step.to_version

    return path


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def migrate_config(
    raw_config: dict[str, Any],
    *,
    dry_run: bool = False,
) -> tuple[dict[str, Any], list[str]]:
    """
    根据 config_version 执行链式版本迁移。

    不修改 raw_config；返回 (迁移后配置, 变更日志列表)。

    Args:
        raw_config: 原始配置字典。
        dry_run: True 时只输出计划但不执行迁移。

    Returns:
        (migrated_config, migration_log)

    Raises:
        MigrationError: 迁移路径不完整或迁移函数执行失败。
    """
    config = deepcopy(raw_config)
    migration_log: list[str] = []

    version = config.get("config_version")
    if not isinstance(version, int) or version < 1:
        version = 1

    if version >= CURRENT_CONFIG_VERSION:
        migration_log.append(f"Config version is already v{version}")
        return config, migration_log

    # 校验迁移图完整性
    path = _validate_migration_graph(version, CURRENT_CONFIG_VERSION)

    if dry_run:
        migration_log.append(f"[DRY-RUN] 迁移计划: v{version} → v{CURRENT_CONFIG_VERSION} " f"({len(path)} 步)")
        for step in path:
            migration_log.append(f"  v{step.from_version}→v{step.to_version}: {step.description}")
        return config, migration_log

    # 逐步执行
    for step in path:
        try:
            config, changes = step.fn(config)
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
            raise MigrationError(
                step.from_version,
                step.to_version,
                f"迁移函数异常: {e}",
            ) from e
        migration_log.append(f"Migrated v{step.from_version}→v{step.to_version}: {changes}")

    final_version = config.get("config_version", CURRENT_CONFIG_VERSION)
    if final_version < CURRENT_CONFIG_VERSION:
        logger.warning(
            "Config version v%d not fully migrated to v%d",
            final_version,
            CURRENT_CONFIG_VERSION,
        )

    return config, migration_log


def migrate_and_persist(
    config_path: "Path",
    raw_config: dict[str, Any],
    *,
    dry_run: bool = False,
) -> tuple[dict[str, Any], list[str]]:
    """
    原子化迁移管道：备份 → 迁移 → 回写。

    IaC 铁律：迁移结果必须持久化到 system.yaml，
    否则下次编译器启动又会从旧版本重新迁移。

    Args:
        config_path: system.yaml 物理路径（用于备份和回写）。
        raw_config: 已解析的配置字典。
        dry_run: True 时只输出计划，不备份也不回写。

    Returns:
        (migrated_config, migration_log)

    Raises:
        MigrationError: 迁移失败。
        OSError: 文件 I/O 失败。
    """
    config_path = Path(config_path)
    migrated, log = migrate_config(raw_config, dry_run=dry_run)

    # dry_run 或无实际迁移 → 不写文件
    if dry_run or migrated.get("config_version") == raw_config.get("config_version"):
        return migrated, log

    # ① 备份原文件（同目录 .bak.{version}）
    current_v = raw_config.get("config_version", 1)
    backup_path = config_path.with_suffix(f".yaml.bak.v{current_v}")
    try:
        import shutil

        shutil.copy2(config_path, backup_path)
        log.append(f"Backup: {config_path} → {backup_path}")
    except OSError as e:
        logger.error("备份失败，中止迁移: %s", e)
        raise

    # ② 回写迁移结果（优先 ruamel 保留注释，降级 PyYAML）
    try:
        try:
            import ruamel.yaml as ryaml

            yaml_engine = ryaml.YAML()
            yaml_engine.preserve_quotes = True
            yaml_engine.default_flow_style = False
            # ruamel 需要从原文件重新加载以保留注释结构
            with open(config_path, encoding="utf-8") as f:
                original_data = yaml_engine.load(f)
            # 深度合并迁移后的值到 ruamel 数据结构
            _deep_update(original_data, migrated)
            with open(config_path, "w", encoding="utf-8") as f:
                yaml_engine.dump(original_data, f)
            log.append(f"Write-back (ruamel.yaml): {config_path}")
        except ImportError:
            import yaml as pyyaml

            logger.warning("ruamel.yaml 不可用，回写将丢失注释格式")
            with open(config_path, "w", encoding="utf-8") as f:
                pyyaml.dump(
                    migrated,
                    f,
                    default_flow_style=False,
                    allow_unicode=True,
                    sort_keys=False,
                )
            log.append(f"Write-back (PyYAML, comments lost): {config_path}")
    except OSError as write_exc:
        # Write-back failed — restore from backup to leave system.yaml intact
        logger.error("迁移回写失败，从备份恢复: %s → %s", backup_path, config_path)
        try:
            import shutil as _shutil
            _shutil.copy2(backup_path, config_path)
            log.append(f"Rollback: restored {config_path} from {backup_path}")
        except OSError as restore_exc:
            logger.critical(
                "回写失败且备份恢复也失败! system.yaml 可能已损坏。"
                "手动从 %s 恢复。write_error=%s restore_error=%s",
                backup_path, write_exc, restore_exc,
            )
        raise MigrationError(f"迁移回写失败（已尝试从 {backup_path} 恢复）: {write_exc}") from write_exc

    target_v = migrated.get("config_version", "?")
    log.append(f"Migration persisted: v{current_v} → v{target_v}")
    return migrated, log


def _deep_update(base: dict, updates: dict) -> None:
    """递归合并 updates 到 base，保留 base 的 ruamel 注释节点。"""
    for key, value in updates.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_update(base[key], value)
        else:
            base[key] = value


# ---------------------------------------------------------------------------
# 迁移步骤定义
# ---------------------------------------------------------------------------


@register_migration(from_v=1, to_v=2, description="Add default backup_tier to storage items")
def _migrate_v1_to_v2(
    old_cfg: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """v1 → v2 迁移：为 storage 下各项补充默认 backup_tier。"""
    new_cfg = deepcopy(old_cfg)
    new_cfg["config_version"] = 2
    changes_parts: list[str] = []
    if "storage" in new_cfg and isinstance(new_cfg["storage"], dict):
        for name, stor in new_cfg["storage"].items():
            if isinstance(stor, dict) and "backup_tier" not in stor:
                stor["backup_tier"] = "critical"
                changes_parts.append(name)
    changes = "Added default backup_tier=critical to storage items: " + ", ".join(changes_parts) if changes_parts else "No storage items to update"
    return new_cfg, changes
