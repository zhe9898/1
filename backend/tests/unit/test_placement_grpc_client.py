from __future__ import annotations

import builtins
import importlib
import sys
from types import SimpleNamespace

from backend.runtime.scheduling.placement_grpc_client import _job_to_proto


def test_job_to_proto_resolves_queue_contract_when_record_fields_are_empty() -> None:
    proto = _job_to_proto(
        SimpleNamespace(
            job_id="job-1",
            kind="connector.invoke",
            priority=50,
            gang_id=None,
            tenant_id="default",
            target_os=None,
            target_arch=None,
            target_zone=None,
            target_executor=None,
            required_capabilities=[],
            required_cpu_cores=0,
            required_memory_mb=0,
            required_gpu_vram_mb=0,
            required_storage_mb=0,
            max_network_latency_ms=0,
            data_locality_key=None,
            prefer_cached_data=False,
            power_budget_watts=0,
            thermal_sensitivity=None,
            cloud_fallback_enabled=False,
            queue_class=None,
            worker_pool=None,
            source="console",
        )
    )

    assert proto.queue_class == "interactive"
    assert proto.worker_pool == "interactive"


def test_job_to_proto_preserves_explicit_queue_contract() -> None:
    proto = _job_to_proto(
        SimpleNamespace(
            job_id="job-2",
            kind="shell.exec",
            priority=50,
            gang_id=None,
            tenant_id="default",
            target_os=None,
            target_arch=None,
            target_zone=None,
            target_executor=None,
            required_capabilities=[],
            required_cpu_cores=0,
            required_memory_mb=0,
            required_gpu_vram_mb=0,
            required_storage_mb=0,
            max_network_latency_ms=0,
            data_locality_key=None,
            prefer_cached_data=False,
            power_budget_watts=0,
            thermal_sensitivity=None,
            cloud_fallback_enabled=False,
            queue_class="batch",
            worker_pool="batch",
            source="console",
        )
    )

    assert proto.queue_class == "batch"
    assert proto.worker_pool == "batch"


def test_generated_proto_package_does_not_require_grpc_for_message_import(monkeypatch) -> None:
    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):  # type: ignore[no-untyped-def]
        if name == "grpc":
            raise ModuleNotFoundError("No module named 'grpc'")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.delitem(sys.modules, "grpc", raising=False)
    monkeypatch.delitem(sys.modules, "backend.runtime.scheduling.gen_grpc", raising=False)
    monkeypatch.delitem(sys.modules, "backend.runtime.scheduling.gen_grpc.placement_pb2", raising=False)
    monkeypatch.setattr(builtins, "__import__", guarded_import)

    placement_pb2 = importlib.import_module("backend.runtime.scheduling.gen_grpc.placement_pb2")

    assert hasattr(placement_pb2, "JobSpec")
