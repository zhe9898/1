"""Alembic environment entrypoint for the application migration chain."""

from __future__ import annotations

from backend.platform.db.alembic_runtime import run_alembic_env

run_alembic_env(__file__)
