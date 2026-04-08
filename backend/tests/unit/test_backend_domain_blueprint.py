from __future__ import annotations

from pathlib import Path

from backend.kernel.governance.domain_blueprint import (
    EXTERNAL_RUNTIME_INVARIANTS,
    PRIORITY_SPLITS,
    TARGET_BACKEND_DOMAINS,
    export_backend_domain_blueprint,
)


def test_domain_blueprint_locks_three_target_domains() -> None:
    assert tuple(domain.key for domain in TARGET_BACKEND_DOMAINS) == (
        "kernel",
        "control_plane",
        "platform",
    )


def test_kernel_domain_keeps_topology_as_first_class_subdomain() -> None:
    kernel_domain = next(domain for domain in TARGET_BACKEND_DOMAINS if domain.key == "kernel")
    assert any(subdomain.key == "topology" for subdomain in kernel_domain.subdomains)


def test_external_invariants_preserve_public_surface_and_policy_chain() -> None:
    invariants = {item.key: item for item in EXTERNAL_RUNTIME_INVARIANTS}

    assert "gateway-kernel" in invariants["kernel_only_runtime_surface"].statement
    assert "backend-driven" in invariants["backend_driven_control_plane"].statement
    assert "PolicyStore plus RuntimePolicyResolver" in invariants["runtime_policy_single_source"].statement
    assert "capability -> surface -> policy -> service contract -> execution contract" in invariants["extensions_follow_contract_chain"].statement


def test_control_plane_split_is_explicitly_three_way() -> None:
    split = next(item for item in PRIORITY_SPLITS if item.current_module == "backend/core/control_plane.py")

    assert split.status == "completed"
    assert split.target_modules == (
        "backend/kernel/surfaces/contracts.py",
        "backend/kernel/surfaces/registry.py",
        "backend/control_plane/console/manifest_service.py",
    )
    assert "kernel" in split.why.lower()
    assert "control plane" in split.why.lower()


def test_blueprint_targets_do_not_reintroduce_backend_core() -> None:
    exported = export_backend_domain_blueprint()

    target_paths = [path for split in exported["priority_splits"] for path in split["target_modules"]]
    assert target_paths
    assert all(not path.startswith("backend/core/") for path in target_paths)


def test_kernel_capability_registry_split_is_completed() -> None:
    split = next(item for item in PRIORITY_SPLITS if item.current_module == "backend/core/kernel_capabilities.py")

    assert split.status == "completed"
    assert split.target_modules == ("backend/kernel/capabilities/registry.py",)


def test_pack_and_profile_splits_are_completed() -> None:
    pack_split = next(item for item in PRIORITY_SPLITS if item.current_module == "backend/core/pack_registry.py")
    profile_split = next(item for item in PRIORITY_SPLITS if item.current_module == "backend/core/gateway_profile.py")

    assert pack_split.status == "completed"
    assert pack_split.target_modules == (
        "backend/kernel/packs/registry.py",
        "backend/kernel/packs/presets.py",
        "backend/kernel/topology/pack_selection.py",
    )
    assert profile_split.status == "completed"
    assert profile_split.target_modules == (
        "backend/kernel/profiles/public_profile.py",
        "backend/kernel/topology/profile_selection.py",
    )


def test_governance_splits_are_completed() -> None:
    architecture_split = next(item for item in PRIORITY_SPLITS if item.current_module == "backend/core/architecture_governance.py")
    owner_split = next(item for item in PRIORITY_SPLITS if item.current_module == "backend/core/aggregate_owner_registry.py")

    assert architecture_split.status == "completed"
    assert architecture_split.target_modules == ("backend/kernel/governance/architecture_rules.py",)
    assert owner_split.status == "completed"
    assert owner_split.target_modules == ("backend/kernel/governance/aggregate_owner_registry.py",)


def test_platform_blueprint_subdomains_exist_on_disk() -> None:
    root = Path(__file__).resolve().parents[3]
    assert (root / "backend" / "platform" / "db").is_dir()
    assert (root / "backend" / "platform" / "redis").is_dir()
    assert (root / "backend" / "platform" / "http").is_dir()
    assert (root / "backend" / "platform" / "logging").is_dir()
    assert (root / "backend" / "platform" / "telemetry").is_dir()
    assert (root / "backend" / "platform" / "security").is_dir()
