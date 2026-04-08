"""Root test configuration that ensures required env vars are present."""

from __future__ import annotations

import os

os.environ.setdefault("DOMAIN", "localhost")
