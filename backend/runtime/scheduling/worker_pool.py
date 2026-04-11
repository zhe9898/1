"""Queue class and worker pool contracts for control-plane scheduling.

This module makes workload lanes explicit instead of relying on implicit
resource heuristics buried inside the scheduler.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from backend.runtime.execution.workload_semantics import WorkloadCategory, get_workload_descriptor

BUILTIN_QUEUE_CLASSES: tuple[str, ...] = (
    "realtime",
    "interactive",
    "batch",
    "gpu-heavy",
    "analytics",
)
_QUEUE_CLASS_SET = frozenset(BUILTIN_QUEUE_CLASSES)
_WORKER_POOL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,63})$")
_ANALYTICS_KEYWORDS = ("analytics", "report", "summary", "aggregate", "rollup", "timeseries")


def list_queue_classes() -> list[str]:
    return list(BUILTIN_QUEUE_CLASSES)


def validate_queue_class(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in _QUEUE_CLASS_SET:
        raise ValueError(f"queue_class must be one of {list(BUILTIN_QUEUE_CLASSES)}, got '{value}'")
    return normalized


def normalize_worker_pool_name(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized:
        raise ValueError("worker_pool must be a non-empty string")
    if not _WORKER_POOL_RE.fullmatch(normalized):
        raise ValueError("worker_pool must match ^[a-z0-9](?:[a-z0-9._-]{0,63})$")
    return normalized


def normalize_worker_pools(values: Iterable[str] | None, *, strict: bool = False) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        try:
            normalized = normalize_worker_pool_name(value)
        except ValueError:
            if strict:
                raise
            continue
        if normalized in seen:
            continue
        ordered.append(normalized)
        seen.add(normalized)
    return ordered


def default_worker_pool_for_queue_class(queue_class: str) -> str:
    return validate_queue_class(queue_class)


def infer_queue_class(
    *,
    kind: str,
    source: str | None = None,
    requested_queue_class: str | None = None,
    required_gpu_vram_mb: int | None = None,
) -> str:
    if requested_queue_class:
        return validate_queue_class(requested_queue_class)

    descriptor = get_workload_descriptor(kind)
    normalized_kind = str(kind or "").strip().lower()
    normalized_source = str(source or "").strip().lower()

    effective_gpu_vram = max(
        int(required_gpu_vram_mb or 0),
        int(descriptor.resource_profile.gpu_vram_mb or 0),
    )
    if effective_gpu_vram > 0:
        return "gpu-heavy"

    if any(token in normalized_kind or token in normalized_source for token in _ANALYTICS_KEYWORDS):
        return "analytics"

    if descriptor.scheduling_profile == "realtime":
        return "realtime"

    if descriptor.category in {WorkloadCategory.SYSTEM, WorkloadCategory.SERVICE}:
        return "realtime"

    if descriptor.category == WorkloadCategory.INTERACTIVE:
        return "interactive"

    return "batch"


def resolve_job_queue_contract(
    *,
    kind: str,
    source: str | None = None,
    requested_queue_class: str | None = None,
    requested_worker_pool: str | None = None,
    required_gpu_vram_mb: int | None = None,
) -> tuple[str, str]:
    queue_class = infer_queue_class(
        kind=kind,
        source=source,
        requested_queue_class=requested_queue_class,
        required_gpu_vram_mb=required_gpu_vram_mb,
    )
    worker_pool = normalize_worker_pool_name(requested_worker_pool) if requested_worker_pool else default_worker_pool_for_queue_class(queue_class)
    return queue_class, worker_pool


def resolve_job_queue_contract_from_record(job: Any) -> tuple[str, str]:
    try:
        queue_class = infer_queue_class(
            kind=str(getattr(job, "kind", "") or ""),
            source=getattr(job, "source", None),
            requested_queue_class=getattr(job, "queue_class", None),
            required_gpu_vram_mb=getattr(job, "required_gpu_vram_mb", None),
        )
    except ValueError:
        queue_class = infer_queue_class(
            kind=str(getattr(job, "kind", "") or ""),
            source=getattr(job, "source", None),
            requested_queue_class=None,
            required_gpu_vram_mb=getattr(job, "required_gpu_vram_mb", None),
        )
    worker_pool_value = getattr(job, "worker_pool", None)
    try:
        worker_pool = normalize_worker_pool_name(worker_pool_value) if worker_pool_value else default_worker_pool_for_queue_class(queue_class)
    except ValueError:
        worker_pool = default_worker_pool_for_queue_class(queue_class)
    return queue_class, worker_pool


def infer_node_worker_pools(
    *,
    worker_pools: Iterable[str] | None = None,
    accepted_kinds: Iterable[str] | None = None,
    capabilities: Iterable[str] | None = None,
    gpu_vram_mb: int | None = None,
    profile: str | None = None,
    metadata: dict[str, object] | None = None,
    strict: bool = False,
) -> list[str]:
    explicit = normalize_worker_pools(worker_pools, strict=strict)
    if explicit:
        return explicit

    inferred: set[str] = set()
    kinds = list(accepted_kinds or []) or list(capabilities or [])
    for kind in kinds:
        inferred.add(infer_queue_class(kind=str(kind or "")))

    if int(gpu_vram_mb or 0) > 0:
        inferred.add("gpu-heavy")

    profile_text = str(profile or "").strip().lower()
    metadata_text = " ".join(f"{key}={value}" for key, value in dict(metadata or {}).items()).lower()
    if any(token in profile_text or token in metadata_text for token in _ANALYTICS_KEYWORDS):
        inferred.add("analytics")

    if not inferred:
        inferred.update({"interactive", "batch"})

    return sorted(inferred)
