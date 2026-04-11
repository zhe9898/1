from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend.runtime.scheduling.job_scheduler import PlacementSolver, SchedulerNodeSnapshot

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "placement_language_parity.json"
NOW = dt.datetime(2026, 4, 9, 12, 0, 0)


def _load_cases() -> list[dict[str, Any]]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return list(payload["cases"])


CASE_DEFINITIONS = _load_cases()
CASE_NAMES = [case["name"] for case in CASE_DEFINITIONS]


def _expand_id(prefix: str, index: int, pad_width: int) -> str:
    if pad_width > 0:
        return f"{prefix}{index:0{pad_width}d}"
    return f"{prefix}{index}"


def _expand_nodes(case: dict[str, Any]) -> list[SchedulerNodeSnapshot]:
    nodes: list[SchedulerNodeSnapshot] = []
    for template in case["node_templates"]:
        for offset in range(int(template["count"])):
            node_id = _expand_id(
                str(template["id_prefix"]),
                int(template.get("id_start", 0)) + offset,
                int(template.get("id_pad_width", 0)),
            )
            nodes.append(
                SchedulerNodeSnapshot(
                    node_id=node_id,
                    os=str(template["os"]),
                    arch=str(template["arch"]),
                    executor=str(template["executor"]),
                    zone=str(template.get("zone") or ""),
                    capabilities=frozenset(template.get("capabilities", [])),
                    accepted_kinds=frozenset(template.get("accepted_kinds", [])),
                    max_concurrency=int(template["max_concurrency"]),
                    active_lease_count=int(template["active_lease_count"]),
                    cpu_cores=int(template["cpu_cores"]),
                    memory_mb=int(template["memory_mb"]),
                    gpu_vram_mb=int(template["gpu_vram_mb"]),
                    storage_mb=int(template["storage_mb"]),
                    reliability_score=float(template["reliability_score"]),
                    last_seen_at=NOW - dt.timedelta(seconds=5),
                    enrollment_status=str(template["enrollment_status"]),
                    status=str(template["status"]),
                    drain_status=str(template["drain_status"]),
                    network_latency_ms=int(template.get("network_latency_ms", 0)),
                    bandwidth_mbps=100,
                    cached_data_keys=frozenset(template.get("cached_data_keys", [])),
                    power_capacity_watts=int(template.get("power_capacity_watts", 0)),
                    current_power_watts=int(template.get("current_power_watts", 0)),
                    thermal_state=str(template.get("thermal_state", "normal")),
                    cloud_connectivity=str(template.get("cloud_connectivity", "online")),
                    metadata_json={},
                    executor_contract=str(template.get("executor_contract", "unknown")),
                    supported_workload_kinds=frozenset(template.get("supported_workload_kinds", [])),
                    worker_pools=frozenset(template.get("worker_pools", [])),
                )
            )
    return nodes


def _expand_jobs(case: dict[str, Any]) -> list[SimpleNamespace]:
    jobs: list[SimpleNamespace] = []
    for template in case["job_templates"]:
        for offset in range(int(template["count"])):
            job_id = _expand_id(
                str(template["id_prefix"]),
                int(template.get("id_start", 0)) + offset,
                int(template.get("id_pad_width", 0)),
            )
            gang_size = int(template.get("gang_size", 0))
            gang_id: str | None = None
            if gang_size > 0:
                gang_prefix = str(template.get("gang_prefix") or f"{template['id_prefix']}gang-")
                gang_id = f"{gang_prefix}{offset // gang_size}"
            jobs.append(
                SimpleNamespace(
                    job_id=job_id,
                    kind=str(template["kind"]),
                    priority=int(template["priority_start"]) + offset * int(template.get("priority_step", 0)),
                    gang_id=gang_id,
                    tenant_id=str(template.get("tenant_id", "default")),
                    target_os=template.get("target_os"),
                    target_arch=template.get("target_arch"),
                    target_zone=template.get("target_zone"),
                    target_executor=template.get("target_executor"),
                    required_capabilities=list(template.get("required_capabilities", [])),
                    required_cpu_cores=int(template.get("required_cpu_cores", 0)),
                    required_memory_mb=int(template.get("required_memory_mb", 0)),
                    required_gpu_vram_mb=int(template.get("required_gpu_vram_mb", 0)),
                    required_storage_mb=int(template.get("required_storage_mb", 0)),
                    max_network_latency_ms=int(template.get("max_network_latency_ms", 0)),
                    data_locality_key=template.get("data_locality_key"),
                    prefer_cached_data=bool(template.get("prefer_cached_data", False)),
                    power_budget_watts=int(template.get("power_budget_watts", 0)),
                    thermal_sensitivity=template.get("thermal_sensitivity"),
                    cloud_fallback_enabled=bool(template.get("cloud_fallback_enabled", False)),
                    queue_class=template.get("queue_class"),
                    worker_pool=template.get("worker_pool"),
                    source="parity-fixture",
                    created_at=NOW - dt.timedelta(minutes=5),
                    status="pending",
                    affinity_rules=None,
                    started_at=None,
                    estimated_duration_s=None,
                    sla_seconds=None,
                )
            )
    return jobs


def _run_python_case(case: dict[str, Any]) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    solver = PlacementSolver()
    assignments = solver.solve(
        _expand_jobs(case),
        _expand_nodes(case),
        now=NOW,
        accepted_kinds=set(case["accepted_kinds"]),
        metrics=metrics,
    )
    return {
        "assignments": assignments,
        "feasible_pairs": int(metrics.get("feasible_pairs", 0)),
        "result": str(metrics.get("result", "")),
    }


def _run_go_oracle() -> dict[str, dict[str, Any]]:
    go_binary = shutil.which("go")
    if go_binary is None:
        pytest.skip("Go toolchain is required for placement language parity")

    completed = subprocess.run(
        [go_binary, "run", "./cmd/parity-oracle", "-fixture", str(FIXTURE_PATH)],
        cwd=REPO_ROOT / "placement-solver",
        capture_output=True,
        text=True,
        timeout=180,
        check=True,
    )
    return json.loads(completed.stdout)


def _assert_prefix_expectations(assignments: dict[str, str], expectations: dict[str, Any]) -> None:
    only_prefixes = tuple(expectations.get("only_node_prefixes", []))
    forbidden_prefixes = tuple(expectations.get("forbidden_node_prefixes", []))
    for node_id in assignments.values():
        if only_prefixes:
            assert node_id.startswith(only_prefixes)
        if forbidden_prefixes:
            assert not node_id.startswith(forbidden_prefixes)


def _assert_priority_head_expectations(assignments: dict[str, str], expectations: dict[str, Any]) -> None:
    head_count = int(expectations.get("priority_head_count", 0))
    if head_count <= 0:
        return

    head_prefixes = tuple(expectations.get("priority_head_node_prefixes", []))
    ordered_job_ids = sorted(assignments)
    for job_id in ordered_job_ids[:head_count]:
        assert assignments[job_id].startswith(head_prefixes)


@pytest.fixture(scope="module")
def go_oracle_results() -> dict[str, dict[str, Any]]:
    return _run_go_oracle()


@pytest.mark.parametrize("case_name", CASE_NAMES)
def test_placement_solver_python_and_go_match(case_name: str, go_oracle_results: dict[str, dict[str, Any]]) -> None:
    case = next(case for case in CASE_DEFINITIONS if case["name"] == case_name)
    python_result = _run_python_case(case)
    go_result = go_oracle_results[case_name]
    expectations = case["expectations"]

    assert python_result["assignments"] == go_result["assignments"]
    assert python_result["feasible_pairs"] == go_result["feasible_pairs"]
    assert python_result["result"] == go_result["result"]
    assert python_result["result"] == expectations["result"]
    assert len(python_result["assignments"]) == expectations["assigned_count"]
    _assert_prefix_expectations(python_result["assignments"], expectations)
    _assert_priority_head_expectations(python_result["assignments"], expectations)
