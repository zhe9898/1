from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import yaml

from backend.kernel.governance.domain_blueprint import export_backend_domain_blueprint
from backend.kernel.governance.domain_import_fence import export_backend_domain_import_fence

AUDITS_DIR = REPO_ROOT / "docs" / "audits"

MODULE_CATALOG_PATH = Path("docs/audits/module-catalog.yaml")
FINDINGS_LEDGER_PATH = Path("docs/audits/findings-ledger.yaml")
AUDITS_README_PATH = Path("docs/audits/README.md")
DOMAIN_BLUEPRINT_DOC_PATH = Path("docs/audits/backend-domain-decomposition-2026-04-08.md")

CANONICAL_BLUEPRINT_PATH = "backend/kernel/governance/domain_blueprint.py"
CANONICAL_IMPORT_FENCE_PATH = "backend/kernel/governance/domain_import_fence.py"
CANONICAL_IMPORT_FENCE_TOOL_PATH = "tools/backend_domain_fence.py"
CANONICAL_REPO_GATE_PATH = "tests/test_repo_hardening.py"

ALLOWED_FINDING_STATES = ("open", "fixed", "verified")


def _repo_path(relative: str | Path, *, repo_root: Path) -> Path:
    return repo_root / Path(relative)


def _load_yaml(relative: str | Path, *, repo_root: Path) -> dict[str, object]:
    path = _repo_path(relative, repo_root=repo_root)
    with path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    if not isinstance(loaded, dict):
        raise ValueError(f"{path.as_posix()} must load as a mapping")
    return loaded


def _load_text(relative: str | Path, *, repo_root: Path) -> str:
    return _repo_path(relative, repo_root=repo_root).read_text(encoding="utf-8")


def _validate_module_catalog(*, repo_root: Path) -> list[str]:
    catalog = _load_yaml(MODULE_CATALOG_PATH, repo_root=repo_root)
    blueprint = export_backend_domain_blueprint()
    import_fence = export_backend_domain_import_fence()

    violations: list[str] = []
    official_domains = catalog.get("official_backend_domains")
    expected_domains = [str(domain["key"]) for domain in blueprint["domains"]]
    fence_domains = list(import_fence["governed_domains"])
    if official_domains != expected_domains:
        violations.append(
            "docs/audits/module-catalog.yaml:official_backend_domains must match export_backend_domain_blueprint()"
        )
    if official_domains != fence_domains:
        violations.append(
            "docs/audits/module-catalog.yaml:official_backend_domains must match export_backend_domain_import_fence()"
        )

    code_backed_truth = catalog.get("code_backed_truth")
    if not isinstance(code_backed_truth, dict):
        violations.append("docs/audits/module-catalog.yaml:code_backed_truth must be a mapping")
    else:
        expected_truth = {
            "domain_blueprint": CANONICAL_BLUEPRINT_PATH,
            "import_fence": CANONICAL_IMPORT_FENCE_PATH,
            "repo_gate": CANONICAL_REPO_GATE_PATH,
        }
        for key, expected in expected_truth.items():
            actual = code_backed_truth.get(key)
            if actual != expected:
                violations.append(f"docs/audits/module-catalog.yaml:code_backed_truth.{key} must equal {expected}")
            elif not _repo_path(str(actual), repo_root=repo_root).exists():
                violations.append(f"docs/audits/module-catalog.yaml:referenced path missing: {actual}")

    highest_priority_pending = catalog.get("highest_priority_pending_modules")
    modules = catalog.get("modules")
    if not isinstance(modules, list):
        violations.append("docs/audits/module-catalog.yaml:modules must be a list")
        return violations

    module_status_by_id: dict[str, str] = {}
    module_records_by_id: dict[str, dict[str, object]] = {}
    for module in modules:
        if not isinstance(module, dict):
            violations.append("docs/audits/module-catalog.yaml:each module entry must be a mapping")
            continue
        module_id = str(module.get("module_id") or "").strip()
        status = str(module.get("status") or "").strip()
        if not module_id:
            violations.append("docs/audits/module-catalog.yaml:module entry missing module_id")
            continue
        if module_id in module_status_by_id:
            violations.append(f"docs/audits/module-catalog.yaml:duplicate module_id {module_id}")
            continue
        module_status_by_id[module_id] = status
        module_records_by_id[module_id] = module

    if not isinstance(highest_priority_pending, list):
        violations.append("docs/audits/module-catalog.yaml:highest_priority_pending_modules must be a list")
    else:
        for module_id in highest_priority_pending:
            normalized = str(module_id).strip()
            if normalized not in module_status_by_id:
                violations.append(
                    f"docs/audits/module-catalog.yaml:highest_priority_pending_modules references unknown module {normalized}"
                )
                continue
            if module_status_by_id[normalized] != "pending":
                violations.append(
                    f"docs/audits/module-catalog.yaml:{normalized} is listed as highest-priority pending but status is {module_status_by_id[normalized]}"
                )

    for module_id, module in module_records_by_id.items():
        included_paths = module.get("included_paths")
        if not isinstance(included_paths, list) or not included_paths:
            violations.append(f"docs/audits/module-catalog.yaml:{module_id} must define a non-empty included_paths list")
        else:
            for raw_path in included_paths:
                path_value = str(raw_path).strip()
                if not path_value:
                    violations.append(f"docs/audits/module-catalog.yaml:{module_id} contains an empty included_paths entry")
                    continue
                if any(token in path_value for token in "*?[]"):
                    if not any(repo_root.glob(path_value)):
                        violations.append(
                            f"docs/audits/module-catalog.yaml:{module_id} glob does not match any files: {path_value}"
                        )
                elif not _repo_path(path_value, repo_root=repo_root).exists():
                    violations.append(
                        f"docs/audits/module-catalog.yaml:{module_id} references missing path: {path_value}"
                    )

        depends_on = module.get("depends_on", [])
        if depends_on is None:
            depends_on = []
        if not isinstance(depends_on, list):
            violations.append(f"docs/audits/module-catalog.yaml:{module_id} depends_on must be a list")
        else:
            for dependency in depends_on:
                dependency_id = str(dependency).strip()
                if dependency_id not in module_status_by_id:
                    violations.append(
                        f"docs/audits/module-catalog.yaml:{module_id} depends_on references unknown module {dependency_id}"
                    )
    return violations


def _validate_findings_ledger(*, repo_root: Path) -> list[str]:
    ledger = _load_yaml(FINDINGS_LEDGER_PATH, repo_root=repo_root)
    violations: list[str] = []

    status_model = ledger.get("status_model")
    if not isinstance(status_model, dict):
        violations.append("docs/audits/findings-ledger.yaml:status_model must be a mapping")
    else:
        if status_model.get("states") != list(ALLOWED_FINDING_STATES):
            violations.append(
                "docs/audits/findings-ledger.yaml:status_model.states must equal [open, fixed, verified]"
            )
        if status_model.get("truth_order") != ["implementation", "tests", "audit_docs"]:
            violations.append(
                "docs/audits/findings-ledger.yaml:status_model.truth_order must equal [implementation, tests, audit_docs]"
            )

    findings = ledger.get("findings")
    if not isinstance(findings, list):
        violations.append("docs/audits/findings-ledger.yaml:findings must be a list")
        return violations

    seen_ids: set[str] = set()
    for finding in findings:
        if not isinstance(finding, dict):
            violations.append("docs/audits/findings-ledger.yaml:each finding must be a mapping")
            continue
        finding_id = str(finding.get("finding_id") or "").strip()
        if not finding_id:
            violations.append("docs/audits/findings-ledger.yaml:finding missing finding_id")
            continue
        if finding_id in seen_ids:
            violations.append(f"docs/audits/findings-ledger.yaml:duplicate finding_id {finding_id}")
            continue
        seen_ids.add(finding_id)

        status = str(finding.get("status") or "").strip()
        if status not in ALLOWED_FINDING_STATES:
            violations.append(f"docs/audits/findings-ledger.yaml:{finding_id} has invalid status {status}")

        path_value = str(finding.get("path") or "").strip()
        if not path_value:
            violations.append(f"docs/audits/findings-ledger.yaml:{finding_id} missing path")
        elif not _repo_path(path_value, repo_root=repo_root).exists():
            violations.append(f"docs/audits/findings-ledger.yaml:{finding_id} references missing path {path_value}")

        if not str(finding.get("opened_at") or "").strip():
            violations.append(f"docs/audits/findings-ledger.yaml:{finding_id} missing opened_at")

        if status == "fixed":
            if not str(finding.get("fixed_at") or "").strip():
                violations.append(f"docs/audits/findings-ledger.yaml:{finding_id} fixed finding missing fixed_at")
            if not isinstance(finding.get("verified_by"), list) or not finding.get("verified_by"):
                violations.append(f"docs/audits/findings-ledger.yaml:{finding_id} fixed finding missing verified_by")
            if not str(finding.get("resolution_note") or "").strip():
                violations.append(f"docs/audits/findings-ledger.yaml:{finding_id} fixed finding missing resolution_note")

        if status == "verified":
            if not str(finding.get("verified_at") or "").strip():
                violations.append(f"docs/audits/findings-ledger.yaml:{finding_id} verified finding missing verified_at")
            if not isinstance(finding.get("verified_by"), list) or not finding.get("verified_by"):
                violations.append(f"docs/audits/findings-ledger.yaml:{finding_id} verified finding missing verified_by")
            if not str(finding.get("resolution_note") or "").strip():
                violations.append(f"docs/audits/findings-ledger.yaml:{finding_id} verified finding missing resolution_note")
    return violations


def _validate_audit_docs(*, repo_root: Path) -> list[str]:
    violations: list[str] = []
    readme = _load_text(AUDITS_README_PATH, repo_root=repo_root)
    blueprint_doc = _load_text(DOMAIN_BLUEPRINT_DOC_PATH, repo_root=repo_root)

    required_readme_refs = (
        CANONICAL_BLUEPRINT_PATH,
        CANONICAL_IMPORT_FENCE_PATH,
        CANONICAL_IMPORT_FENCE_TOOL_PATH,
        CANONICAL_REPO_GATE_PATH,
    )
    for ref in required_readme_refs:
        if ref not in readme:
            violations.append(f"docs/audits/README.md must reference {ref}")

    required_blueprint_refs = (
        CANONICAL_BLUEPRINT_PATH,
        CANONICAL_IMPORT_FENCE_PATH,
        CANONICAL_IMPORT_FENCE_TOOL_PATH,
        CANONICAL_REPO_GATE_PATH,
        "backend/control_plane/console/manifest_service.py",
    )
    for ref in required_blueprint_refs:
        if ref not in blueprint_doc:
            violations.append(f"docs/audits/backend-domain-decomposition-2026-04-08.md must reference {ref}")

    required_blueprint_tokens = (
        "code-backed-target-architecture",
        "control_plane",
        "runtime",
        "extensions",
        "platform",
        "adapters/",
        "execution/",
    )
    for token in required_blueprint_tokens:
        if token not in blueprint_doc:
            violations.append(f"docs/audits/backend-domain-decomposition-2026-04-08.md missing token {token}")
    return violations


def audit_drift_violations(*, repo_root: Path | None = None) -> list[str]:
    resolved_root = (repo_root or REPO_ROOT).resolve()
    violations: list[str] = []
    violations.extend(_validate_module_catalog(repo_root=resolved_root))
    violations.extend(_validate_findings_ledger(repo_root=resolved_root))
    violations.extend(_validate_audit_docs(repo_root=resolved_root))
    return violations


def main() -> int:
    violations = audit_drift_violations()
    if not violations:
        return 0
    print("audit drift violations detected:")
    for violation in violations:
        print(violation)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
