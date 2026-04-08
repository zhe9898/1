"""Alembic environment entrypoint for the legacy migration chain."""

from __future__ import annotations

from backend.platform.db.alembic_runtime import run_alembic_env

run_alembic_env(__file__)
