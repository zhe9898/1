"""Builtin published kind catalog facade."""

from __future__ import annotations

from .extension_builtin_connector_kinds import build_core_connector_kinds
from .extension_builtin_job_kinds import build_core_job_kinds

__all__ = ["build_core_connector_kinds", "build_core_job_kinds"]
