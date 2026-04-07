from __future__ import annotations

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
UPDATE_PATH = REPO_ROOT / "scripts" / "update.py"


def _load_update_module():
    spec = importlib.util.spec_from_file_location("zen70_update_helpers", UPDATE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_changed_files_normalizes_relative_paths() -> None:
    update_mod = _load_update_module()

    changed = update_mod._parse_changed_files("backend\\requirements.txt\n\nbackend/Dockerfile\n")

    assert changed == {"backend/requirements.txt", "backend/Dockerfile"}


def test_requirements_changed_from_diff_only_triggers_for_backend_requirements() -> None:
    update_mod = _load_update_module()

    assert update_mod._requirements_changed_from_diff("backend/requirements.txt\n")
    assert not update_mod._requirements_changed_from_diff("frontend/package.json\nbackend/Dockerfile\n")


def test_collect_dirty_files_trims_git_status_output() -> None:
    update_mod = _load_update_module()

    dirty = update_mod._collect_dirty_files(" M backend/api/auth.py\n?? tests/test_update_helpers.py\n")

    assert dirty == ["M backend/api/auth.py", "?? tests/test_update_helpers.py"]


def test_needs_image_rebuild_from_hashes_only_when_image_inputs_change() -> None:
    update_mod = _load_update_module()

    assert not update_mod._needs_image_rebuild_from_hashes("a", "b", "a", "b")
    assert update_mod._needs_image_rebuild_from_hashes("a", "b", "c", "b")
    assert update_mod._needs_image_rebuild_from_hashes("a", "b", "a", "c")


def test_step_check_deps_changed_uses_normalized_diff_output(monkeypatch) -> None:
    update_mod = _load_update_module()

    def fake_run(args, **kwargs):  # noqa: ANN001
        assert args[:3] == ["git", "diff", "HEAD~1"]
        return 0, "backend\\requirements.txt\n"

    monkeypatch.setattr(update_mod, "_run", fake_run)

    assert update_mod.step_check_deps_changed(force=False) is True


def test_detect_pull_branch_prefers_env(monkeypatch) -> None:
    update_mod = _load_update_module()
    monkeypatch.setenv("ZEN70_UPDATE_BRANCH", "release/3.4")

    assert update_mod._detect_pull_branch() == "release/3.4"


def test_detect_pull_branch_uses_upstream_when_available(monkeypatch) -> None:
    update_mod = _load_update_module()
    monkeypatch.delenv("ZEN70_UPDATE_BRANCH", raising=False)

    def fake_run(args, **kwargs):  # noqa: ANN001
        if args[:3] == ["git", "rev-parse", "--abbrev-ref"]:
            return 0, "origin/master"
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(update_mod, "_run", fake_run)

    assert update_mod._detect_pull_branch() == "master"


def test_detect_pull_branch_falls_back_to_current_branch(monkeypatch) -> None:
    update_mod = _load_update_module()
    monkeypatch.delenv("ZEN70_UPDATE_BRANCH", raising=False)

    def fake_run(args, **kwargs):  # noqa: ANN001
        if args[:3] == ["git", "rev-parse", "--abbrev-ref"]:
            return 1, "fatal"
        if args[:3] == ["git", "branch", "--show-current"]:
            return 0, "master"
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(update_mod, "_run", fake_run)

    assert update_mod._detect_pull_branch() == "master"


def test_step_git_pull_uses_detected_branch(monkeypatch) -> None:
    update_mod = _load_update_module()
    monkeypatch.setattr(update_mod, "_detect_pull_branch", lambda: "master")

    calls: list[list[str]] = []

    def fake_run(args, **kwargs):  # noqa: ANN001
        calls.append(args)
        if args == ["git", "rev-parse", "HEAD"]:
            return 0, "deadbeefcafebabe"
        if args == ["git", "pull", "--rebase", "origin", "master"]:
            return 0, ""
        if args == ["git", "rev-parse", "--short", "HEAD"]:
            return 0, "cafebabe"
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(update_mod, "_run", fake_run)

    previous_head = update_mod.step_git_pull(dry_run=False)

    assert previous_head == "deadbeefcafebabe"
    assert ["git", "pull", "--rebase", "origin", "master"] in calls
