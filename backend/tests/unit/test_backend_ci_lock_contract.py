from __future__ import annotations

from backend.tests.unit._repo_paths import repo_path

_WINDOWS_ONLY_REQUIREMENTS: dict[str, str] = {
    "pywin32": 'sys_platform == "win32"',
}


def _requirement_line(text: str, requirement: str) -> str | None:
    prefix = f"{requirement}=="
    for line in text.splitlines():
        if line.startswith(prefix):
            return line
    return None


def test_backend_ci_source_declares_windows_only_dependencies_explicitly() -> None:
    source = repo_path("backend", "requirements-ci.in").read_text(encoding="utf-8")

    for requirement, marker in _WINDOWS_ONLY_REQUIREMENTS.items():
        line = _requirement_line(source, requirement)
        assert line is not None
        assert marker in line


def test_backend_ci_lockfile_guards_windows_only_dependencies_with_markers() -> None:
    lock_text = repo_path("backend", "requirements-ci.lock").read_text(encoding="utf-8")

    for requirement, marker in _WINDOWS_ONLY_REQUIREMENTS.items():
        line = _requirement_line(lock_text, requirement)
        if line is not None:
            assert marker in line
