from __future__ import annotations

import os

DEFAULT_RUNTIME_VERSION = "1.58.0"


def get_runtime_version() -> str:
    version = (os.getenv("ZEN70_API_VERSION") or os.getenv("ZEN70_VERSION") or DEFAULT_RUNTIME_VERSION).strip()
    return version or DEFAULT_RUNTIME_VERSION
