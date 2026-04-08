from __future__ import annotations

import re
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
IMAGE_LINE_PATTERN = re.compile(r"^\s*image:\s*(?P<ref>\S+)\s*$", re.MULTILINE)
DOCKERFILE_FROM_PATTERN = re.compile(r"^\s*FROM\s+(?P<ref>\S+)", re.MULTILINE)
LOCAL_BUILD_IMAGES = ("zen70-gateway", "zen70-runner-agent")


def test_runtime_secret_artifacts_are_ignored_untracked_and_absent() -> None:
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    for required in ("runtime/tmp-compile/", "runtime/secrets/", "config/users.acl"):
        assert required in gitignore, f"{required} must be ignored in .gitignore"

    tracked = subprocess.run(
        ["git", "ls-files", "--", "runtime/tmp-compile", "runtime/secrets", "config/users.acl"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    tracked_paths = [line.strip() for line in tracked.stdout.splitlines() if line.strip()]
    assert not tracked_paths, f"runtime secrets/tmp artifacts must never be tracked: {tracked_paths}"

    leaked_files: list[str] = []
    for path in (REPO_ROOT / "runtime" / "secrets", REPO_ROOT / "runtime" / "tmp-compile"):
        if path.exists():
            leaked_files.extend(item.relative_to(REPO_ROOT).as_posix() for item in path.rglob("*") if item.is_file())
    if (REPO_ROOT / "config" / "users.acl").exists():
        leaked_files.append("config/users.acl")
    assert not leaked_files, f"runtime secret artifacts must not remain in the workspace: {leaked_files}"


def test_workflows_use_immutable_runner_and_action_refs() -> None:
    workflow_files = sorted(WORKFLOW_DIR.glob("*.yml"))
    assert workflow_files, "expected at least one workflow file under .github/workflows"

    floating_runners: list[str] = []
    floating_refs: list[str] = []
    floating_latest: list[str] = []
    mutable_publish_tags: list[str] = []

    for workflow in workflow_files:
        text = workflow.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("runs-on:") and "ubuntu-latest" in stripped:
                floating_runners.append(f"{workflow.name}:{line_number}:{stripped}")
            if stripped.startswith("uses:") or stripped.startswith("- uses:"):
                ref = stripped.split("@", 1)[1] if "@" in stripped else ""
                if ref and not re.fullmatch(r"[0-9a-f]{40}", ref):
                    floating_refs.append(f"{workflow.name}:{line_number}:{stripped}")
            if ":latest" in stripped:
                floating_latest.append(f"{workflow.name}:{line_number}:{stripped}")
            if workflow.name == "docker-publish.yml" and stripped in {
                "type=schedule",
                "type=ref,event=branch",
                "type=ref,event=pr",
                "type=semver,pattern={{major}}.{{minor}}",
                "type=semver,pattern={{major}}",
            }:
                mutable_publish_tags.append(f"{workflow.name}:{line_number}:{stripped}")

    assert not floating_runners, f"workflow runners must be pinned, found: {floating_runners}"
    assert not floating_refs, f"workflow actions must be pinned to commit SHA, found: {floating_refs}"
    assert not floating_latest, f"workflow files must not use latest tags: {floating_latest}"
    assert not mutable_publish_tags, f"docker-publish must not emit mutable branch/pr/schedule tags: {mutable_publish_tags}"


def test_python_ci_workflows_use_hashed_lockfile() -> None:
    violations: list[str] = []
    lockfile = REPO_ROOT / "backend" / "requirements-ci.lock"
    assert lockfile.exists(), "backend/requirements-ci.lock must exist"
    assert "--hash=" in lockfile.read_text(encoding="utf-8"), "requirements-ci.lock must contain pip hashes"

    for workflow_name in ("ci.yml", "compliance.yml"):
        text = (WORKFLOW_DIR / workflow_name).read_text(encoding="utf-8")
        if "requirements-ci.lock" not in text:
            violations.append(f"{workflow_name}:missing requirements-ci.lock")
        if "--require-hashes" not in text:
            violations.append(f"{workflow_name}:missing --require-hashes")
        if "pip install --upgrade pip" in text or "python -m pip install --upgrade pip" in text:
            violations.append(f"{workflow_name}:floating pip bootstrap")

    assert not violations, f"Python CI workflows must install from the hashed lockfile: {violations}"


def test_offline_release_workflow_uses_immutable_release_tags_and_clean_bundle_inputs() -> None:
    text = (WORKFLOW_DIR / "build_offline_v2_9.yml").read_text(encoding="utf-8")
    assert "RELEASE_SERIES:" in text, "offline bundle workflow must distinguish release series from frozen release tags"
    assert 'echo "RELEASE_TAG=' in text, "offline bundle workflow must derive an immutable release tag from the commit"
    assert "RELEASE_TAG: v2.9.1" not in text, "offline bundle workflow must not hard-code a reusable release tag"
    assert 'has_asset "${ASSET_NAME}.sha256"' in text, "offline bundle upload skipping logic must verify checksum assets"
    assert "python scripts/validate_offline_bundle.py \"$BUNDLE_ROOT\"" in text, "offline bundle workflow must validate the bundle before packaging"

    for pattern in (
        "config/system.yaml",
        "frontend/build_*.txt",
        "frontend/eslint_*.txt",
        "frontend/vuetsc_*.txt",
        "frontend/full_build_*.txt",
        "frontend/test_output.txt",
        "frontend/test_result*.json",
    ):
        assert pattern in text, f"offline bundle workflow must exclude frontend audit residue: {pattern}"


def test_offline_release_workflow_compiles_iac_env_before_resolving_images() -> None:
    text = (WORKFLOW_DIR / "build_offline_v2_9.yml").read_text(encoding="utf-8")
    assert "Compile deterministic IaC runtime inputs" in text
    assert "python -m pip install --disable-pip-version-check -r requirements-infra.txt" in text
    assert "python scripts/compiler.py system.yaml -o ." in text
    assert "ZEN70_SECRET_STATE_DIR: ${{ runner.temp }}/zen70-secrets" in text
    assert "docker compose --env-file .env config --images" in text
    assert "if [ ! -s offline_bundle/compose-images.txt ]; then" in text
    assert "render-manifest.json" in text
    assert "docs/openapi-kernel.json" in text
    assert "contracts/openapi/zen70-gateway-kernel.openapi.json" in text


def test_repo_has_single_runtime_config_entrypoint() -> None:
    assert not (REPO_ROOT / "config" / "system.yaml").exists(), "legacy config/system.yaml must not exist in the repo root surface"
    assert (REPO_ROOT / "scripts" / "compiler.py").exists(), "scripts/compiler.py must remain the canonical compiler entrypoint"
    assert not (REPO_ROOT / "deploy" / "config-compiler.py").exists(), "compatibility compiler wrapper must not exist in development"
    assert not (REPO_ROOT / "deploy" / "bootstrap.py").exists(), "compatibility bootstrap wrapper must not exist in development"


def test_ci_trivy_scan_uses_pinned_setup_action_and_direct_cli() -> None:
    text = (WORKFLOW_DIR / "ci.yml").read_text(encoding="utf-8")
    assert "aquasecurity/setup-trivy@3fb12ec12f41e471780db15c232d5dd185dcb514" in text
    assert "trivy image \\" in text
    assert "aquasecurity/trivy-action@" not in text


def test_external_image_references_are_digest_pinned() -> None:
    violations: list[str] = []

    system_yaml = (REPO_ROOT / "system.yaml").read_text(encoding="utf-8")
    for match in IMAGE_LINE_PATTERN.finditer(system_yaml):
        image_ref = match.group("ref")
        if any(local in image_ref for local in LOCAL_BUILD_IMAGES):
            continue
        if "@sha256:" not in image_ref:
            violations.append(f"system.yaml:{image_ref}")

    test_compose = (REPO_ROOT / "tests" / "docker-compose.yml").read_text(encoding="utf-8")
    for match in IMAGE_LINE_PATTERN.finditer(test_compose):
        image_ref = match.group("ref")
        if "@sha256:" not in image_ref:
            violations.append(f"tests/docker-compose.yml:{image_ref}")

    assert not violations, f"all external image references must be digest pinned: {violations}"


def test_deploy_images_list_is_digest_pinned() -> None:
    images_list = REPO_ROOT / "deploy" / "images.list"
    assert images_list.exists(), "deploy/images.list must exist"

    violations: list[str] = []
    for line_number, raw_line in enumerate(images_list.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "@sha256:" not in line:
            violations.append(f"{line_number}:{line}")

    assert not violations, f"deploy/images.list entries must be digest pinned: {violations}"


def test_dockerfiles_use_digest_pinned_base_images() -> None:
    violations: list[str] = []
    for relative in ("backend/Dockerfile", "runner-agent/Dockerfile"):
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        stage_aliases = {
            match.group(1)
            for match in re.finditer(r"^\s*FROM\s+\S+\s+AS\s+([A-Za-z0-9._-]+)\s*$", text, re.MULTILINE)
        }
        for match in DOCKERFILE_FROM_PATTERN.finditer(text):
            image_ref = match.group("ref")
            if image_ref in stage_aliases:
                continue
            if image_ref.startswith("${"):
                violations.append(f"{relative}:{image_ref}")
                continue
            if "@sha256:" not in image_ref:
                violations.append(f"{relative}:{image_ref}")

    assert not violations, f"Dockerfile base images must be digest pinned: {violations}"


def test_frontend_audit_artifacts_do_not_exist_in_workspace() -> None:
    leftovers: list[str] = []
    frontend_dir = REPO_ROOT / "frontend"
    for pattern in ("build_*.txt", "eslint_*.txt", "vuetsc_*.txt", "full_build_*.txt", "test_output.txt"):
        leftovers.extend(path.relative_to(REPO_ROOT).as_posix() for path in frontend_dir.glob(pattern))

    assert not leftovers, f"frontend build or audit residue must not remain in the workspace: {leftovers}"


def test_health_pack_no_longer_uses_placeholder_artifacts() -> None:
    violations: list[str] = []
    for relative in (
        "clients/health-ios/placeholder.yaml",
        "clients/health-android/placeholder.yaml",
    ):
        if (REPO_ROOT / relative).exists():
            violations.append(relative)

    assert not violations, f"Health Pack must not fall back to placeholder artifacts: {violations}"


_BACKEND_SOURCE_MAX_LINES = 600
_BACKEND_TEST_MAX_LINES = 800

_BACKEND_SOURCE_ALLOWLIST: dict[str, str] = {
    "sentinel/topology_sentinel.py": "The topology sentinel remains a large state machine until it is decomposed safely.",
    "core/redis_client.py": "redis_client is still a shared platform helper and remains allowlisted for now.",
    "api/jobs/dispatch.py": "jobs dispatch still carries the main scheduling path and cannot be split blindly.",
    "kernel/scheduling/placement_solver.py": "Global placement solver remains intentionally centralized until further decomposition",
    "kernel/scheduling/backfill_scheduling.py": "Backfill scheduling still centralizes reservation time-window coordination.",
    "api/jobs/lifecycle.py": "job lifecycle still shares transaction and audit context that has not been safely split yet.",
    "kernel/extensions/extension_sdk.py": "extension SDK still shares bootstrap, registration, and manifest parsing context.",
}

_BACKEND_TEST_ALLOWLIST: dict[str, str] = {
    "tests/unit/test_scheduling_governance.py": "Scheduling governance intentionally keeps broad scenario coverage in one test file for now.",
    "tests/unit/test_scheduler_auto_tune.py": "scheduler auto-tune integration coverage is intentionally kept together for now.",
}


def _count_lines(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def test_backend_source_files_do_not_exceed_line_limit() -> None:
    backend = REPO_ROOT / "backend"
    violations: list[str] = []
    for py in sorted(backend.rglob("*.py")):
        rel = py.relative_to(backend).as_posix()
        if any(part in rel for part in ("__pycache__", "alembic/", "tests/")):
            continue
        lines = _count_lines(py)
        if lines > _BACKEND_SOURCE_MAX_LINES and rel not in _BACKEND_SOURCE_ALLOWLIST:
            violations.append(f"{rel} ({lines} lines, limit {_BACKEND_SOURCE_MAX_LINES})")

    assert not violations, (
        f"backend source files exceeded the {_BACKEND_SOURCE_MAX_LINES}-line limit; "
        f"split them or document them in the allowlist.\n" + "\n".join(violations)
    )


def test_backend_test_files_do_not_exceed_line_limit() -> None:
    backend = REPO_ROOT / "backend"
    violations: list[str] = []
    for py in sorted(backend.rglob("*.py")):
        rel = py.relative_to(backend).as_posix()
        if "tests/" not in rel or "__pycache__" in rel:
            continue
        lines = _count_lines(py)
        if lines > _BACKEND_TEST_MAX_LINES and rel not in _BACKEND_TEST_ALLOWLIST:
            violations.append(f"{rel} ({lines} lines, limit {_BACKEND_TEST_MAX_LINES})")

    assert not violations, (
        f"backend test files exceeded the {_BACKEND_TEST_MAX_LINES}-line limit; "
        f"split them or document them in the allowlist.\n" + "\n".join(violations)
    )


def test_backend_source_allowlist_entries_are_still_needed() -> None:
    backend = REPO_ROOT / "backend"
    stale: list[str] = []
    for rel in _BACKEND_SOURCE_ALLOWLIST:
        path = backend / rel
        if not path.exists():
            stale.append(f"{rel} (file no longer exists)")
            continue
        lines = _count_lines(path)
        if lines <= _BACKEND_SOURCE_MAX_LINES:
            stale.append(f"{rel} ({lines} lines, now below {_BACKEND_SOURCE_MAX_LINES})")

    assert not stale, "source allowlist contains stale entries:\n" + "\n".join(stale)
