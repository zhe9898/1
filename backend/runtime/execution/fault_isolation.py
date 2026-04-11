from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FaultIsolationContract:
    runner_api_client_timeout_seconds: int
    lease_renewal_min_interval_seconds: int
    lease_renewal_failure_abandon_after: int
    reporting_timeout_seconds: int
    graceful_drain_timeout_seconds: int
    execution_headroom_seconds: int
    default_execution_timeout_seconds: int
    process_isolation: str
    stale_lease_guard: str
    reporting_context_strategy: str


FAULT_ISOLATION_CONTRACT = FaultIsolationContract(
    runner_api_client_timeout_seconds=30,
    lease_renewal_min_interval_seconds=5,
    lease_renewal_failure_abandon_after=3,
    reporting_timeout_seconds=15,
    graceful_drain_timeout_seconds=30,
    execution_headroom_seconds=5,
    default_execution_timeout_seconds=300,
    process_isolation="control-plane workers run out-of-process and runner execution is isolated from the API process",
    stale_lease_guard="machine callbacks require the current node_id + attempt + lease_token owner tuple",
    reporting_context_strategy="final result/failure reporting uses a timeout-bounded context detached from parent cancellation",
)


def export_fault_isolation_contract() -> dict[str, object]:
    contract = FAULT_ISOLATION_CONTRACT
    return {
        "runner_api_client_timeout_seconds": contract.runner_api_client_timeout_seconds,
        "lease_renewal": {
            "min_interval_seconds": contract.lease_renewal_min_interval_seconds,
            "failure_abandon_after": contract.lease_renewal_failure_abandon_after,
            "formula": "max(5, lease_seconds / 2)",
            "backoff_cap_formula": "min(renew_interval - 1s, 30s, max(1, lease_seconds - 1)s)",
        },
        "reporting": {
            "timeout_seconds": contract.reporting_timeout_seconds,
            "strategy": contract.reporting_context_strategy,
        },
        "graceful_shutdown": {
            "drain_timeout_seconds": contract.graceful_drain_timeout_seconds,
            "strategy": "notify backend drain endpoint using a timeout-bounded WithoutCancel context",
        },
        "execution_timeout": {
            "headroom_seconds": contract.execution_headroom_seconds,
            "default_timeout_seconds": contract.default_execution_timeout_seconds,
            "formula": "lease>10 => lease-5; lease>0 => lease; otherwise default",
        },
        "stale_lease_guard": contract.stale_lease_guard,
        "process_isolation": contract.process_isolation,
        "source_files": {
            "poller": "runner-agent/internal/jobs/poller.go",
            "executor": "runner-agent/internal/exec/executor.go",
            "service": "runner-agent/internal/service/service.go",
            "api_client": "runner-agent/internal/api/client.go",
            "lifecycle_api": "backend/control_plane/adapters/jobs/lifecycle.py",
            "control_worker": "backend/workers/control_plane_worker.py",
        },
    }
