#!/usr/bin/env python3
"""
ZEN70 上线后部署脚本：服务更新、配置重载、回滚恢复。

- 幂等：重复执行仅应用增量变更。
- --rollback：从 .zen70_backups 恢复上一健康快照并重载。
- 禁止用于 Day-0 裸机点火，仅限已上线系统的更新与回滚。
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from deploy_utils import project_root as _root
from deploy_utils import resolve_name_conflict as _resolve_name_conflict
from deploy_utils import scripts_dir as _scripts_dir
from deploy_utils import start_host_services as _start_host_services

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
BACKUP_DIR = ".zen70_backups"
DEFAULT_CONFIG = "system.yaml"


# ---------------------------------------------------------------------------
# 零停机 compose up：优雅冲突检测 → 精确修复 → 重试 (法典 §1.2)
# ---------------------------------------------------------------------------


def _compose_up_graceful(
    root: Path,
    compose_file: Path,
    env: dict[str, str],
    *,
    timeout: int = 300,
    label: str = "部署",
) -> None:
    """
    零停机 docker compose up -d --remove-orphans，含优雅冲突解决。

    正常路径: compose 自动检测配置变更，仅重建变更容器（零停机滚动更新）。
    冲突路径: 若失败于 'name already in use'，精确诊断冲突容器：
      - 属于其他 compose project → compose -p <旧project> down
      - 无 compose label（手动创建）→ docker rm -f <单个容器>
    然后重试一次。

    与旧方案 docker rm -f * 的本质区别：
      旧方案无条件杀光所有 zen70-* → 全量停服
      新方案先正常 up（零停机），仅在冲突时精确处理单个容器

    Args:
        root: 项目根目录。
        compose_file: docker-compose.yml 路径。
        env: 环境变量字典（必须含 COMPOSE_PROJECT_NAME=zen70）。
        timeout: compose up 超时秒数。
        label: 日志前缀（"部署" / "回滚"）。

    Raises:
        SystemExit: 重试后仍失败。
    """
    up_args = [
        "docker",
        "compose",
        "-f",
        str(compose_file),
        "up",
        "-d",
        "--force-recreate",
        "--remove-orphans",
    ]

    # ── Step 1: 正常路径（零停机滚动更新）──
    try:
        r = subprocess.run(
            up_args,
            cwd=str(root),
            env=env,
            timeout=timeout,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired:
        logger.error("%s: compose up 超时 (%ds)", label, timeout)
        sys.exit(1)
    except FileNotFoundError:
        logger.error("未找到 docker 或 compose 插件")
        sys.exit(1)

    if r.returncode == 0:
        return  # 滚动更新成功，零停机

    stderr = (r.stderr or r.stdout or "").strip()

    # ── Step 2: 检测 'already in use' 冲突 ──
    if "already in use" not in stderr:
        logger.error("%s失败 (rc=%s): %s", label, r.returncode, stderr[:500])
        sys.exit(1)

    logger.warning(
        "[冲突修复] compose up 遇容器名冲突，启动精确修复...",
    )
    _resolve_name_conflict(root, stderr)

    # ── Step 3: 重试一次 ──
    logger.info("[冲突修复] 重试 compose up...")
    try:
        r2 = subprocess.run(
            up_args,
            cwd=str(root),
            env=env,
            timeout=timeout,
            capture_output=True,
            text=True,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.error("%s重试失败: %s", label, e)
        sys.exit(1)

    if r2.returncode != 0:
        err2 = (r2.stderr or r2.stdout or "").strip()
        logger.error("%s重试仍失败 (rc=%s): %s", label, r2.returncode, err2[:500])
        sys.exit(1)

    logger.info("[冲突修复] 重试成功")


def _backup_state(root: Path, config_path: Path, output_dir: Path) -> Path:
    """将当前 system.yaml、docker-compose.yml、.env 备份到带时间戳目录。"""
    backup_base = root / BACKUP_DIR
    backup_base.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dest = backup_base / ts
    dest.mkdir(parents=True, exist_ok=True)
    for name, src in [
        ("system.yaml", config_path),
        ("docker-compose.yml", output_dir / "docker-compose.yml"),
        (".env", output_dir / ".env"),
    ]:
        if src.exists():
            shutil.copy2(src, dest / name)
    return dest


def _list_backups(root: Path) -> list[Path]:
    """返回按时间倒序的备份目录列表。"""
    base = root / BACKUP_DIR
    if not base.exists():
        return []
    dirs = [d for d in base.iterdir() if d.is_dir()]
    dirs.sort(key=lambda p: p.name, reverse=True)
    return dirs


def _rollback(root: Path, output_dir: Path, backup_path: Path) -> None:
    """从备份目录恢复并执行 compose up。"""
    for name in ("system.yaml", "docker-compose.yml", ".env"):
        src = backup_path / name
        if not src.exists():
            logger.error("备份缺失: %s", name)
            sys.exit(1)
        dst = root / DEFAULT_CONFIG if name == "system.yaml" else output_dir / name
        shutil.copy2(src, dst)
        logger.info("已恢复 %s", name)
    compose_file = output_dir / "docker-compose.yml"
    env = os.environ.copy()
    # 法典 §1.2: IaC 唯一事实来源 —— 强制 project name，严禁 setdefault
    env["COMPOSE_PROJECT_NAME"] = "zen70"
    _compose_up_graceful(root, compose_file, env, label="回滚")
    logger.info("回滚完成")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ZEN70 部署器（幂等，支持 --rollback 回滚）",
    )
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG,
        help="system.yaml 路径，默认 system.yaml",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        default=None,
        help="编译输出目录，默认项目根",
    )
    parser.add_argument(
        "--rollback",
        action="store_true",
        help="回滚到上一备份快照",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="跳过本次部署前的备份（仅当确信可回滚时使用）",
    )
    args = parser.parse_args()

    root = _root()
    config_path = root / args.config if not Path(args.config).is_absolute() else Path(args.config)
    if args.output_dir:
        output_dir = Path(args.output_dir) if Path(args.output_dir).is_absolute() else root / args.output_dir
    else:
        output_dir = root

    if args.rollback:
        backups = _list_backups(root)
        if not backups:
            logger.error("无可用备份，无法回滚")
            sys.exit(1)
        logger.info("回滚到: %s", backups[0].name)
        _rollback(root, output_dir, backups[0])
        return

    if not config_path.exists():
        logger.error("配置不存在: %s", config_path)
        sys.exit(1)

    if not args.no_backup:
        _backup_state(root, config_path, output_dir)
        logger.info("已备份当前状态")

    # 调用 compiler + compose up
    try:
        out_arg = "." if output_dir.resolve() == root.resolve() else str(output_dir.relative_to(root))
    except ValueError:
        out_arg = str(output_dir)
    cmd_compiler = [
        sys.executable,
        str(_scripts_dir() / "compiler.py"),
        str(config_path),
        "-o",
        out_arg,
    ]
    subprocess.run(cmd_compiler, cwd=str(root), check=True, timeout=120)
    compose_file = output_dir / "docker-compose.yml"
    env = os.environ.copy()
    # 法典 §1.2: IaC 唯一事实来源 —— 强制 project name，严禁 setdefault
    env["COMPOSE_PROJECT_NAME"] = "zen70"
    _compose_up_graceful(root, compose_file, env, label="部署")

    # host 服务 systemctl 管理
    _start_host_services(config_path, output_dir / "systemd")

    logger.info("部署完成")


if __name__ == "__main__":
    main()
