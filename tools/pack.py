#!/usr/bin/env python3
# 使用 pathlib，禁止 os.path 拼接；入口接受命令行参数，禁止硬编码路径。
"""
打包工具：将源目录打成 ZIP，排除 .git、node_modules、venv 等。
"""
from __future__ import annotations

import argparse
import fnmatch
import zipfile
from pathlib import Path

EXCLUDES = (
    ".git*",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    "*.pyc",
    ".DS_Store",
    "build_err_invite.txt",
    "*.zip",
)


def _should_exclude(path: Path, root: Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return True
    parts = rel.parts
    name = path.name
    for pattern in EXCLUDES:
        if fnmatch.fnmatch(name, pattern):
            return True
        if any(fnmatch.fnmatch(p, pattern) for p in parts):
            return True
    return False


def create_zip(source_dir: Path, output_path: Path) -> None:
    """将 source_dir 打包为 output_path（ZIP），排除 EXCLUDES。"""
    source_dir = source_dir.resolve()
    output_path = output_path.resolve()
    if not source_dir.is_dir():
        raise NotADirectoryError(str(source_dir))
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for full_path in source_dir.rglob("*"):
            if not full_path.is_file():
                continue
            if _should_exclude(full_path, source_dir):
                continue
            try:
                arcname = full_path.relative_to(source_dir)
            except ValueError:
                continue
            zipf.write(full_path, arcname.as_posix())
            print(f"Added: {arcname.as_posix()}")


def main() -> None:
    parser = argparse.ArgumentParser(description="ZEN70 发布打包：源目录 → ZIP")
    parser.add_argument("source", type=Path, nargs="?", default=Path.cwd(), help="源目录（默认当前目录）")
    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=None,
        help="输出 ZIP 路径（默认：ZEN70_Release_V3.0.zip）",
    )
    args = parser.parse_args()
    source = args.source.resolve()
    default_name = "ZEN70_Release_V3.0.zip" if source.name in (".", "3.0") else f"{source.name}_Release.zip"
    output = args.output or (source if source.is_dir() else source.parent) / default_name
    print(f"Packaging {source} into {output}...")
    create_zip(source, output)
    print("Packaging complete!")


if __name__ == "__main__":
    main()
