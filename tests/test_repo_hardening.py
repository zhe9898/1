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
        assert required in gitignore, f"{required} 必须被 .gitignore 严格忽略"

    tracked = subprocess.run(
        ["git", "ls-files", "--", "runtime/tmp-compile", "runtime/secrets", "config/users.acl"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    tracked_paths = [line.strip() for line in tracked.stdout.splitlines() if line.strip()]
    assert not tracked_paths, f"禁止跟踪运行态 secrets/tmp 产物: {tracked_paths}"

    leaked_files: list[str] = []
    for path in (REPO_ROOT / "runtime" / "secrets", REPO_ROOT / "runtime" / "tmp-compile"):
        if path.exists():
            leaked_files.extend(item.relative_to(REPO_ROOT).as_posix() for item in path.rglob("*") if item.is_file())
    if (REPO_ROOT / "config" / "users.acl").exists():
        leaked_files.append("config/users.acl")
    assert not leaked_files, f"工作树中不得残留运行态明文 secrets 产物: {leaked_files}"


def test_workflows_use_immutable_runner_and_action_refs() -> None:
    workflow_files = sorted(WORKFLOW_DIR.glob("*.yml"))
    assert workflow_files, "未发现 .github/workflows/*.yml"

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

    assert not floating_runners, f"workflow runner 必须固定镜像: {floating_runners}"
    assert not floating_refs, f"workflow actions 必须固定 commit SHA: {floating_refs}"
    assert not floating_latest, f"workflow 中禁止 latest: {floating_latest}"
    assert not mutable_publish_tags, f"docker-publish 不得生成 branch/pr/schedule 可变标签: {mutable_publish_tags}"


def test_python_ci_workflows_use_hashed_lockfile() -> None:
    violations: list[str] = []
    lockfile = REPO_ROOT / "backend" / "requirements-ci.lock"
    assert lockfile.exists(), "backend/requirements-ci.lock 必须存在"
    assert "--hash=" in lockfile.read_text(encoding="utf-8"), "requirements-ci.lock 必须包含 pip hashes"
    for workflow_name in ("ci.yml", "compliance.yml"):
        text = (WORKFLOW_DIR / workflow_name).read_text(encoding="utf-8")
        if "requirements-ci.lock" not in text:
            violations.append(f"{workflow_name}:missing requirements-ci.lock")
        if "--require-hashes" not in text:
            violations.append(f"{workflow_name}:missing --require-hashes")
        if "pip install --upgrade pip" in text or "python -m pip install --upgrade pip" in text:
            violations.append(f"{workflow_name}:floating pip bootstrap")
    assert not violations, f"Python CI workflows 必须使用带 hash 的锁文件安装: {violations}"


def test_offline_release_workflow_uses_immutable_release_tags_and_clean_bundle_inputs() -> None:
    text = (WORKFLOW_DIR / "build_offline_v2_9.yml").read_text(encoding="utf-8")
    assert "RELEASE_SERIES:" in text, "离线包 workflow 必须区分 release 系列与冻结发行 tag"
    assert 'echo "RELEASE_TAG=' in text, "离线包 workflow 必须按 commit 生成不可变 release tag"
    assert "RELEASE_TAG: v2.9.1" not in text, "离线包 workflow 不得复用固定 release tag 承载持续构建"
    assert 'has_asset "${ASSET_NAME}.sha256"' in text, "离线包上传跳过逻辑必须同时校验 checksum 资产"
    assert "python scripts/validate_offline_bundle.py \"$BUNDLE_ROOT\"" in text, "离线包必须在打包前执行白名单校验"
    for pattern in (
        "config/system.yaml",
        "frontend/build_*.txt",
        "frontend/eslint_*.txt",
        "frontend/vuetsc_*.txt",
        "frontend/full_build_*.txt",
        "frontend/test_output.txt",
        "frontend/test_result*.json",
    ):
        assert pattern in text, f"离线包必须排除前端临时审计/构建残留: {pattern}"


def test_offline_release_workflow_compiles_iac_env_before_resolving_images() -> None:
    text = (WORKFLOW_DIR / "build_offline_v2_9.yml").read_text(encoding="utf-8")
    assert "Compile deterministic IaC runtime inputs" in text, "Offline bundle workflow must compile IaC inputs before resolving compose images"
    assert "python -m pip install --disable-pip-version-check -r requirements-infra.txt" in text, "Offline bundle workflow must install compiler dependencies"
    assert "python scripts/compiler.py system.yaml -o ." in text, "Offline bundle workflow must generate .env from IaC before compose image resolution"
    assert "ZEN70_SECRET_STATE_DIR: ${{ runner.temp }}/zen70-secrets" in text, "Offline bundle workflow must keep ACL secrets under runner.temp instead of the repository workspace"
    assert "docker compose --env-file .env config --images" in text, "Offline bundle workflow must resolve compose images against the compiled .env"
    assert "if [ ! -s offline_bundle/compose-images.txt ]; then" in text, "Offline bundle workflow must fail fast when compose image resolution returns no images"
    assert "render-manifest.json" in text, "Offline bundle workflow must validate render-manifest consistency before packaging"
    assert "docs/openapi-kernel.json" in text, "Offline bundle workflow must validate kernel OpenAPI consistency before packaging"
    assert "contracts/openapi/zen70-gateway-kernel.openapi.json" in text, "Offline bundle workflow must validate contract OpenAPI consistency before packaging"


def test_repo_has_single_runtime_config_entrypoint() -> None:
    assert not (REPO_ROOT / "config" / "system.yaml").exists(), "legacy config/system.yaml must not exist in the repo root surface"

    wrapper = (REPO_ROOT / "deploy" / "config-compiler.py").read_text(encoding="utf-8")
    assert "scripts/compiler.py" in wrapper, "deploy/config-compiler.py must delegate to the canonical compiler"
    assert "config/system.yaml" not in wrapper, "deploy/config-compiler.py must not advertise a second config entrypoint"
    assert "migrate_and_persist" not in wrapper, "deploy/config-compiler.py must not embed a second compiler implementation"


def test_ci_trivy_scan_uses_pinned_setup_action_and_direct_cli() -> None:
    text = (WORKFLOW_DIR / "ci.yml").read_text(encoding="utf-8")
    assert "aquasecurity/setup-trivy@3fb12ec12f41e471780db15c232d5dd185dcb514" in text, "Trivy installation must use a commit-pinned setup-trivy action"
    assert "trivy image \\" in text, "Trivy scan must call the CLI directly to avoid wrapper action drift"
    assert "aquasecurity/trivy-action@" not in text, "Do not reintroduce trivy-action wrapper after the upstream resolution failure"


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

    assert not violations, f"所有外部镜像引用都必须 digest pin: {violations}"


def test_deploy_images_list_is_digest_pinned() -> None:
    images_list = REPO_ROOT / "deploy" / "images.list"
    assert images_list.exists(), "deploy/images.list 蹇呴』瀛樺湪"

    violations: list[str] = []
    for line_number, raw_line in enumerate(images_list.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "@sha256:" not in line:
            violations.append(f"{line_number}:{line}")

    assert not violations, f"deploy/images.list 蹇呴』鏄?digest pinned: {violations}"


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
    assert not violations, f"Dockerfile 基础镜像必须 digest pin: {violations}"


def test_frontend_audit_artifacts_do_not_exist_in_workspace() -> None:
    leftovers: list[str] = []
    frontend_dir = REPO_ROOT / "frontend"
    for pattern in ("build_*.txt", "eslint_*.txt", "vuetsc_*.txt", "full_build_*.txt", "test_output.txt"):
        leftovers.extend(path.relative_to(REPO_ROOT).as_posix() for path in frontend_dir.glob(pattern))
    assert not leftovers, f"前端构建/审计残留不得留在工作树: {leftovers}"


def test_health_pack_no_longer_uses_placeholder_artifacts() -> None:
    violations: list[str] = []
    for relative in (
        "clients/health-ios/placeholder.yaml",
        "clients/health-android/placeholder.yaml",
    ):
        if (REPO_ROOT / relative).exists():
            violations.append(relative)
    assert not violations, f"Health Pack 已进入最小交付阶段，不得回流 placeholder 产物: {violations}"
