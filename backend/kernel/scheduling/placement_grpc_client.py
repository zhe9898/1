"""Async gRPC client for the standalone Go PlacementSolver service.

Architecture
------------
The Python ``PlacementSolver`` is correct and feature-complete but runs
synchronously in CPython, which blocks the FastAPI asyncio event loop for
hundreds of milliseconds at 1 000-node × 10 000-job scale — unacceptable in
an async gateway that must also service device heartbeats and SSE streams.

This module provides two escape hatches, applied in order:

1. **gRPC fast path** — delegates to the Go ``placement-solver`` sidecar over
   a local gRPC channel.  The Go implementation runs the same routing-key
   partition algorithm entirely outside CPython, achieving <10 ms p99 even for
   10 000 heterogeneous jobs across 1 000 nodes.  The channel is lazy-opened
   and kept alive across dispatch cycles.

2. **Thread-pool fallback** — if the gRPC sidecar is unavailable (not deployed,
   starting up, or circuit-broken), the Python solver runs in
   ``asyncio.get_event_loop().run_in_executor(None, ...)`` so the event loop
   is never blocked.

Public API
----------
``async_solve(jobs, nodes, *, now, accepted_kinds, ...) -> dict[str, str]``
    Drop-in async replacement for ``PlacementSolver.solve()``.

``async_build_time_budgeted_placement_plan(...)``
    Drop-in async replacement for ``build_time_budgeted_placement_plan()``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from backend.kernel.scheduling.job_scheduler import SchedulerNodeSnapshot
    from backend.models.job import Job

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

#: gRPC endpoint for the Go placement-solver sidecar.
#: Override via PLACEMENT_SOLVER_GRPC_ADDR environment variable.
_DEFAULT_GRPC_ADDR = "localhost:50055"
_GRPC_ADDR = os.environ.get("PLACEMENT_SOLVER_GRPC_ADDR", _DEFAULT_GRPC_ADDR)

#: Per-call deadline in seconds for the gRPC fast path.
_GRPC_DEADLINE_S = float(os.environ.get("PLACEMENT_SOLVER_GRPC_DEADLINE_S", "0.5"))

#: Number of consecutive gRPC failures before the circuit opens.
_CIRCUIT_TRIP_THRESHOLD = int(os.environ.get("PLACEMENT_SOLVER_CIRCUIT_TRIP", "3"))

#: Seconds to wait before re-probing the gRPC sidecar after a circuit trip.
_CIRCUIT_RESET_S = float(os.environ.get("PLACEMENT_SOLVER_CIRCUIT_RESET_S", "30"))

# ---------------------------------------------------------------------------
# Thread pool for Python fallback (shared across all async_solve calls)
# ---------------------------------------------------------------------------

_SOLVER_EXECUTOR: ThreadPoolExecutor | None = None


def _get_executor() -> ThreadPoolExecutor:
    global _SOLVER_EXECUTOR
    if _SOLVER_EXECUTOR is None:
        # One thread is sufficient: the solver is single-threaded inside and
        # dispatch cycles are serialised per tenant.
        _SOLVER_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="placement-solver")
    return _SOLVER_EXECUTOR


# ---------------------------------------------------------------------------
# gRPC channel (lazy, module-level singleton)
# ---------------------------------------------------------------------------

_grpc_channel: object | None = None
_grpc_stub: object | None = None
_grpc_failures: int = 0
_grpc_circuit_open_at: float = 0.0


def _grpc_available() -> bool:
    """Return True when the circuit breaker allows a gRPC attempt."""
    if _grpc_failures < _CIRCUIT_TRIP_THRESHOLD:
        return True
    return (time.monotonic() - _grpc_circuit_open_at) >= _CIRCUIT_RESET_S


def _get_grpc_stub() -> object | None:
    """Return the gRPC stub, opening the channel lazily.  Returns None if gRPC
    is not installed or the circuit is open."""
    global _grpc_channel, _grpc_stub
    if not _grpc_available():
        return None
    try:
        import grpc

        from backend.core.gen_grpc import placement_pb2_grpc

        placement_pb2_grpc_any = cast(Any, placement_pb2_grpc)

        if _grpc_stub is None:
            _grpc_channel = grpc.insecure_channel(
                _GRPC_ADDR,
                options=[
                    ("grpc.enable_retries", 0),
                    ("grpc.keepalive_time_ms", 10_000),
                ],
            )
            _grpc_stub = placement_pb2_grpc_any.PlacementSolverStub(_grpc_channel)
        return _grpc_stub
    except ImportError:
        return None


def _record_grpc_failure() -> None:
    global _grpc_failures, _grpc_circuit_open_at, _grpc_stub, _grpc_channel
    _grpc_failures += 1
    if _grpc_failures >= _CIRCUIT_TRIP_THRESHOLD:
        _grpc_circuit_open_at = time.monotonic()
        logger.warning(
            "placement_grpc_circuit_open: failures=%d addr=%s reset_in=%.0fs",
            _grpc_failures,
            _GRPC_ADDR,
            _CIRCUIT_RESET_S,
        )
        # Drop the channel so it is re-created after the reset window
        _grpc_stub = None
        _grpc_channel = None


def _record_grpc_success() -> None:
    global _grpc_failures
    if _grpc_failures > 0:
        logger.info("placement_grpc_circuit_closed: addr=%s", _GRPC_ADDR)
    _grpc_failures = 0


# ---------------------------------------------------------------------------
# Job / node → proto conversion helpers
# ---------------------------------------------------------------------------


def _job_to_proto(job: Job) -> object:
    """Convert a Job ORM/mock object to a placement_pb2.JobSpec."""
    from backend.core.gen_grpc import placement_pb2

    placement_pb2_any = cast(Any, placement_pb2)

    def _s(v: object) -> str:
        return str(v) if v is not None else ""

    def _i(v: object) -> int:
        if isinstance(v, bool):
            return 0
        if isinstance(v, (int, float)):
            return int(v)
        return 0

    caps = getattr(job, "required_capabilities", None) or []
    return placement_pb2_any.JobSpec(
        job_id=_s(getattr(job, "job_id", "")),
        kind=_s(getattr(job, "kind", "")),
        priority=_i(getattr(job, "priority", 50)),
        gang_id=_s(getattr(job, "gang_id", "")),
        tenant_id=_s(getattr(job, "tenant_id", "default")),
        target_os=_s(getattr(job, "target_os", "")),
        target_arch=_s(getattr(job, "target_arch", "")),
        target_zone=_s(getattr(job, "target_zone", "")),
        target_executor=_s(getattr(job, "target_executor", "")),
        required_capabilities=[str(c) for c in caps if c],
        required_cpu_cores=_i(getattr(job, "required_cpu_cores", 0)),
        required_memory_mb=_i(getattr(job, "required_memory_mb", 0)),
        required_gpu_vram_mb=_i(getattr(job, "required_gpu_vram_mb", 0)),
        required_storage_mb=_i(getattr(job, "required_storage_mb", 0)),
        max_network_latency_ms=_i(getattr(job, "max_network_latency_ms", 0)),
        data_locality_key=_s(getattr(job, "data_locality_key", "")),
        prefer_cached_data=bool(getattr(job, "prefer_cached_data", False)),
        power_budget_watts=_i(getattr(job, "power_budget_watts", 0)),
        thermal_sensitivity=_s(getattr(job, "thermal_sensitivity", "")),
        cloud_fallback_enabled=bool(getattr(job, "cloud_fallback_enabled", False)),
        queue_class=_s(getattr(job, "queue_class", "")),
        worker_pool=_s(getattr(job, "worker_pool", "")),
    )


def _node_to_proto(node: SchedulerNodeSnapshot) -> object:
    """Convert a SchedulerNodeSnapshot to a placement_pb2.NodeSpec."""
    from backend.core.gen_grpc import placement_pb2

    placement_pb2_any = cast(Any, placement_pb2)

    return placement_pb2_any.NodeSpec(
        node_id=node.node_id,
        os=node.os,
        arch=node.arch,
        executor=node.executor,
        zone=node.zone or "",
        capabilities=list(node.capabilities),
        accepted_kinds=list(node.accepted_kinds),
        worker_pools=list(node.worker_pools),
        max_concurrency=node.max_concurrency,
        active_lease_count=node.active_lease_count,
        cpu_cores=node.cpu_cores,
        memory_mb=node.memory_mb,
        gpu_vram_mb=node.gpu_vram_mb,
        storage_mb=node.storage_mb,
        reliability_score=float(node.reliability_score),
        enrollment_status=node.enrollment_status,
        status=node.status,
        drain_status=node.drain_status,
        network_latency_ms=node.network_latency_ms,
        cached_data_keys=list(node.cached_data_keys),
        power_capacity_watts=node.power_capacity_watts,
        current_power_watts=node.current_power_watts,
        thermal_state=node.thermal_state,
        cloud_connectivity=node.cloud_connectivity,
    )


# ---------------------------------------------------------------------------
# gRPC fast path
# ---------------------------------------------------------------------------


def _grpc_solve_sync(
    jobs: list[Job],
    nodes: list[SchedulerNodeSnapshot],
    accepted_kinds: set[str],
    budget_ms: int,
) -> dict[str, str] | None:
    """Synchronous gRPC call — intended to be run in a thread pool.

    Returns None on any failure (caller should fall through to Python solver).
    """
    stub = _get_grpc_stub()
    if stub is None:
        return None

    try:
        from backend.core.gen_grpc import placement_pb2

        placement_pb2_any = cast(Any, placement_pb2)

        req = placement_pb2_any.SolveRequest(
            jobs=[_job_to_proto(j) for j in jobs],
            nodes=[_node_to_proto(n) for n in nodes],
            accepted_kinds=list(accepted_kinds),
            budget_ms=budget_ms,
        )
        stub_any = cast(Any, stub)
        resp = stub_any.Solve(req, timeout=_GRPC_DEADLINE_S)
        _record_grpc_success()
        logger.debug(
            "placement_grpc_solve: assigned=%d result=%s elapsed_us=%d",
            len(resp.assignments),
            resp.result,
            resp.elapsed_us,
        )
        return dict(resp.assignments)
    except Exception as exc:
        _record_grpc_failure()
        logger.warning("placement_grpc_solve_failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Public async API
# ---------------------------------------------------------------------------


async def async_solve(
    jobs: list[Job],
    nodes: list[SchedulerNodeSnapshot],
    *,
    now: object,
    accepted_kinds: set[str],
    recent_failed_job_ids: set[str] | None = None,
    active_jobs_by_node: dict[str, list[Job]] | None = None,
    metrics: dict[str, object] | None = None,
    deadline_monotonic: float | None = None,
) -> dict[str, str]:
    """Async drop-in for ``PlacementSolver.solve()``.

    Tries the Go gRPC sidecar first; falls back to the Python solver in a
    thread-pool executor so the asyncio event loop is never blocked.
    """
    if not jobs or not nodes:
        return {}

    loop = asyncio.get_running_loop()
    executor = _get_executor()

    budget_ms = 0
    if deadline_monotonic is not None:
        remaining = (deadline_monotonic - time.monotonic()) * 1000
        budget_ms = max(int(remaining), 0)

    # ── Try gRPC fast path ────────────────────────────────────────────────
    if _grpc_available():
        grpc_result = await loop.run_in_executor(
            executor,
            _grpc_solve_sync,
            jobs,
            nodes,
            accepted_kinds,
            budget_ms,
        )
        if grpc_result is not None:
            if metrics is not None:
                metrics["solver_backend"] = "grpc"
                metrics["assignments"] = len(grpc_result)
            return grpc_result

    # ── Thread-pool Python fallback ───────────────────────────────────────
    from backend.kernel.scheduling.placement_solver import get_placement_solver

    solver = get_placement_solver()

    def _py_solve() -> dict[str, str]:
        return solver.solve(
            jobs,
            nodes,
            now=now,
            accepted_kinds=accepted_kinds,
            recent_failed_job_ids=recent_failed_job_ids,
            active_jobs_by_node=active_jobs_by_node,
            metrics=metrics,
            deadline_monotonic=deadline_monotonic,
        )

    result = await loop.run_in_executor(executor, _py_solve)
    if metrics is not None:
        metrics["solver_backend"] = "python_threadpool"
    return result


async def async_build_time_budgeted_placement_plan(
    jobs: list[Job],
    nodes: list[SchedulerNodeSnapshot],
    *,
    now: object,
    accepted_kinds: set[str],
    recent_failed_job_ids: set[str] | None = None,
    active_jobs_by_node: dict[str, list[Job]] | None = None,
    decision_context: dict[str, object] | None = None,
) -> dict[str, str]:
    """Async drop-in for ``build_time_budgeted_placement_plan()``.

    Respects the same solver config (enabled flag, windows, budget) as the
    synchronous version, but never blocks the event loop.
    """
    import datetime as _dt

    from backend.kernel.scheduling.placement_solver import _get_solver_config

    _now: _dt.datetime = now  # type: ignore[assignment]

    solver_cfg = _get_solver_config()
    if decision_context is not None:
        decision_context.clear()
        decision_context.update(
            {
                "enabled": bool(solver_cfg.enabled_in_dispatch),
                "attempted": False,
                "candidate_jobs": len(jobs),
                "candidate_nodes": len(nodes),
                "candidate_pairs_upper_bound": len(jobs) * len(nodes),
                "dispatch_time_budget_ms": solver_cfg.dispatch_time_budget_ms,
                "timed_out": False,
                "assignments": 0,
            }
        )
    if not solver_cfg.enabled_in_dispatch:
        if decision_context is not None:
            decision_context["reason"] = "disabled"
        return {}
    if not jobs or not nodes:
        if decision_context is not None:
            decision_context["reason"] = "empty_window"
        return {}
    if len(jobs) > solver_cfg.max_jobs_per_dispatch:
        if decision_context is not None:
            decision_context["reason"] = "oversized_job_window"
        return {}
    if len(nodes) > solver_cfg.max_nodes_per_dispatch:
        if decision_context is not None:
            decision_context["reason"] = "oversized_node_window"
        return {}
    if len(jobs) * len(nodes) > solver_cfg.max_candidate_pairs_per_dispatch:
        if decision_context is not None:
            decision_context["reason"] = "oversized_candidate_matrix"
        return {}

    deadline_monotonic: float | None = None
    if solver_cfg.dispatch_time_budget_ms > 0:
        deadline_monotonic = time.monotonic() + (solver_cfg.dispatch_time_budget_ms / 1000.0)

    if decision_context is not None:
        decision_context["attempted"] = True
        decision_context["reason"] = "solver_attempted"

    plan = await async_solve(
        jobs,
        nodes,
        now=_now,
        accepted_kinds=accepted_kinds,
        recent_failed_job_ids=recent_failed_job_ids,
        active_jobs_by_node=active_jobs_by_node,
        metrics=decision_context,
        deadline_monotonic=deadline_monotonic,
    )
    if decision_context is not None:
        decision_context["assignments"] = len(plan)
        decision_context["reason"] = str(decision_context.get("result", "planned" if plan else "no_assignments"))
    return plan
