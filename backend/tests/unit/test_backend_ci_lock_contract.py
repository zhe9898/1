from __future__ import annotations

import re

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


def test_backend_proto_runtime_pin_matches_generated_placement_gencode() -> None:
    placement_pb2 = repo_path("backend", "runtime", "scheduling", "gen_grpc", "placement_pb2.py").read_text(encoding="utf-8")
    match = re.search(
        r"ValidateProtobufRuntimeVersion\([^)]*?,\s*(?P<major>\d+),\s*(?P<minor>\d+),\s*(?P<patch>\d+),",
        placement_pb2,
    )
    assert match is not None, "placement_pb2.py must declare its protobuf runtime contract"
    gencode_major = int(match.group("major"))
    gencode_minor = int(match.group("minor"))
    gencode_patch = int(match.group("patch"))

    requirements_core = repo_path("backend", "requirements-core.txt").read_text(encoding="utf-8")
    lock_text = repo_path("backend", "requirements-ci.lock").read_text(encoding="utf-8")
    runtime_match = re.search(r"protobuf==(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)", requirements_core)
    lock_match = re.search(r"protobuf==(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)", lock_text)

    assert runtime_match is not None
    assert lock_match is not None

    runtime_major = int(runtime_match.group("major"))
    runtime_minor = int(runtime_match.group("minor"))
    runtime_patch = int(runtime_match.group("patch"))
    lock_major = int(lock_match.group("major"))
    lock_minor = int(lock_match.group("minor"))
    lock_patch = int(lock_match.group("patch"))

    assert (runtime_major, runtime_minor) == (gencode_major, gencode_minor)
    assert runtime_patch >= gencode_patch
    assert (lock_major, lock_minor, lock_patch) == (runtime_major, runtime_minor, runtime_patch)
