"""Builtin published job kind catalog facade."""

from __future__ import annotations

from .extension_builtin_job_kinds_compute import build_core_compute_job_kinds
from .extension_builtin_job_kinds_control import build_core_control_job_kinds
from .extension_builtin_job_kinds_integration import build_core_integration_job_kinds
from .extension_contracts import JobKindSpec


def build_core_job_kinds() -> tuple[JobKindSpec, ...]:
    return (
        *build_core_compute_job_kinds(),
        *build_core_control_job_kinds(),
        *build_core_integration_job_kinds(),
    )
