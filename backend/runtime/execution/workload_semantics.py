"""Workload Semantics — rich resource model & lifecycle for job kinds.

Moves beyond "generic actions" (connector.invoke, http.request, script.run)
to a proper workload ecosystem with:

1. **WorkloadCategory** — classification (batch, service, interactive,
   streaming, cron) that influences scheduling strategy selection.
2. **ResourceProfile** — default resource requests/limits per kind,
   used by the scheduler when the job doesn't specify explicit resources.
3. **LifecycleHooks** — named lifecycle events (pre_start, health_check,
   post_complete, on_timeout, on_preempt) that executors can implement.
4. **WorkloadDescriptor** — composite metadata for each job kind.

The scheduler consults ``get_workload_descriptor(kind)`` to:
- Select the scheduling profile (batch → backfill-aware, service → spread)
- Apply default resource requests for scoring
- Determine preemptibility and timeout behaviour

References:
- Nomad job spec: type (service | batch | system | sysbatch), resources
- K8s: QoS classes (Guaranteed | Burstable | BestEffort)
- Slurm: job types + QOS + TRES (Trackable Resources)

**Module boundary**
This module owns *read-only job kind metadata*: static descriptors,
resource profiles, QoS classes, lifecycle hook names, and the kind
registry.  It does **not** own scheduling decision logic.  Decisions
that *use* this metadata (priority boosting, preemption, SLA risk)
belong in ``business_scheduling.py``.  Constraint classes belong in
``scheduling_constraints.py``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# =====================================================================
# Workload Categories
# =====================================================================


class WorkloadCategory(str, Enum):
    """High-level workload classification influencing scheduling strategy."""

    BATCH = "batch"  # Finite, offline; tolerant of queuing; backfill candidate
    SERVICE = "service"  # Long-running; spread across zones; non-preemptible
    INTERACTIVE = "interactive"  # Low-latency; prioritise fast scheduling
    STREAMING = "streaming"  # Continuous data processing; location-aware
    CRON = "cron"  # Periodic triggers; deadline-driven
    SYSTEM = "system"  # Platform-internal (health, sync); highest priority


class QoSClass(str, Enum):
    """Quality-of-Service class (inspired by K8s QoS)."""

    GUARANTEED = "guaranteed"  # Resources reserved; non-preemptible
    BURSTABLE = "burstable"  # Min resources guaranteed, can burst
    BEST_EFFORT = "best_effort"  # No resource guarantee; first to preempt


# =====================================================================
# Resource Profile
# =====================================================================


@dataclass(frozen=True, slots=True)
class ResourceProfile:
    """Default resource requirements for a workload kind.

    Used by the scheduler when the job payload doesn't specify explicit
    resource requests. Also serves as documentation of typical resource
    needs for capacity planning.
    """

    cpu_cores: float = 0.0  # Requested CPU cores (fractional)
    memory_mb: int = 0  # Requested memory in MB
    gpu_vram_mb: int = 0  # Requested GPU VRAM in MB
    storage_mb: int = 0  # Requested storage in MB
    network_bandwidth_mbps: int = 0  # Expected network usage
    max_duration_s: int = 0  # Hard timeout ceiling (0 = inherit from kind)
    ephemeral: bool = True  # Whether output is ephemeral or persisted


# =====================================================================
# Lifecycle Hooks
# =====================================================================


@dataclass(frozen=True, slots=True)
class LifecycleHooks:
    """Named lifecycle events that executors can implement per kind.

    Each hook is a string identifier that the executor looks up in its
    hook registry. Empty string = no hook.
    """

    pre_start: str = ""  # Before execution begins (setup volumes, pull image)
    health_check: str = ""  # Periodic health during execution
    post_complete: str = ""  # Cleanup after successful completion
    on_timeout: str = ""  # Graceful shutdown on timeout
    on_preempt: str = ""  # Checkpoint/save state before eviction
    on_retry: str = ""  # Prepare for retry (clean temp files, reset state)


# =====================================================================
# Workload Descriptor
# =====================================================================


@dataclass(frozen=True, slots=True)
class WorkloadDescriptor:
    """Complete workload metadata for a job kind."""

    kind: str
    category: WorkloadCategory = WorkloadCategory.BATCH
    qos: QoSClass = QoSClass.BEST_EFFORT
    resource_profile: ResourceProfile = field(default_factory=ResourceProfile)
    lifecycle: LifecycleHooks = field(default_factory=LifecycleHooks)
    preemptible: bool = True
    restartable: bool = True
    max_retries: int = 3
    gang_capable: bool = False  # Can participate in gang scheduling
    batch_capable: bool = True  # Can participate in batch co-location
    scheduling_profile: str = "default"  # Which SchedulingProfile to use
    description: str = ""


# =====================================================================
# Built-in workload descriptors
# =====================================================================

_WORKLOAD_REGISTRY: dict[str, WorkloadDescriptor] = {}


def register_workload(descriptor: WorkloadDescriptor) -> None:
    """Register a workload descriptor for a job kind."""
    _WORKLOAD_REGISTRY[descriptor.kind] = descriptor


def get_workload_descriptor(kind: str) -> WorkloadDescriptor:
    """Get workload descriptor for a kind, with sensible fallback."""
    if kind in _WORKLOAD_REGISTRY:
        return _WORKLOAD_REGISTRY[kind]
    # Fallback: generic batch workload
    return WorkloadDescriptor(kind=kind, description="Unregistered workload kind")


def list_workload_descriptors() -> list[WorkloadDescriptor]:
    """Return all registered workload descriptors."""
    return sorted(_WORKLOAD_REGISTRY.values(), key=lambda w: w.kind)


def get_workload_info(kind: str) -> dict[str, Any]:
    """Get serialisable workload info for API/diagnostics."""
    w = get_workload_descriptor(kind)
    from backend.runtime.scheduling.worker_pool import default_worker_pool_for_queue_class, infer_queue_class

    queue_class = infer_queue_class(
        kind=w.kind,
        required_gpu_vram_mb=w.resource_profile.gpu_vram_mb,
    )
    return {
        "kind": w.kind,
        "category": w.category.value,
        "qos": w.qos.value,
        "preemptible": w.preemptible,
        "restartable": w.restartable,
        "gang_capable": w.gang_capable,
        "batch_capable": w.batch_capable,
        "scheduling_profile": w.scheduling_profile,
        "default_queue_class": queue_class,
        "default_worker_pool": default_worker_pool_for_queue_class(queue_class),
        "resource_profile": {
            "cpu_cores": w.resource_profile.cpu_cores,
            "memory_mb": w.resource_profile.memory_mb,
            "gpu_vram_mb": w.resource_profile.gpu_vram_mb,
            "storage_mb": w.resource_profile.storage_mb,
            "max_duration_s": w.resource_profile.max_duration_s,
        },
        "lifecycle_hooks": {
            "pre_start": w.lifecycle.pre_start,
            "health_check": w.lifecycle.health_check,
            "post_complete": w.lifecycle.post_complete,
            "on_timeout": w.lifecycle.on_timeout,
            "on_preempt": w.lifecycle.on_preempt,
            "on_retry": w.lifecycle.on_retry,
        },
        "description": w.description,
    }


# ── Register built-in workloads ──────────────────────────────────────

register_workload(
    WorkloadDescriptor(
        kind="noop",
        category=WorkloadCategory.BATCH,
        qos=QoSClass.BEST_EFFORT,
        resource_profile=ResourceProfile(cpu_cores=0.01, memory_mb=16, max_duration_s=5),
        preemptible=True,
        restartable=True,
        max_retries=1,
        description="No-op control-plane validation workload",
    )
)

register_workload(
    WorkloadDescriptor(
        kind="shell.exec",
        category=WorkloadCategory.BATCH,
        qos=QoSClass.BEST_EFFORT,
        resource_profile=ResourceProfile(cpu_cores=0.5, memory_mb=128, max_duration_s=300),
        lifecycle=LifecycleHooks(on_timeout="sigterm_then_kill", on_retry="clean_tmpdir"),
        preemptible=True,
        restartable=True,
        max_retries=3,
        description="Execute a shell command on the target node",
    )
)

register_workload(
    WorkloadDescriptor(
        kind="http.request",
        category=WorkloadCategory.INTERACTIVE,
        qos=QoSClass.BURSTABLE,
        resource_profile=ResourceProfile(cpu_cores=0.1, memory_mb=64, network_bandwidth_mbps=10, max_duration_s=30),
        lifecycle=LifecycleHooks(on_timeout="abort_request"),
        preemptible=True,
        restartable=True,
        max_retries=5,
        description="Execute an HTTP request (webhook, API call)",
    )
)

register_workload(
    WorkloadDescriptor(
        kind="container.run",
        category=WorkloadCategory.BATCH,
        qos=QoSClass.BURSTABLE,
        resource_profile=ResourceProfile(cpu_cores=1.0, memory_mb=512, storage_mb=1024, max_duration_s=600),
        lifecycle=LifecycleHooks(
            pre_start="pull_image",
            health_check="container_healthcheck",
            post_complete="remove_container",
            on_timeout="docker_stop_grace",
            on_preempt="docker_checkpoint",
        ),
        preemptible=True,
        restartable=True,
        max_retries=2,
        gang_capable=True,
        description="Run a container image (Docker/containerd)",
    )
)

register_workload(
    WorkloadDescriptor(
        kind="healthcheck",
        category=WorkloadCategory.SYSTEM,
        qos=QoSClass.GUARANTEED,
        resource_profile=ResourceProfile(cpu_cores=0.05, memory_mb=32, max_duration_s=10),
        preemptible=False,
        restartable=True,
        max_retries=10,
        scheduling_profile="system",
        description="Health probe (HTTP/TCP/DNS/exec)",
    )
)

register_workload(
    WorkloadDescriptor(
        kind="ml.inference",
        category=WorkloadCategory.BATCH,
        qos=QoSClass.GUARANTEED,
        resource_profile=ResourceProfile(cpu_cores=2.0, memory_mb=2048, gpu_vram_mb=4096, max_duration_s=120),
        lifecycle=LifecycleHooks(
            pre_start="load_model",
            on_timeout="save_partial_results",
            on_preempt="save_checkpoint",
        ),
        preemptible=False,
        restartable=False,
        max_retries=1,
        gang_capable=True,
        description="ML model inference (ONNX/TensorRT/PyTorch)",
    )
)

register_workload(
    WorkloadDescriptor(
        kind="media.transcode",
        category=WorkloadCategory.BATCH,
        qos=QoSClass.BURSTABLE,
        resource_profile=ResourceProfile(
            cpu_cores=4.0,
            memory_mb=4096,
            gpu_vram_mb=2048,
            storage_mb=10240,
            max_duration_s=1800,
        ),
        lifecycle=LifecycleHooks(
            pre_start="allocate_scratch",
            post_complete="upload_output",
            on_timeout="save_partial_transcode",
            on_preempt="save_transcode_progress",
        ),
        preemptible=True,
        restartable=True,
        max_retries=2,
        batch_capable=True,
        description="Video/audio transcoding (H.264/H.265/VP9/AV1)",
    )
)

register_workload(
    WorkloadDescriptor(
        kind="script.run",
        category=WorkloadCategory.BATCH,
        qos=QoSClass.BEST_EFFORT,
        resource_profile=ResourceProfile(cpu_cores=0.5, memory_mb=256, max_duration_s=300),
        lifecycle=LifecycleHooks(on_timeout="sigterm_then_kill"),
        preemptible=True,
        restartable=True,
        max_retries=3,
        description="Run an interpreted script (bash/python/node)",
    )
)

register_workload(
    WorkloadDescriptor(
        kind="wasm.run",
        category=WorkloadCategory.INTERACTIVE,
        qos=QoSClass.BURSTABLE,
        resource_profile=ResourceProfile(cpu_cores=0.5, memory_mb=128, max_duration_s=60),
        preemptible=True,
        restartable=True,
        max_retries=2,
        description="WebAssembly sandboxed execution",
    )
)

register_workload(
    WorkloadDescriptor(
        kind="cron.tick",
        category=WorkloadCategory.CRON,
        qos=QoSClass.GUARANTEED,
        resource_profile=ResourceProfile(cpu_cores=0.1, memory_mb=64, max_duration_s=120),
        lifecycle=LifecycleHooks(post_complete="record_next_fire"),
        preemptible=False,
        restartable=True,
        max_retries=5,
        scheduling_profile="cron",
        description="Scheduled cron trigger execution",
    )
)

register_workload(
    WorkloadDescriptor(
        kind="data.sync",
        category=WorkloadCategory.STREAMING,
        qos=QoSClass.BURSTABLE,
        resource_profile=ResourceProfile(
            cpu_cores=0.5,
            memory_mb=256,
            network_bandwidth_mbps=50,
            storage_mb=2048,
            max_duration_s=600,
        ),
        lifecycle=LifecycleHooks(
            pre_start="verify_endpoints",
            post_complete="verify_integrity",
            on_timeout="flush_partial",
            on_preempt="flush_partial",
        ),
        preemptible=True,
        restartable=True,
        max_retries=3,
        description="Edge↔cloud data synchronisation",
    )
)

register_workload(
    WorkloadDescriptor(
        kind="file.transfer",
        category=WorkloadCategory.BATCH,
        qos=QoSClass.BEST_EFFORT,
        resource_profile=ResourceProfile(cpu_cores=0.1, memory_mb=64, storage_mb=1024, max_duration_s=300),
        lifecycle=LifecycleHooks(post_complete="verify_sha256"),
        preemptible=True,
        restartable=True,
        max_retries=3,
        description="Local file copy with integrity verification",
    )
)

register_workload(
    WorkloadDescriptor(
        kind="iot.collect",
        category=WorkloadCategory.STREAMING,
        qos=QoSClass.GUARANTEED,
        resource_profile=ResourceProfile(cpu_cores=0.1, memory_mb=64, network_bandwidth_mbps=5, max_duration_s=0),
        lifecycle=LifecycleHooks(
            pre_start="init_sensor_connection",
            health_check="sensor_heartbeat",
            on_timeout="flush_buffer",
        ),
        preemptible=False,
        restartable=True,
        max_retries=10,
        scheduling_profile="realtime",
        description="IoT sensor data collection (MQTT/Modbus/OPC-UA)",
    )
)

register_workload(
    WorkloadDescriptor(
        kind="connector.invoke",
        category=WorkloadCategory.INTERACTIVE,
        qos=QoSClass.BURSTABLE,
        resource_profile=ResourceProfile(cpu_cores=0.2, memory_mb=128, max_duration_s=30),
        lifecycle=LifecycleHooks(on_timeout="abort_connector"),
        preemptible=True,
        restartable=True,
        max_retries=3,
        description="Generic connector invocation (legacy compatibility)",
    )
)

register_workload(
    WorkloadDescriptor(
        kind="alert.notify",
        category=WorkloadCategory.INTERACTIVE,
        qos=QoSClass.BURSTABLE,
        resource_profile=ResourceProfile(cpu_cores=0.1, memory_mb=64, network_bandwidth_mbps=5, max_duration_s=30),
        lifecycle=LifecycleHooks(on_timeout="abort_request"),
        preemptible=True,
        restartable=True,
        max_retries=5,
        description="Send an outbound alert notification or webhook",
    )
)

register_workload(
    WorkloadDescriptor(
        kind="cron.trigger",
        category=WorkloadCategory.CRON,
        qos=QoSClass.GUARANTEED,
        resource_profile=ResourceProfile(cpu_cores=0.1, memory_mb=64, max_duration_s=60),
        lifecycle=LifecycleHooks(post_complete="record_next_fire"),
        preemptible=False,
        restartable=True,
        max_retries=5,
        scheduling_profile="cron",
        description="Trigger a scheduled webhook or automation job",
    )
)

register_workload(
    WorkloadDescriptor(
        kind="docker.exec",
        category=WorkloadCategory.BATCH,
        qos=QoSClass.BEST_EFFORT,
        resource_profile=ResourceProfile(cpu_cores=0.5, memory_mb=256, max_duration_s=300),
        lifecycle=LifecycleHooks(on_timeout="docker_exec_kill"),
        preemptible=True,
        restartable=True,
        max_retries=2,
        description="Execute command in existing Docker container",
    )
)
