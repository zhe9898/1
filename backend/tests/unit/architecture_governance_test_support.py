from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.control_plane.auth.authority_boundary import export_auth_boundary_contract
from backend.extensions.extension_guard import (
    assert_budgeted_payload,
    export_extension_budget_contract,
    validate_extension_manifest_contract,
    validate_scheduling_profile_budget,
)
from backend.kernel.capabilities.registry import capability_keys
from backend.kernel.contracts.status import export_status_compatibility_rules
from backend.kernel.governance.aggregate_owner_registry import export_aggregate_owner_registry, unique_owner_service_map
from backend.kernel.governance.architecture_rules import (
    export_architecture_governance_rules,
    export_architecture_governance_snapshot,
)
from backend.kernel.governance.development_cleanroom import export_development_cleanroom_contract
from backend.kernel.governance.domain_import_fence import export_backend_domain_import_fence
from backend.kernel.policy.runtime_policy_resolver import export_runtime_policy_contract
from backend.kernel.surfaces.registry import export_surface_registry
from backend.platform.events.channels import export_event_channel_contract, tenant_realtime_subject, tenant_subject_token
from backend.platform.redis.runtime_state import export_runtime_state_contract
from backend.runtime.execution.fault_isolation import export_fault_isolation_contract
from backend.runtime.execution.lease_service import export_lease_service_contract
from backend.runtime.scheduling.scheduling_framework import SchedulingProfile
from backend.runtime.topology.runtime_contracts import control_plane_persona_keys, export_runtime_contract_taxonomy
from tools.auth_boundary_guard import auth_boundary_violations
from tools.auth_tenant_boundary_guard import auth_tenant_boundary_violations
from tools.backend_domain_fence import backend_domain_import_fence_violations
from tools.cookie_boundary_guard import cookie_boundary_violations
from tools.development_cleanroom_guard import development_cleanroom_violations
from tools.tenant_claim_guard import tenant_claim_violations

ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = ROOT / "backend"
RUNNER_ROOT = ROOT / "runner-agent"
SCANNED_SOURCE_FOLDERS = ("control_plane", "core", "kernel", "runtime", "extensions", "workers", "sentinel")

_OWNER_MODULES_BY_FIELD: dict[tuple[str, str], set[str]] = {
    ("job", "status"): {
        "backend/runtime/execution/job_lifecycle_service.py",
        "backend/runtime/execution/lease_service.py",
    },
    ("job", "attempt"): {"backend/runtime/execution/lease_service.py"},
    ("job", "lease_token"): {"backend/runtime/execution/lease_service.py"},
    ("job", "leased_until"): {"backend/runtime/execution/lease_service.py"},
    ("attempt", "status"): {"backend/runtime/execution/lease_service.py"},
    ("attempt", "lease_token"): {"backend/runtime/execution/lease_service.py"},
    ("attempt", "scheduling_decision_id"): {"backend/runtime/execution/lease_service.py"},
    ("node", "enrollment_status"): {"backend/runtime/topology/node_enrollment_service.py"},
    ("node", "drain_status"): {"backend/runtime/topology/node_enrollment_service.py"},
    ("node", "drain_until"): {"backend/runtime/topology/node_enrollment_service.py"},
    ("connector", "status"): {"backend/extensions/connector_service.py"},
    ("connector", "config"): {"backend/extensions/connector_service.py"},
    ("trigger", "status"): {"backend/extensions/trigger_command_service.py"},
    ("delivery", "status"): {"backend/extensions/trigger_command_service.py"},
    ("workflow", "status"): {"backend/extensions/workflow_command_service.py"},
    ("policy", "config_version"): {"backend/runtime/scheduling/scheduling_policy_service.py"},
    ("flag", "enabled"): {"backend/kernel/policy/feature_flag_service.py"},
    ("flag", "updated_by"): {"backend/kernel/policy/feature_flag_service.py"},
}

_LEASE_ONLY_FIELDS: set[tuple[str, str]] = {
    ("job", "attempt"),
    ("job", "lease_token"),
    ("job", "leased_until"),
    ("attempt", "status"),
    ("attempt", "lease_token"),
    ("attempt", "scheduling_decision_id"),
}


def _python_sources(*folders: str) -> list[Path]:
    paths: list[Path] = []
    for folder in folders:
        paths.extend(sorted((BACKEND_ROOT / folder).rglob("*.py")))
    return paths


def _runner_text(*parts: str) -> str:
    return (RUNNER_ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _flatten_targets(target: ast.expr) -> list[ast.expr]:
    if isinstance(target, (ast.Tuple, ast.List)):
        values: list[ast.expr] = []
        for item in target.elts:
            values.extend(_flatten_targets(item))
        return values
    return [target]


def _expr_chain(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.Name):
        return (node.id,)
    if isinstance(node, ast.Attribute):
        return (*_expr_chain(node.value), node.attr)
    if isinstance(node, ast.Call):
        return _expr_chain(node.func)
    return ()


def _assignment_pairs(path: Path) -> list[tuple[int, tuple[str, str]]]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    pairs: list[tuple[int, tuple[str, str]]] = []
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            for target in node.targets:
                targets.extend(_flatten_targets(target))
        elif isinstance(node, ast.AnnAssign | ast.AugAssign):
            targets.extend(_flatten_targets(node.target))
        else:
            continue

        for target in targets:
            if not isinstance(target, ast.Attribute):
                continue
            if not isinstance(target.value, ast.Name):
                continue
            pair = (target.value.id, target.attr)
            if pair in _OWNER_MODULES_BY_FIELD:
                pairs.append((getattr(target, "lineno", 0), pair))
    return pairs


def _dict_literal_string_keys(node: ast.AST | None) -> set[str]:
    if not isinstance(node, ast.Dict):
        return set()
    keys: set[str] = set()
    for key in node.keys:
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            keys.add(key.value)
    return keys


def _function_def(tree: ast.AST, name: str) -> ast.AsyncFunctionDef | ast.FunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return node
    return None


def _call_line(function_def: ast.AsyncFunctionDef | ast.FunctionDef, call_name: str) -> int | None:
    matching_lines = [
        getattr(child, "lineno", 0) for child in ast.walk(function_def) if isinstance(child, ast.Call) and _expr_chain(child.func)[-1:] == (call_name,)
    ]
    if not matching_lines:
        return None
    return min(matching_lines)


__all__ = [
    "BACKEND_ROOT",
    "ROOT",
    "SCANNED_SOURCE_FOLDERS",
    "SimpleNamespace",
    "SchedulingProfile",
    "_LEASE_ONLY_FIELDS",
    "_OWNER_MODULES_BY_FIELD",
    "_assignment_pairs",
    "_call_line",
    "_dict_literal_string_keys",
    "_expr_chain",
    "_function_def",
    "_python_sources",
    "_rel",
    "_runner_text",
    "assert_budgeted_payload",
    "ast",
    "auth_boundary_violations",
    "auth_tenant_boundary_violations",
    "backend_domain_import_fence_violations",
    "capability_keys",
    "control_plane_persona_keys",
    "cookie_boundary_violations",
    "development_cleanroom_violations",
    "export_aggregate_owner_registry",
    "export_architecture_governance_rules",
    "export_architecture_governance_snapshot",
    "export_auth_boundary_contract",
    "export_backend_domain_import_fence",
    "export_development_cleanroom_contract",
    "export_event_channel_contract",
    "export_extension_budget_contract",
    "export_fault_isolation_contract",
    "export_lease_service_contract",
    "export_runtime_contract_taxonomy",
    "export_runtime_policy_contract",
    "export_runtime_state_contract",
    "export_status_compatibility_rules",
    "export_surface_registry",
    "pytest",
    "tenant_claim_violations",
    "tenant_realtime_subject",
    "tenant_subject_token",
    "unique_owner_service_map",
    "validate_extension_manifest_contract",
    "validate_scheduling_profile_budget",
]
