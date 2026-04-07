"""
Kernel Boundary Hardening Tests

Tests to ensure default kernel maintains clean boundaries and does not
leak business/ops/AI capabilities.

Based on: docs/KERNEL_CLOSURE_CHECKLIST.md
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.core.control_plane import load_control_plane_surfaces

REPO_ROOT = Path(__file__).resolve().parents[1]


# ============================================================================
# P0: Control-plane source direction
# ============================================================================


def test_control_plane_surfaces_defined_in_backend():
    """Backend is the single source of truth for control-plane surfaces."""
    control_plane = (REPO_ROOT / "backend" / "core" / "control_plane.py").read_text(encoding="utf-8")

    # Should NOT read from frontend
    assert "frontend/src/config/controlPlaneSurfaces.json" not in control_plane, (
        "Backend should not read control-plane surfaces from frontend"
    )

    # Should have hardcoded surfaces
    assert "_KERNEL_CONTROL_PLANE_SURFACES" in control_plane, (
        "Backend should define control-plane surfaces as constants"
    )

    surface_keys = {surface.surface_key for surface in load_control_plane_surfaces()}
    assert surface_keys == {
        "dashboard",
        "nodes",
        "jobs",
        "connectors",
        "triggers",
        "reservations",
        "evaluations",
        "settings",
    }


def test_console_api_exposes_surfaces_endpoint():
    """Console API should expose /surfaces endpoint for frontend."""
    console_api = (REPO_ROOT / "backend" / "api" / "console.py").read_text(encoding="utf-8")

    assert '@router.get("/surfaces"' in console_api, (
        "Console API should expose /surfaces endpoint"
    )
    assert "ControlPlaneSurfacesResponse" in console_api, (
        "Console API should return control-plane surfaces"
    )


# ============================================================================
# P1: Capabilities boundary
# ============================================================================


def test_capabilities_only_exposes_kernel_surfaces():
    """Capabilities module should only expose kernel control-plane surfaces."""
    capabilities = (REPO_ROOT / "backend" / "capabilities.py").read_text(encoding="utf-8")

    # Should have kernel surfaces
    assert "Gateway Dashboard" in capabilities
    assert "Gateway Nodes" in capabilities
    assert "Gateway Jobs" in capabilities
    assert "Gateway Connectors" in capabilities
    assert "Gateway Settings" in capabilities

    # Should NOT have business/ops/AI capabilities
    forbidden_capabilities = [
        "容灾备份",
        "Local LLM Agent",
        "对话记忆",
        "对话日总结",
        "场景编排",
        "定时调度",
        "能耗监测",
        "语音控制",
        "集群状态",
    ]

    for forbidden in forbidden_capabilities:
        assert forbidden not in capabilities, (
            f"Capabilities should not expose {forbidden} (should be in pack)"
        )


# ============================================================================
# P1: Settings boundary
# ============================================================================


def test_settings_api_only_exposes_kernel_endpoints():
    """Settings API should only expose kernel runtime settings."""
    settings_api = (REPO_ROOT / "backend" / "api" / "settings.py").read_text(encoding="utf-8")

    # Should NOT have AI-related endpoints
    forbidden_endpoints = [
        "@router.get(\"/ai-models\")",
        "@router.post(\"/ai-models/scan\")",
        "@router.get(\"/ai-providers/health\")",
        "@router.get(\"/ai-providers/endpoints\")",
        "@router.put(\"/ai-providers/{provider}/url\")",
        "@router.put(\"/ai-model\")",
        "@router.get(\"/system\")",  # GPU/disk info
    ]

    for forbidden in forbidden_endpoints:
        assert forbidden not in settings_api, (
            f"Settings API should not expose {forbidden} (should be in AI/Ops pack)"
        )


# ============================================================================
# P1: Switches boundary
# ============================================================================


def test_switches_api_not_in_default_kernel():
    """Switches API should not be in default kernel (should be in IoT pack)."""
    routes = (REPO_ROOT / "backend" / "api" / "routes.py").read_text(encoding="utf-8")

    assert "toggle_switch" not in routes, (
        "Switches API should not be in default kernel (should be in IoT pack)"
    )
    assert "@router.get(\"/switches\"" not in routes, (
        "Switches routes should not be in default kernel (should be in IoT pack)"
    )
    assert "@router.post(\"/switches" not in routes, (
        "Switches routes should not be in default kernel (should be in IoT pack)"
    )


# ============================================================================
# Edge Computing: Kind dimension
# ============================================================================


def test_node_model_has_accepted_kinds_field():
    """Node model should have accepted_kinds as formal contract field."""
    node_model = (REPO_ROOT / "backend" / "models" / "node.py").read_text(encoding="utf-8")

    assert "accepted_kinds" in node_model, (
        "Node model should have accepted_kinds field as formal contract"
    )


def test_scheduler_snapshot_includes_accepted_kinds():
    """Scheduler node snapshot should include accepted_kinds."""
    scheduler = (REPO_ROOT / "backend" / "core" / "job_scheduler.py").read_text(encoding="utf-8")

    # SchedulerNodeSnapshot should have accepted_kinds
    assert "accepted_kinds: frozenset[str]" in scheduler, (
        "SchedulerNodeSnapshot should include accepted_kinds"
    )

    # build_node_snapshot should populate accepted_kinds
    assert "accepted_kinds=frozenset" in scheduler, (
        "build_node_snapshot should populate accepted_kinds from node"
    )


def test_node_blockers_uses_node_contract_accepted_kinds():
    """node_blockers_for_job should use node contract accepted_kinds."""
    scheduler = (REPO_ROOT / "backend" / "core" / "job_scheduler.py").read_text(encoding="utf-8")

    # Should check node.accepted_kinds first
    assert "if node.accepted_kinds:" in scheduler, (
        "node_blockers_for_job should check node contract accepted_kinds"
    )
    assert "not-in-node-contract" in scheduler, (
        "node_blockers_for_job should report kind not in node contract"
    )


# ============================================================================
# Edge Computing: Advanced scheduling factors
# ============================================================================


def test_job_model_has_edge_computing_fields():
    """Job model should have edge computing scheduling fields."""
    job_model = (REPO_ROOT / "backend" / "models" / "job.py").read_text(encoding="utf-8")

    edge_fields = [
        "data_locality_key",
        "max_network_latency_ms",
        "prefer_cached_data",
        "power_budget_watts",
        "thermal_sensitivity",
        "cloud_fallback_enabled",
    ]

    for field in edge_fields:
        assert field in job_model, (
            f"Job model should have {field} for edge computing scheduling"
        )


def test_node_model_has_edge_computing_fields():
    """Node model should have edge computing attributes."""
    node_model = (REPO_ROOT / "backend" / "models" / "node.py").read_text(encoding="utf-8")

    edge_fields = [
        "network_latency_ms",
        "bandwidth_mbps",
        "cached_data_keys",
        "power_capacity_watts",
        "current_power_watts",
        "thermal_state",
        "cloud_connectivity",
    ]

    for field in edge_fields:
        assert field in node_model, (
            f"Node model should have {field} for edge computing scheduling"
        )


def test_scheduler_snapshot_includes_edge_attributes():
    """Scheduler node snapshot should include edge computing attributes."""
    scheduler = (REPO_ROOT / "backend" / "core" / "job_scheduler.py").read_text(encoding="utf-8")

    edge_attrs = [
        "network_latency_ms: int",
        "bandwidth_mbps: int",
        "cached_data_keys: frozenset[str]",
        "power_capacity_watts: int",
        "current_power_watts: int",
        "thermal_state: str",
        "cloud_connectivity: str",
    ]

    for attr in edge_attrs:
        assert attr in scheduler, (
            f"SchedulerNodeSnapshot should include {attr}"
        )


def test_node_blockers_checks_edge_constraints():
    """node_blockers_for_job should check edge computing constraints."""
    scheduler = (REPO_ROOT / "backend" / "core" / "job_scheduler.py").read_text(encoding="utf-8")

    edge_checks = [
        "max_network_latency_ms",
        "data_locality_key",
        "power_budget_watts",
        "thermal_sensitivity",
        "cloud_fallback_enabled",
    ]

    for check in edge_checks:
        assert check in scheduler, (
            f"node_blockers_for_job should check {check} constraint"
        )


def test_score_job_includes_edge_factors():
    """score_job_for_node should include edge computing scoring factors."""
    scheduler = (REPO_ROOT / "backend" / "core" / "job_scoring.py").read_text(encoding="utf-8")

    # Find score_job_for_node function
    func_start = scheduler.find("def score_job_for_node(")
    func_end = scheduler.find("\ndef ", func_start + 1)
    func_body = scheduler[func_start:func_end]

    edge_factors = [
        "data_locality_key",
        "network_latency_ms",
        "power_bonus",
        "thermal_bonus",
    ]

    for factor in edge_factors:
        assert factor in func_body, (
            f"score_job_for_node should include {factor} in scoring"
        )


# ============================================================================
# Summary
# ============================================================================


def test_kernel_boundary_hardening_summary():
    """Summary of kernel boundary hardening status."""
    status = {
        "P0 - Control-plane source": "✅ Fixed (backend is source of truth)",
        "P1 - Capabilities boundary": "✅ Fixed (only kernel surfaces)",
        "P1 - Settings boundary": "✅ Fixed (AI/system endpoints removed)",
        "P1 - Switches boundary": "✅ Fixed (switches API removed)",
        "Edge - Kind dimension": "✅ Fixed (accepted_kinds in node contract)",
        "Edge - Advanced factors": "✅ Fixed (latency/data/power/thermal)",
    }

    print("\n" + "=" * 80)
    print("Kernel Boundary Hardening Status")
    print("=" * 80)
    for item, state in status.items():
        print(f"{item}: {state}")
    print("=" * 80 + "\n")
