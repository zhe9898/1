"""Root test conftest — 保证模块收集阶段环境变量就位。"""
from __future__ import annotations

import os

os.environ.setdefault("DOMAIN", "localhost")
