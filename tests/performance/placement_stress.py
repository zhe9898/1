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


# ---------------------------------------------------------------------------
# Scenario helpers
# ---------------------------------------------------------------------------


def _run_scenario(
    scenario: str,
    jobs: list[MagicMock],
    nodes: list[SchedulerNodeSnapshot],
    accepted: set[str],
    solver: PlacementSolver,
    time_threshold_ms: float,
    memory_threshold_mb: float,
) -> bool:
    """Run one stress scenario and print results.  Returns True if all gate checks pass."""
    tracemalloc.start()
    mem_before = tracemalloc.get_traced_memory()[0]

    t0 = time.perf_counter()
    plan = solver.solve(jobs, nodes, now=_utcnow(), accepted_kinds=accepted)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    mem_after = tracemalloc.get_traced_memory()[0]
    tracemalloc.stop()
    mem_delta_mb = (mem_after - mem_before) / (1024 * 1024)

    placed = len(plan)
    node_counts = Counter(plan.values())
    counts = list(node_counts.values()) if node_counts else [0]
    mean_load = statistics.mean(counts)
    stdev_load = statistics.stdev(counts) if len(counts) > 1 else 0.0
    routing_groups = len({j.kind for j in jobs})

    print(f"\n{'=' * 60}")
    print(f"Scenario: {scenario}")
    print(f"{'=' * 60}")
    print(f"  Nodes:           {len(nodes)}")
    print(f"  Jobs:            {len(jobs)}")
    print(f"  Routing groups:  {routing_groups}  (distinct job kinds)")
    print(f"  Placed:          {placed}/{len(jobs)} ({placed / len(jobs):.1%})")
    print(f"  Elapsed:         {elapsed_ms:.1f} ms")
    print(f"  Memory delta:    {mem_delta_mb:.1f} MB")
    print(f"  Spread quality:  mean={mean_load:.1f}  stdev={stdev_load:.2f}  min/max={min(counts)}/{max(counts)}")
    print(f"{'=' * 60}")

    ok = True
    if elapsed_ms > time_threshold_ms:
        print(f"FAIL  solve time {elapsed_ms:.0f}ms > {time_threshold_ms:.0f}ms threshold")
        ok = False
    else:
        print(f"PASS  solve time {elapsed_ms:.1f}ms ≤ {time_threshold_ms:.0f}ms")

    if mem_delta_mb > memory_threshold_mb:
        print(f"FAIL  memory delta {mem_delta_mb:.0f}MB > {memory_threshold_mb:.0f}MB threshold")
        ok = False
    else:
        print(f"PASS  memory {mem_delta_mb:.1f}MB ≤ {memory_threshold_mb:.0f}MB")

    return ok


def main() -> None:
    n_nodes = int(os.getenv("PLACEMENT_STRESS_NODES", "1000"))
    n_jobs = int(os.getenv("PLACEMENT_STRESS_JOBS", "10000"))
    concurrency_per_node = int(os.getenv("PLACEMENT_STRESS_CONCURRENCY", "16"))
    time_threshold_ms = float(os.getenv("PLACEMENT_STRESS_MAX_MS", "5000"))
    memory_threshold_mb = float(os.getenv("PLACEMENT_STRESS_MAX_MEM_MB", "500"))

    solver = PlacementSolver()

    # Warm-up (small batch to initialise any lazy state)
    _warm_nodes = [_node(f"w{i}", max_concurrency=concurrency_per_node) for i in range(5)]
    _warm_jobs = [_job(f"wj{i}") for i in range(10)]
    solver.solve(_warm_jobs, _warm_nodes, now=_utcnow(), accepted_kinds={"shell.exec"})

    overall_ok = True

    # ── Scenario 1: Homogeneous batch ──────────────────────────────────
    # Classic 1 000 × 10 000 with identical routing contracts.
    # The fast path collapses 10 M candidate pairs to a single group.
    print(f"\nBuilding {n_nodes} nodes × {n_jobs} jobs (homogeneous) …")
    nodes = [_node(f"n{i}", max_concurrency=concurrency_per_node) for i in range(n_nodes)]
    jobs_homo = [_job(f"j{i}", priority=50 + (i % 50)) for i in range(n_jobs)]
    accepted_homo: set[str] = {"shell.exec"}

    ok1 = _run_scenario(
        "Homogeneous batch (1 routing group)",
        jobs_homo,
        nodes,
        accepted_homo,
        solver,
        time_threshold_ms,
        memory_threshold_mb,
    )
    overall_ok = overall_ok and ok1

    # ── Scenario 2: Heterogeneous batch (smart-home mixed workload) ────
    # 10 distinct job kinds × 1 000 jobs each.
    # Exposes the weakness of any "all jobs must be identical" fast path:
    # with 10 routing groups every group still gets the O(J log N) path and
    # the shared capacity map prevents over-commit.
    MIXED_KINDS = [
        "light.toggle",
        "thermostat.set",
        "sensor.query",
        "camera.snapshot",
        "lock.control",
        "fan.speed",
        "sprinkler.run",
        "alarm.trigger",
        "ota.update",
        "shell.exec",
    ]

    def _hetero_node(node_id: str) -> SchedulerNodeSnapshot:
        n = _node(node_id, max_concurrency=concurrency_per_node)
        # All nodes accept every kind
        return SchedulerNodeSnapshot(
            node_id=n.node_id,
            os=n.os,
            arch=n.arch,
            executor=n.executor,
            zone=n.zone,
            capabilities=n.capabilities,
            accepted_kinds=frozenset(MIXED_KINDS),
            worker_pools=n.worker_pools,
            max_concurrency=n.max_concurrency,
            active_lease_count=n.active_lease_count,
            cpu_cores=n.cpu_cores,
            memory_mb=n.memory_mb,
            gpu_vram_mb=n.gpu_vram_mb,
            storage_mb=n.storage_mb,
            reliability_score=n.reliability_score,
            last_seen_at=n.last_seen_at,
            enrollment_status=n.enrollment_status,
            status=n.status,
            drain_status=n.drain_status,
            network_latency_ms=n.network_latency_ms,
            bandwidth_mbps=n.bandwidth_mbps,
            cached_data_keys=n.cached_data_keys,
            power_capacity_watts=n.power_capacity_watts,
            current_power_watts=n.current_power_watts,
            thermal_state=n.thermal_state,
            cloud_connectivity=n.cloud_connectivity,
            metadata_json=n.metadata_json,
        )

    print(f"\nBuilding {n_nodes} nodes × {n_jobs} jobs (heterogeneous, {len(MIXED_KINDS)} kinds) …")
    hetero_nodes = [_hetero_node(f"h{i}") for i in range(n_nodes)]

    def _hetero_job(job_id: str, kind: str, priority: int = 50) -> MagicMock:
        j = _job(job_id, priority=priority)
        j.kind = kind
        return j

    jobs_hetero = [
        _hetero_job(f"hj{i}", MIXED_KINDS[i % len(MIXED_KINDS)], priority=50 + (i % 50))
        for i in range(n_jobs)
    ]
    accepted_hetero = set(MIXED_KINDS)

    ok2 = _run_scenario(
        f"Heterogeneous batch ({len(MIXED_KINDS)} routing groups)",
        jobs_hetero,
        hetero_nodes,
        accepted_hetero,
        solver,
        time_threshold_ms,
        memory_threshold_mb,
    )
    overall_ok = overall_ok and ok2

    if not overall_ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
