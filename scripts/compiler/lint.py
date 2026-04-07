#!/usr/bin/env python3
"""
配置编译器包：config-lint 校验模块。

从 compiler.py 拆解而来，负责 YAML 格式校验、版本检测与 Schema 强校验。
大厂级防御：缺失必填字段时立即报错退出，杜绝静默生成残废 .env。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Schema 定义：system.yaml 必填字段（法典 §1.2 唯一事实来源）
# 支持嵌套 dict 校验，叶子节点为 type 或 None（仅检查存在性）
# ---------------------------------------------------------------------------
REQUIRED_SCHEMA: dict[str, Any] = {
    "version": str,
    "services": {
        "postgres": {"image": str},
        "redis": {"image": str},
        "gateway": {"build": dict},
    },
    "network": {"domain": str},
}

# 推荐但不强制的字段（缺失时 WARN 不退出）
RECOMMENDED_FIELDS: list[str] = [
    "capabilities.storage.media_path",
    "sentinel.mount_container_map",
    "sentinel.watch_targets",
    "sentinel.switch_container_map",
]


def _validate_schema(
    data: dict,
    schema: dict[str, Any],
    path: str = "",
) -> list[str]:
    """
    递归校验 data 是否满足 schema 定义的必填字段。

    返回错误消息列表；空列表表示校验通过。
    """
    errors: list[str] = []
    for key, expected in schema.items():
        full_path = f"{path}.{key}" if path else key
        value = data.get(key)

        if value is None:
            errors.append(f"缺失必填字段: {full_path}")
            continue

        if isinstance(expected, dict):
            # 递归校验子节点
            if not isinstance(value, dict):
                errors.append(f"{full_path} 应为 dict，实际为 {type(value).__name__}")
            else:
                errors.extend(_validate_schema(value, expected, full_path))
        elif isinstance(expected, type):
            if not isinstance(value, expected):
                errors.append(f"{full_path} 应为 {expected.__name__}，" f"实际为 {type(value).__name__}")

    return errors


def _check_recommended(data: dict) -> None:
    """检查推荐字段，缺失时输出 WARN 但不退出。"""
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
            print(
                f"[WARN] [config-lint] 推荐字段 {dotted} 未配置，" "部分功能可能不可用。",
                file=sys.stderr,
            )


def config_lint(path: Path) -> dict:
    """
    config-lint：校验 YAML 格式合法性 + Schema 必填字段。

    返回解析后的配置字典；格式非法或缺必填字段时 sys.exit(1)。
    """
    # 1. 文件读取
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        print(f"[config-lint] 无法读取文件: {path}", file=sys.stderr)
        sys.exit(1)

    # 2. YAML 解析
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        print(f"[config-lint] YAML 格式非法: {e}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(data, dict):
        print("[config-lint] 根节点必须为字典", file=sys.stderr)
        sys.exit(1)

    # 3. 版本检测
    version = data.get("version")
    if not version:
        print("[WARN] [config-lint] system.yaml 缺失 version 字段。" "建议补充以启用自动配置迁移。当前架构规范要求 V2.0+ (法典 7.1.5)。")
    else:
        # 语义版本对比：tuple 比较确保 "10.0" > "2.0"（字符串比较会误判）
        try:
            version_tuple = tuple(int(x) for x in str(version).split("."))
            if version_tuple < (2, 0):
                print(
                    f"[WARN] [config-lint] 检测到旧版本配置 ({version})。" "请注意，新版架构引擎如果涉及弃用的参数，" "请参阅 docs/adr/ 决定并手写迁移扩展！",
                    file=sys.stderr,
                )
        except (ValueError, TypeError):
            print(
                f"[WARN] [config-lint] 无法解析版本号: {version}",
                file=sys.stderr,
            )

    # 4. Schema 强校验（大厂级防御）
    errors = _validate_schema(data, REQUIRED_SCHEMA)
    if errors:
        print(
            "\n🔴 [config-lint] Schema 校验失败，以下必填字段缺失或类型错误：",
            file=sys.stderr,
        )
        for err in errors:
            print(f"  ✗ {err}", file=sys.stderr)
        print(
            "\n请检查 system.yaml 是否包含完整的 services/network 定义。",
            file=sys.stderr,
        )
        sys.exit(1)

    # 5. 推荐字段检查（仅 WARN）
    _check_recommended(data)

    return data
