"""Tests for workload semantics (workload_semantics.py)."""

from __future__ import annotations

from backend.core.workload_semantics import (
    QoSClass,
    ResourceProfile,
    WorkloadCategory,
    WorkloadDescriptor,
    get_workload_descriptor,
    get_workload_info,
    list_workload_descriptors,
    register_workload,
)

# ── Category and QoS enums ──────────────────────────────────────────


def test_workload_category_values():
    assert WorkloadCategory.BATCH.value == "batch"
    assert WorkloadCategory.SERVICE.value == "service"
    assert WorkloadCategory.SYSTEM.value == "system"
    assert WorkloadCategory.CRON.value == "cron"


def test_qos_class_values():
    assert QoSClass.GUARANTEED.value == "guaranteed"
    assert QoSClass.BURSTABLE.value == "burstable"
    assert QoSClass.BEST_EFFORT.value == "best_effort"


# ── ResourceProfile ─────────────────────────────────────────────────


def test_resource_profile_defaults():
    rp = ResourceProfile()
    assert rp.cpu_cores == 0.0
    assert rp.memory_mb == 0
    assert rp.gpu_vram_mb == 0
    assert rp.ephemeral is True


def test_resource_profile_custom():
    rp = ResourceProfile(cpu_cores=4.0, memory_mb=8192, gpu_vram_mb=4096)
    assert rp.cpu_cores == 4.0
    assert rp.memory_mb == 8192


# ── Built-in workload descriptors ───────────────────────────────────


def test_builtin_shell_exec():
    w = get_workload_descriptor("shell.exec")
    assert w.kind == "shell.exec"
    assert w.category == WorkloadCategory.BATCH
    assert w.qos == QoSClass.BEST_EFFORT
    assert w.preemptible is True
    assert w.resource_profile.cpu_cores == 0.5


def test_builtin_container_run():
    w = get_workload_descriptor("container.run")
    assert w.kind == "container.run"
    assert w.gang_capable is True
    assert w.resource_profile.memory_mb == 512
    assert w.lifecycle.pre_start == "pull_image"
    assert w.lifecycle.on_preempt == "docker_checkpoint"


def test_builtin_ml_inference():
    w = get_workload_descriptor("ml.inference")
    assert w.qos == QoSClass.GUARANTEED
    assert w.preemptible is False
    assert w.resource_profile.gpu_vram_mb == 4096
    assert w.gang_capable is True


def test_builtin_healthcheck():
    w = get_workload_descriptor("healthcheck")
    assert w.category == WorkloadCategory.SYSTEM
    assert w.preemptible is False
    assert w.scheduling_profile == "system"


def test_builtin_cron_tick():
    w = get_workload_descriptor("cron.tick")
    assert w.category == WorkloadCategory.CRON
    assert w.scheduling_profile == "cron"


def test_builtin_data_sync():
    w = get_workload_descriptor("data.sync")
    assert w.category == WorkloadCategory.STREAMING
    assert w.resource_profile.network_bandwidth_mbps == 50


def test_builtin_iot_collect():
    w = get_workload_descriptor("iot.collect")
    assert w.category == WorkloadCategory.STREAMING
    assert w.qos == QoSClass.GUARANTEED
    assert w.scheduling_profile == "realtime"


def test_builtin_wasm_run():
    w = get_workload_descriptor("wasm.run")
    assert w.category == WorkloadCategory.INTERACTIVE


def test_builtin_http_request():
    w = get_workload_descriptor("http.request")
    assert w.category == WorkloadCategory.INTERACTIVE


def test_builtin_connector_invoke():
    w = get_workload_descriptor("connector.invoke")
    assert w.description == "Generic connector invocation (legacy compatibility)"


def test_builtin_docker_exec():
    w = get_workload_descriptor("docker.exec")
    assert w.category == WorkloadCategory.BATCH


def test_builtin_file_transfer():
    w = get_workload_descriptor("file.transfer")
    assert w.lifecycle.post_complete == "verify_sha256"


def test_builtin_media_transcode():
    w = get_workload_descriptor("media.transcode")
    assert w.resource_profile.gpu_vram_mb == 2048
    assert w.resource_profile.storage_mb == 10240


def test_builtin_script_run():
    w = get_workload_descriptor("script.run")
    assert w.resource_profile.memory_mb == 256


# ── Unknown kind fallback ────────────────────────────────────────────


def test_unknown_kind_fallback():
    w = get_workload_descriptor("some.unknown.kind")
    assert w.kind == "some.unknown.kind"
    assert w.category == WorkloadCategory.BATCH  # default
    assert w.description == "Unregistered workload kind"


# ── Registration ─────────────────────────────────────────────────────


def test_register_custom_workload():
    custom = WorkloadDescriptor(
        kind="custom.test.kind",
        category=WorkloadCategory.SERVICE,
        qos=QoSClass.GUARANTEED,
        resource_profile=ResourceProfile(cpu_cores=8.0, memory_mb=32768),
        preemptible=False,
        description="Custom test workload",
    )
    register_workload(custom)

    w = get_workload_descriptor("custom.test.kind")
    assert w.kind == "custom.test.kind"
    assert w.category == WorkloadCategory.SERVICE
    assert w.resource_profile.cpu_cores == 8.0


# ── List and info ────────────────────────────────────────────────────


def test_list_workload_descriptors():
    descriptors = list_workload_descriptors()
    assert len(descriptors) > 0
    kinds = [d.kind for d in descriptors]
    assert "shell.exec" in kinds
    assert "container.run" in kinds


def test_get_workload_info():
    info = get_workload_info("container.run")
    assert info["kind"] == "container.run"
    assert info["category"] == "batch"
    assert info["gang_capable"] is True
    assert "cpu_cores" in info["resource_profile"]
    assert "pre_start" in info["lifecycle_hooks"]
    assert info["lifecycle_hooks"]["pre_start"] == "pull_image"


def test_get_workload_info_unknown():
    info = get_workload_info("totally.unknown")
    assert info["kind"] == "totally.unknown"
    assert info["description"] == "Unregistered workload kind"
