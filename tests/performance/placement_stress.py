"""Large-scale PlacementSolver stress test.

Simulates 1000 nodes × 10000 jobs and measures:
- Solve wall-clock time
- Memory delta
- Placement spread quality (stddev of jobs-per-node)

Run:
    python tests/performance/placement_stress.py
"""

from __future__ import annotations

import datetime
import os
import statistics
import sys
import time
import tracemalloc
from collections import Counter
from unittest.mock import MagicMock

# Ensure project root on sys.path
sys.path.insert(0, ".")

from backend.core.job_scheduler import PlacementSolver, SchedulerNodeSnapshot  # noqa: E402


def _utcnow() -> datetime.datetime:
    return datetime.datetime(2026, 4, 1, 12, 0, 0, tzinfo=datetime.UTC)


def _node(node_id: str, *, max_concurrency: int = 16) -> SchedulerNodeSnapshot:
    return SchedulerNodeSnapshot(
        node_id=node_id,
        os="linux",
        arch="amd64",
        executor="docker",
        zone=f"z{hash(node_id) % 4}",
        capabilities=frozenset({"shell", "docker"}),
        accepted_kinds=frozenset({"shell.exec"}),
        worker_pools=frozenset({"batch"}),
        max_concurrency=max_concurrency,
        active_lease_count=0,
        cpu_cores=16,
        memory_mb=32768,
        gpu_vram_mb=0,
        storage_mb=100000,
        reliability_score=0.95,
        last_seen_at=_utcnow() - datetime.timedelta(seconds=5),
        enrollment_status="active",
        status="online",
        drain_status="active",
        network_latency_ms=10,
        bandwidth_mbps=1000,
        cached_data_keys=frozenset(),
        power_capacity_watts=200,
        current_power_watts=80,
        thermal_state="normal",
        cloud_connectivity="online",
        metadata_json={},
    )


def _job(job_id: str, *, priority: int = 50) -> MagicMock:
    j = MagicMock()
    j.job_id = job_id
    j.kind = "shell.exec"
    j.priority = priority
    j.gang_id = None
    j.tenant_id = "default"
    j.target_os = None
    j.target_arch = None
    j.target_zone = None
    j.target_executor = None
    j.required_capabilities = []
    j.required_cpu_cores = 0
    j.required_memory_mb = 0
    j.required_gpu_vram_mb = 0
    j.required_storage_mb = 0
    j.max_network_latency_ms = None
    j.data_locality_key = None
    j.prefer_cached_data = False
    j.power_budget_watts = None
    j.thermal_sensitivity = None
    j.cloud_fallback_enabled = False
    j.affinity_rules = None
    j.sla_seconds = None
    j.estimated_duration_s = 300
    j.started_at = None
    j.created_at = _utcnow() - datetime.timedelta(minutes=5)
    j.status = "pending"
    j.deadline_at = None
    j.parent_job_id = None
    return j


def main() -> None:
    n_nodes = int(os.getenv("PLACEMENT_STRESS_NODES", "1000"))
    n_jobs = int(os.getenv("PLACEMENT_STRESS_JOBS", "10000"))
    concurrency_per_node = int(os.getenv("PLACEMENT_STRESS_CONCURRENCY", "16"))
    time_threshold_ms = float(os.getenv("PLACEMENT_STRESS_MAX_MS", "5000"))
    memory_threshold_mb = float(os.getenv("PLACEMENT_STRESS_MAX_MEM_MB", "500"))

    print(f"Building {n_nodes} nodes × {n_jobs} jobs …")
    nodes = [_node(f"n{i}", max_concurrency=concurrency_per_node) for i in range(n_nodes)]
    jobs = [_job(f"j{i}", priority=50 + (i % 50)) for i in range(n_jobs)]
    accepted = {"shell.exec"}

    solver = PlacementSolver()

    # Warm-up (small batch to JIT-compile any lazy init)
    solver.solve(jobs[:10], nodes[:5], now=_utcnow(), accepted_kinds=accepted)

    # ── Measure ──────────────────────────────────────────────────────
    tracemalloc.start()
    mem_before = tracemalloc.get_traced_memory()[0]

    t0 = time.perf_counter()
    plan = solver.solve(jobs, nodes, now=_utcnow(), accepted_kinds=accepted)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    mem_after = tracemalloc.get_traced_memory()[0]
    tracemalloc.stop()
    mem_delta_mb = (mem_after - mem_before) / (1024 * 1024)

    # ── Analyse placement quality ────────────────────────────────────
    placed = len(plan)
    total_capacity = n_nodes * concurrency_per_node
    node_counts = Counter(plan.values())
    counts = list(node_counts.values()) if node_counts else [0]
    mean_load = statistics.mean(counts)
    stdev_load = statistics.stdev(counts) if len(counts) > 1 else 0.0
    max_load = max(counts)
    min_load = min(counts)

    # ── Report ───────────────────────────────────────────────────────
    print(f"\n{'=' * 60}")
    print("PlacementSolver Stress Test Results")
    print(f"{'=' * 60}")
    print(f"  Nodes:          {n_nodes}")
    print(f"  Jobs:           {n_jobs}")
    print(f"  Total capacity: {total_capacity} slots")
    print(f"  Placed:         {placed}/{n_jobs} ({placed / n_jobs:.1%})")
    print(f"  Elapsed:        {elapsed_ms:.1f} ms")
    print(f"  Memory delta:   {mem_delta_mb:.1f} MB")
    print("  Spread quality:")
    print(f"    Mean jobs/node: {mean_load:.1f}")
    print(f"    Stdev:          {stdev_load:.2f}")
    print(f"    Min/Max:        {min_load}/{max_load}")
    print(f"{'=' * 60}")

    # ── Gate checks ──────────────────────────────────────────────────
    ok = True
    if elapsed_ms > time_threshold_ms:
        print(f"FAIL: solve time {elapsed_ms:.0f}ms > {time_threshold_ms:.0f}ms threshold")
        ok = False
    else:
        print("PASS: solve time within threshold")

    if mem_delta_mb > memory_threshold_mb:
        print(f"FAIL: memory delta {mem_delta_mb:.0f}MB > {memory_threshold_mb:.0f}MB threshold")
        ok = False
    else:
        print("PASS: memory within threshold")

    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
