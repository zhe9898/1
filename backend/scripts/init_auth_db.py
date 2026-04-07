#!/usr/bin/env python3
"""
创建认证相关表（users、webauthn_credentials）。
需设置 POSTGRES_DSN；从项目根目录运行：PYTHONPATH=. python backend/scripts/init_auth_db.py
生产环境表结构变更必须使用 Alembic 迁移，本脚本仅用于开发/初始化。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# 确保可导入 backend
_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from backend.db import init_db  # noqa: E402


def main() -> None:
    if not os.getenv("POSTGRES_DSN"):
        print("POSTGRES_DSN not set", file=sys.stderr)
        sys.exit(1)
    asyncio.run(init_db())
    print("Tables created.")


if __name__ == "__main__":
    main()
