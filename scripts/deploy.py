#!/usr/bin/env python3
"""
ZEN70 一键部署入口脚本。

自动启动图形化安装向导并打开浏览器：
  python scripts/deploy.py          # 启动图形安装器
  python scripts/deploy.py --cli    # 纯 CLI 模式（无浏览器）
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

logger = logging.getLogger(__name__)

# ── 路径解析 ──────────────────────────────────────────────────
SCRIPT_DIR: Path = Path(__file__).resolve().parent
PROJECT_ROOT: Path = SCRIPT_DIR.parent
INSTALLER_MAIN: Path = PROJECT_ROOT / "start_installer.py"

# ── ANSI 颜色 ─────────────────────────────────────────────────
GREEN = "\033[92m"
RED = "\033[91m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"

INSTALLER_PORT = 8081
INSTALLER_URL = f"http://localhost:{INSTALLER_PORT}"


def _wait_for_server(url: str, timeout: int = 15) -> bool:
    """
    等待 HTTP 服务器就绪。

    Args:
        url: 要探测的 URL。
        timeout: 最大等待秒数。

    Returns:
        服务器是否就绪。
    """
    import urllib.request
    import urllib.error

    start = time.time()
    while time.time() - start < timeout:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
    return False


def main() -> None:
    """启动图形化安装向导并自动打开浏览器。"""
    parser = argparse.ArgumentParser(description="ZEN70 一键部署 — 启动图形化安装向导")
    parser.add_argument(
        "--cli",
        action="store_true",
        help="纯 CLI 模式，不启动图形界面",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=INSTALLER_PORT,
        help=f"安装器端口（默认 {INSTALLER_PORT}）",
    )
    args = parser.parse_args()
    port = args.port
    url = f"http://localhost:{port}"

    if args.cli:
        logger.info("%s%s[ZEN70] CLI 模式部署%s", BOLD, CYAN, RESET)
        bootstrap = SCRIPT_DIR / "bootstrap.py"
        if not bootstrap.exists():
            logger.error("%s[ERROR] bootstrap.py 未找到%s", RED, RESET)
            sys.exit(1)
        sys.exit(subprocess.call([sys.executable, str(bootstrap)], cwd=str(PROJECT_ROOT)))

    # ── 图形界面模式 ──────────────────────────────────────────
    if not INSTALLER_MAIN.exists():
        logger.error("%s[ERROR] start_installer.py 未找到%s", RED, RESET)
        sys.exit(1)

    logger.info(
        "%s%s╔══════════════════════════════════════════════╗\n"
        "║     ZEN70 V2.0  图形化部署向导               ║\n"
        "╚══════════════════════════════════════════════╝%s",
        BOLD,
        CYAN,
        RESET,
    )
    logger.info("  启动安装服务器 → %s%s%s", BOLD, url, RESET)

    # 启动安装器进程
    try:
        proc = subprocess.Popen(
            [sys.executable, str(INSTALLER_MAIN)],
            cwd=str(PROJECT_ROOT),
        )
    except OSError as e:
        logger.error("%s[ERROR] 启动安装器失败: %s%s", RED, e, RESET)
        sys.exit(1)

    logger.info("  等待服务就绪...")
    if _wait_for_server(url):
        logger.info("  %s✔%s", GREEN, RESET)
        logger.info("  %s%s浏览器已打开 → %s%s", GREEN, BOLD, url, RESET)
        logger.info("  %s在网页中完成配置后点击「开始一键部署」即可。%s", CYAN, RESET)
        logger.info("  按 %sCtrl+C%s 关闭安装器。", BOLD, RESET)
        webbrowser.open(url)
    else:
        logger.error("  %s超时%s", RED, RESET)
        logger.error("  %s服务器启动失败，请手动访问 %s%s", RED, url, RESET)

    # 保持前台运行直到用户 Ctrl+C
    try:
        proc.wait()
    except KeyboardInterrupt:
        logger.info("%s[ZEN70] 安装器已关闭%s", CYAN, RESET)
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
