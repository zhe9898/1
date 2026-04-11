from __future__ import annotations

from types import SimpleNamespace

from backend.runtime.scheduling.worker_pool import (
    infer_node_worker_pools,
    infer_queue_class,
    resolve_job_queue_contract,
    resolve_job_queue_contract_from_record,
)


def test_infer_queue_class_maps_realtime_interactive_gpu_and_analytics() -> None:
    assert infer_queue_class(kind="iot.collect") == "realtime"
    assert infer_queue_class(kind="connector.invoke") == "interactive"
    assert infer_queue_class(kind="ml.inference") == "gpu-heavy"
    assert infer_queue_class(kind="shell.exec", source="daily-analytics-report") == "analytics"


def test_resolve_job_queue_contract_prefers_explicit_overrides() -> None:
    queue_class, worker_pool = resolve_job_queue_contract(
        kind="connector.invoke",
        requested_queue_class="batch",
        requested_worker_pool="interactive-hi",
    )

    assert queue_class == "batch"
    assert worker_pool == "interactive-hi"


def test_resolve_job_queue_contract_from_record_derives_defaults() -> None:
    job = SimpleNamespace(
        kind="connector.invoke",
        source="console",
        queue_class=None,
        worker_pool=None,
        required_gpu_vram_mb=None,
    )

    queue_class, worker_pool = resolve_job_queue_contract_from_record(job)

    assert queue_class == "interactive"
    assert worker_pool == "interactive"


def test_infer_node_worker_pools_keeps_general_pools_for_gpu_nodes() -> None:
    pools = infer_node_worker_pools(
        accepted_kinds=["connector.invoke", "shell.exec"],
        gpu_vram_mb=8192,
    )

    assert pools == ["batch", "gpu-heavy", "interactive"]
