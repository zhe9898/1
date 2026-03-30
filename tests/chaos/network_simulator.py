#!/usr/bin/env python3
"""
网络分区/延迟模拟：通过 tc 或 iptables 模拟丢包、延迟、阻断。

仅 Linux 且需 root；CI 或非 Linux 下相关测试应跳过。
"""

from __future__ import annotations

import argparse
import platform
import subprocess
import sys
from typing import Optional


def _is_linux() -> bool:
    return platform.system() == "Linux"


def add_delay(interface: str, delay_ms: int = 100, use_sudo: bool = True) -> bool:
    """使用 tc 添加固定延迟。"""
    if not _is_linux():
        return False
    cmd = [
        "tc", "qdisc", "add", "dev", interface, "root", "netem",
        "delay", f"{delay_ms}ms",
    ]
    if use_sudo:
        cmd = ["sudo"] + cmd
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=10)
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return False


def add_loss(interface: str, loss_pct: int = 50, use_sudo: bool = True) -> bool:
    """使用 tc 添加丢包率。"""
    if not _is_linux():
        return False
    cmd = [
        "tc", "qdisc", "add", "dev", interface, "root", "netem",
        "loss", f"{loss_pct}%",
    ]
    if use_sudo:
        cmd = ["sudo"] + cmd
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=10)
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return False


def clear_tc(interface: str, use_sudo: bool = True) -> bool:
    """清除接口上的 tc 规则。"""
    if not _is_linux():
        return False
    cmd = ["tc", "qdisc", "del", "dev", interface, "root"]
    if use_sudo:
        cmd = ["sudo"] + cmd
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=10)
        return True
    except subprocess.CalledProcessError:
        return True  # 无规则时也返回 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _main() -> None:
    parser = argparse.ArgumentParser(description="Network chaos: delay/loss via tc (Linux only)")
    parser.add_argument("--interface", default="eth0")
    parser.add_argument("--action", choices=("delay", "loss", "clear"))
    parser.add_argument("--delay-ms", type=int, default=100)
    parser.add_argument("--loss-pct", type=int, default=50)
    args = parser.parse_args()
    if not _is_linux():
        print("Only supported on Linux", file=sys.stderr)
        sys.exit(2)
    if args.action == "delay":
        ok = add_delay(args.interface, args.delay_ms)
    elif args.action == "loss":
        ok = add_loss(args.interface, args.loss_pct)
    else:
        ok = clear_tc(args.interface)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    _main()
