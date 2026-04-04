#!/usr/bin/env python3
"""
ZEN70 一键修复脚本。

自动检测并修复常见部署问题：
  1. 配置文件缺失 → 重新编译
  2. 容器异常 → 自动重启
  3. 孤儿容器 → 清理
  4. 权限问题 → 自动修正
  5. 磁盘清理 → docker system prune

用法:
    python scripts/repair.py             # 交互式全量修复
    python scripts/repair.py --auto      # 非交互，自动修复所有问题
    python scripts/repair.py --dry-run   # 仅检查，不执行修复
"""

from __future__ import annotations

import argparse
import logging
import platform
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# ── 路径解析 ──────────────────────────────────────────────────
SCRIPT_DIR: Path = Path(__file__).resolve().parent
PROJECT_ROOT: Path = SCRIPT_DIR.parent
SYSTEM_YAML: Path = PROJECT_ROOT / "system.yaml"
COMPILER_SCRIPT: Path = SCRIPT_DIR / "compiler.py"
ENV_FILE: Path = PROJECT_ROOT / ".env"
COMPOSE_FILE: Path = PROJECT_ROOT / "docker-compose.yml"
USERS_ACL: Path = PROJECT_ROOT / "config" / "users.acl"
CADDYFILE: Path = PROJECT_ROOT / "config" / "Caddyfile"

# ── ANSI 颜色 ─────────────────────────────────────────────────
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

# ── 检测结果收集 ──────────────────────────────────────────────
CheckResult = tuple[str, str, str]  # (id, severity, description)


def _banner() -> None:
    """打印修复横幅。"""
    logger.info(
        "%s%s╔══════════════════════════════════════════════╗\n"
        "║     ZEN70 V2.0  一键修复工具                 ║\n"
        "╚══════════════════════════════════════════════╝%s",
        BOLD,
        CYAN,
        RESET,
    )


def _cmd(args: list[str], timeout: int = 30) -> tuple[int, str]:
    """
    执行命令并返回 (退出码, stdout)。

    Args:
        args: 命令参数列表。
        timeout: 超时秒数。

    Returns:
        (exit_code, stdout_text) 元组。
    """
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            cwd=str(PROJECT_ROOT),
        )
        return result.returncode, result.stdout + result.stderr
    except FileNotFoundError:
        return -1, f"命令未找到: {args[0]}"
    except subprocess.TimeoutExpired:
        return -2, f"命令超时 ({timeout}s)"
    except OSError as e:
        return -3, str(e)


# ═══════════════════════════════════════════════════════════════
# 检测器（Detector）：每个函数返回一组 CheckResult
# ═══════════════════════════════════════════════════════════════


def check_config_files() -> list[CheckResult]:
    """检查关键配置文件是否存在。"""
    issues: list[CheckResult] = []
    checks = {
        "system.yaml": SYSTEM_YAML,
        ".env": ENV_FILE,
        "docker-compose.yml": COMPOSE_FILE,
        "config/users.acl": USERS_ACL,
        "config/Caddyfile": CADDYFILE,
    }
    for name, path in checks.items():
        if not path.exists():
            issues.append(("config_missing", "ERROR", f"配置文件缺失: {name}"))
    return issues


def check_docker_daemon() -> list[CheckResult]:
    """检查 Docker 守护进程是否可用。"""
    issues: list[CheckResult] = []
    rc, output = _cmd(["docker", "info"])
    if rc != 0:
        issues.append(("docker_down", "FATAL", "Docker 守护进程不可用"))
    return issues


def check_compose_status() -> list[CheckResult]:
    """检查容器编排状态。"""
    issues: list[CheckResult] = []
    if not COMPOSE_FILE.exists():
        return issues

    rc, output = _cmd(["docker", "compose", "-f", str(COMPOSE_FILE), "ps", "--format", "json"])
    if rc != 0:
        issues.append(("compose_error", "WARN", "docker compose ps 执行失败"))
        return issues

    # 检查不健康/已停止/持续重启的容器
    import json

    for line in output.strip().splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            ctr = json.loads(line)
        except json.JSONDecodeError:
            continue
        name = ctr.get("Name", ctr.get("Service", "unknown"))
        state = ctr.get("State", "").lower()
        health = ctr.get("Health", "").lower()

        if state == "exited" or state == "dead":
            issues.append(("container_dead", "ERROR", f"容器已停止: {name} (state={state})"))
        elif state == "restarting":
            issues.append(("container_restart", "ERROR", f"容器循环重启: {name}"))
        elif health == "unhealthy":
            issues.append(("container_unhealthy", "WARN", f"容器不健康: {name}"))

    return issues


def check_orphan_containers() -> list[CheckResult]:
    """检查孤儿容器（non-compose 残留）。"""
    issues: list[CheckResult] = []
    rc, output = _cmd(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            "label=com.docker.compose.project=zen70",
            "--filter",
            "status=exited",
            "--format",
            "{{.Names}} {{.Status}}",
        ]
    )
    if rc == 0:
        for line in output.strip().splitlines():
            if line.strip():
                issues.append(("orphan", "WARN", f"残留停止容器: {line.strip()}"))
    return issues


def check_disk_space() -> list[CheckResult]:
    """检查 Docker 磁盘占用。"""
    issues: list[CheckResult] = []
    rc, output = _cmd(["docker", "system", "df"])
    if rc == 0:
        for line in output.strip().splitlines():
            if "reclaimable" in line.lower() and any(u in line for u in ["GB", "MB"]):
                # 粗略检测可回收空间
                pass
    # 检查系统盘使用率
    try:
        import shutil

        total, used, free = shutil.disk_usage(str(PROJECT_ROOT))
        pct = int(used / total * 100)
        if pct >= 95:
            issues.append(("disk_critical", "FATAL", f"系统盘 {pct}% 已满（法典 §3.3 红线 95%）"))
        elif pct >= 85:
            issues.append(("disk_warn", "WARN", f"系统盘 {pct}% 使用率偏高"))
    except OSError:
        pass
    return issues


def check_env_secrets() -> list[CheckResult]:
    """检查 .env 中是否存在占位符未替换的密钥。"""
    issues: list[CheckResult] = []
    if not ENV_FILE.exists():
        return issues
    try:
        text = ENV_FILE.read_text(encoding="utf-8")
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "your_cloudflare_token_here" in line:
                issues.append(("token_placeholder", "WARN", "TUNNEL_TOKEN 仍为占位符，隧道不会生效"))
            if "=" in line:
                key, _, val = line.partition("=")
                if key.strip() in ("POSTGRES_PASSWORD", "REDIS_PASSWORD", "JWT_SECRET_CURRENT") and not val.strip():
                    issues.append(("empty_secret", "ERROR", f"关键密钥为空: {key.strip()}"))
    except OSError:
        pass
    return issues


# ═══════════════════════════════════════════════════════════════
# 修复器（Fixer）：根据 issue id 执行对应修复
# ═══════════════════════════════════════════════════════════════


def fix_config_missing(dry_run: bool = False) -> bool:
    """重新运行编译器生成缺失的配置文件。"""
    if dry_run:
        logger.info("  %s[DRY-RUN] 将重新编译配置%s", DIM, RESET)
        return True
    logger.info("  %s[FIX] 重新编译配置文件...%s", CYAN, RESET)
    rc, output = _cmd(
        [sys.executable, str(COMPILER_SCRIPT), str(SYSTEM_YAML), "-o", "."],
        timeout=120,
    )
    if rc == 0:
        logger.info("  %s[OK] 配置重新编译完成%s", GREEN, RESET)
        return True
    logger.error("  %s[FAIL] 编译器失败: %s%s", RED, output[:200], RESET)
    return False


def fix_container_restart(dry_run: bool = False) -> bool:
    """重启所有容器（--remove-orphans 斩杀孤儿）。"""
    if dry_run:
        logger.info("  %s[DRY-RUN] 将执行 docker compose up -d --remove-orphans%s", DIM, RESET)
        return True
    logger.info("  %s[FIX] 重启容器集群...%s", CYAN, RESET)
    rc, output = _cmd(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d", "--remove-orphans"],
        timeout=300,
    )
    if rc == 0:
        logger.info("  %s[OK] 容器集群已重启%s", GREEN, RESET)
        return True
    logger.error("  %s[FAIL] compose up 失败: %s%s", RED, output[:200], RESET)
    return False


def fix_orphan_cleanup(dry_run: bool = False) -> bool:
    """清理停止的孤儿容器。"""
    if dry_run:
        logger.info("  %s[DRY-RUN] 将清理已停止的容器%s", DIM, RESET)
        return True
    logger.info("  %s[FIX] 清理孤儿容器...%s", CYAN, RESET)
    rc, _ = _cmd(["docker", "container", "prune", "-f", "--filter", "label=com.docker.compose.project=zen70"])
    return rc == 0


def fix_disk_cleanup(dry_run: bool = False) -> bool:
    """清理 Docker 悬空镜像和废弃卷（保护 gc.keep 标签资产）。"""
    if dry_run:
        logger.info("  %s[DRY-RUN] 将执行 docker system prune (保护 zen70.gc.keep)%s", DIM, RESET)
        return True
    logger.info("  %s[FIX] 清理 Docker 悬空资源（保留核心资产）...%s", CYAN, RESET)
    # 法典 §3.7：GC 保护 zen70.gc.keep=true 标签
    rc, _ = _cmd(
        [
            "docker",
            "system",
            "prune",
            "-f",
            "--filter",
            "label!=zen70.gc.keep=true",
        ],
        timeout=120,
    )
    if rc == 0:
        logger.info("  %s[OK] 清理完成%s", GREEN, RESET)
    return rc == 0


def fix_empty_secret(dry_run: bool = False) -> bool:
    """重新运行编译器以重新生成空密钥。"""
    return fix_config_missing(dry_run)


# ── 修复分发表 ────────────────────────────────────────────────
FIXERS: dict[str, callable] = {
    "config_missing": fix_config_missing,
    "container_dead": fix_container_restart,
    "container_restart": fix_container_restart,
    "container_unhealthy": fix_container_restart,
    "orphan": fix_orphan_cleanup,
    "disk_warn": fix_disk_cleanup,
    "disk_critical": fix_disk_cleanup,
    "empty_secret": fix_empty_secret,
}


# ═══════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════


def main() -> None:
    """解析参数并执行检查-修复流程。"""
    parser = argparse.ArgumentParser(description="ZEN70 一键修复工具")
    parser.add_argument("--auto", action="store_true", help="非交互式，自动修复所有可修复问题")
    parser.add_argument("--dry-run", action="store_true", help="仅检查，不执行修复")
    args = parser.parse_args()

    _banner()
    start_time = time.time()

    # ── 运行所有检测器 ────────────────────────────────────────
    logger.info("%s━━━ 检测阶段 ━━━%s", BOLD, RESET)
    all_issues: list[CheckResult] = []

    detectors = [
        ("配置文件", check_config_files),
        ("Docker 守护进程", check_docker_daemon),
        ("容器编排状态", check_compose_status),
        ("孤儿容器", check_orphan_containers),
        ("磁盘空间", check_disk_space),
        ("密钥完整性", check_env_secrets),
    ]

    for label, detector in detectors:
        issues = detector()
        if issues:
            logger.info("  检查 %s... %s发现 %s 个问题%s", label, RED, len(issues), RESET)
            all_issues.extend(issues)
        else:
            logger.info("  检查 %s... %s✔%s", label, GREEN, RESET)

    # ── 汇总报告 ──────────────────────────────────────────────
    if not all_issues:
        logger.info("%s%s✔ 系统状态健康，未发现问题%s", GREEN, BOLD, RESET)
        return

    logger.info("%s━━━ 问题汇总（%s 项）━━━%s", BOLD, len(all_issues), RESET)
    for i, (issue_id, severity, desc) in enumerate(all_issues, 1):
        color = RED if severity in ("ERROR", "FATAL") else YELLOW
        fixable = "fix" if issue_id in FIXERS else "manual"
        logger.info("  %s[%s]%s [%s] %s", color, severity, RESET, fixable, desc)

    fixable_issues = [(iid, sev, desc) for iid, sev, desc in all_issues if iid in FIXERS]
    unfixable = [(iid, sev, desc) for iid, sev, desc in all_issues if iid not in FIXERS]

    if not fixable_issues:
        logger.info("%s以上问题需要手动处理%s", YELLOW, RESET)
        return

    # ── 修复阶段 ──────────────────────────────────────────────
    if args.dry_run:
        logger.info("%s━━━ 修复预览（dry-run）━━━%s", BOLD, RESET)
    else:
        if not args.auto:
            answer = input(f"\n{BOLD}是否自动修复 {len(fixable_issues)} 个可修复问题？[Y/n] {RESET}").strip()
            if answer.lower() in ("n", "no"):
                logger.info("已取消")
                return
        logger.info("%s━━━ 修复阶段 ━━━%s", BOLD, RESET)

    # 去重：同类问题只修复一次
    seen_fixes: set[str] = set()
    fixed = 0
    failed = 0

    for issue_id, severity, desc in fixable_issues:
        if issue_id in seen_fixes:
            continue
        seen_fixes.add(issue_id)
        fixer = FIXERS[issue_id]
        try:
            if fixer(dry_run=args.dry_run):
                fixed += 1
            else:
                failed += 1
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
            logger.error("  %s[ERROR] 修复异常: %s%s", RED, e, RESET)
            failed += 1

    # ── 结果 ──────────────────────────────────────────────────
    elapsed = int(time.time() - start_time)
    mode = "预览" if args.dry_run else "修复"
    logger.info(
        "%s━━━ %s完成（%ss）━━━%s\n  %s✔ 已修复: %s%s\n  %s✘ 失败: %s%s\n  %s需手动: %s%s",
        BOLD,
        mode,
        elapsed,
        RESET,
        GREEN,
        fixed,
        RESET,
        RED if failed else DIM,
        failed,
        RESET,
        YELLOW if unfixable else DIM,
        len(unfixable),
        RESET,
    )

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
