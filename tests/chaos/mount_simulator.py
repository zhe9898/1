#!/usr/bin/env python3
"""
模拟挂载点工具：创建/删除虚拟磁盘（环回设备），支持 mount/umount。

用于集成测试中模拟存储插入/拔出；需 root 或 sudo（Linux）。
CI 无环回设备时可仅运行 Mock 模式测试。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional


class MountSimulator:
    """通过环回设备创建虚拟磁盘并挂载到指定目录，用于模拟存储插拔。"""

    def __init__(self, mount_point: str = "/mnt/test") -> None:
        self.mount_point = Path(mount_point)
        self.image_file: Optional[Path] = None
        self._use_sudo = os.getenv("MOUNT_SIMULATOR_SUDO", "1") in ("1", "true")

    def _run(
        self,
        cmd: list[str],
        check: bool = True,
        timeout: int = 60,
    ) -> subprocess.CompletedProcess:
        if self._use_sudo and cmd[0] in ("mount", "umount", "losetup"):
            cmd = ["sudo"] + cmd
        return subprocess.run(
            cmd,
            check=check,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def create_image(self, size_mb: int = 100) -> Path:
        """创建虚拟磁盘镜像文件并格式化为 ext4。"""
        fd, path = tempfile.mkstemp(suffix=".img", prefix="zen70_mount_sim_")
        os.close(fd)
        self.image_file = Path(path)
        subprocess.run(
            [
                "dd", "if=/dev/zero", f"of={self.image_file}", "bs=1M",
                f"count={size_mb}",
            ],
            check=True,
            capture_output=True,
            timeout=120,
        )
        subprocess.run(
            ["mkfs.ext4", "-F", str(self.image_file)],
            check=True,
            capture_output=True,
            timeout=30,
        )
        return self.image_file

    def mount(self, size_mb: int = 100) -> bool:
        """挂载虚拟磁盘到 self.mount_point；无镜像时先创建。"""
        if self.image_file is None or not self.image_file.exists():
            self.create_image(size_mb=size_mb)
        self.mount_point.mkdir(parents=True, exist_ok=True)
        try:
            self._run(
                ["mount", "-o", "loop", str(self.image_file), str(self.mount_point)],
                check=True,
            )
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def umount(self) -> bool:
        """卸载虚拟磁盘。"""
        try:
            self._run(["umount", str(self.mount_point)], check=True)
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def cleanup(self) -> None:
        """删除镜像文件；应先 umount。"""
        if self.image_file is not None and self.image_file.exists():
            try:
                self.image_file.unlink()
            except OSError:
                pass
            self.image_file = None

    def teardown(self) -> None:
        """卸载并清理。"""
        self.umount()
        self.cleanup()


def _main() -> None:
    parser = argparse.ArgumentParser(description="Mount simulator: create/attach/detach loop device")
    parser.add_argument("--mount-point", default="/mnt/test", help="Mount point path")
    parser.add_argument("--action", choices=("mount", "umount", "create"), default="mount")
    parser.add_argument("--size-mb", type=int, default=100)
    args = parser.parse_args()
    sim = MountSimulator(mount_point=args.mount_point)
    if args.action == "mount":
        ok = sim.mount(size_mb=args.size_mb)
        sys.exit(0 if ok else 1)
    if args.action == "umount":
        ok = sim.umount()
        sim.cleanup()
        sys.exit(0 if ok else 1)
    if args.action == "create":
        sim.create_image(size_mb=args.size_mb)
        print(sim.image_file)
        sys.exit(0)


if __name__ == "__main__":
    _main()
