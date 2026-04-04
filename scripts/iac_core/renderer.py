"""
iac_core.renderer — Jinja2 模板渲染与 YAML 序列化工具。

职责:
1. dict → YAML 块序列化 (dict_to_yaml_block)
2. Jinja2 Environment 构造与模板渲染
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import jinja2
import yaml

logger = logging.getLogger(__name__)


def _normalize_command_booleans(data: dict[str, Any]) -> dict[str, Any]:
    """
    递归遍历服务配置，将 command 列表中的 Python 布尔值转换为字符串。

    YAML 解析器将裸 yes/true → Python True，但 Redis 等工具
    只接受 'yes'/'no'，不接受 'true'/'false'。
    此函数确保 command 数组中的布尔值被序列化为 'yes'/'no'。

    法典 FIX-058: 防止 appendonly "true" 导致 Redis FATAL CONFIG FILE ERROR。
    """
    for key, value in data.items():
        if key == "command" and isinstance(value, list):
            data[key] = [("yes" if item else "no") if isinstance(item, bool) else item for item in value]
        elif isinstance(value, dict):
            _normalize_command_booleans(value)
    return data


def dict_to_yaml_block(data: dict[str, Any]) -> str:
    """
    将 dict 序列化为 docker-compose.yml 服务级 YAML 块（4 空格缩进）。

    消灭手拼 f-string 缩进风险（法典 §8.2）。
    """
    _normalize_command_booleans(data)
    raw = yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)
    lines = raw.splitlines()
    return "".join(f"    {line}\n" for line in lines if line.strip())


def create_jinja2_env(templates_dir: Path) -> jinja2.Environment:
    """
    构造 Jinja2 Environment（统一配置，防碎片化）。

    Args:
        templates_dir: 模板文件所在目录（必须存在）。

    Returns:
        配置好的 Jinja2 Environment。

    Raises:
        FileNotFoundError: 模板目录不存在。
    """
    if not templates_dir.exists():
        msg = f"模板目录不存在: {templates_dir}"
        raise FileNotFoundError(msg)

    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(templates_dir)),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


def render_template(
    env: jinja2.Environment,
    template_name: str,
    context: dict[str, Any],
) -> str:
    """
    渲染单个 Jinja2 模板。

    Args:
        env: Jinja2 Environment。
        template_name: 模板文件名（如 docker-compose.yml.j2）。
        context: 模板上下文变量。

    Returns:
        渲染后的字符串。

    Raises:
        jinja2.TemplateError: 模板语法/渲染错误。
    """
    template = env.get_template(template_name)
    return template.render(**context)
