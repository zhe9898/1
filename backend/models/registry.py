"""Canonical model registry for metadata-driven runtime operations."""

from __future__ import annotations

from collections.abc import Iterable
from importlib import import_module
from typing import Final

from sqlalchemy import MetaData

from backend.models.base import Base

CANONICAL_MODEL_MODULES: Final[tuple[str, ...]] = (
    "backend.models.alert",
    "backend.models.asset",
    "backend.models.audit_log",
    "backend.models.connector",
    "backend.models.feature_flag",
    "backend.models.health_record",
    "backend.models.job",
    "backend.models.job_attempt",
    "backend.models.job_log",
    "backend.models.memory",
    "backend.models.node",
    "backend.models.permission",
    "backend.models.quota",
    "backend.models.scheduling_decision",
    "backend.models.session",
    "backend.models.software_evaluation",
    "backend.models.system",
    "backend.models.tenant",
    "backend.models.tenant_scheduling_policy",
    "backend.models.trigger",
    "backend.models.user",
    "backend.models.webauthn_challenge",
    "backend.models.workflow",
)


def load_canonical_model_modules(modules: Iterable[str] = CANONICAL_MODEL_MODULES) -> None:
    for module_name in modules:
        import_module(module_name)


def load_canonical_model_metadata() -> MetaData:
    load_canonical_model_modules()
    return Base.metadata
