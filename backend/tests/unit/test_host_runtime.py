from __future__ import annotations

from pathlib import Path

import pytest

from backend.tests.unit._repo_paths import repo_path
from scripts.iac_core.host_runtime import (
    HostRuntimePaths,
    build_host_service_spec,
    quote_systemd_arg,
    validate_host_service_config,
)


def test_quote_systemd_arg_escapes_whitespace_and_percent() -> None:
    assert quote_systemd_arg("alpha beta") == '"alpha beta"'
    assert quote_systemd_arg("%i") == "%%i"


def test_validate_host_service_config_requires_structured_entrypoint() -> None:
    errors = validate_host_service_config("gateway", {"runtime": "host"})
    assert errors == ["services.gateway: runtime=host requires entrypoint"]


def test_validate_host_service_config_requires_python_module_target() -> None:
    errors = validate_host_service_config("gateway", {"runtime": "host", "entrypoint": {"type": "python-module"}})
    assert errors == ["services.gateway.entrypoint.module is required for python-module"]


def test_build_host_service_spec_renders_runner_agent_binary_entrypoint(tmp_path: Path) -> None:
    paths = HostRuntimePaths(project_root=repo_path(), output_root=tmp_path)
    service = {
        "runtime": "host",
        "working_dir": ".",
        "environment": {"RUNNER_NODE_NAME": "zen70-go-runner"},
        "entrypoint": {
            "type": "binary",
            "path": "runtime/host/bin/runner-agent",
            "build": {
                "type": "go-binary",
                "source_dir": "runner-agent",
                "package": "./cmd/runner-agent",
                "output": "runtime/host/bin/runner-agent",
                "env": {"CGO_ENABLED": "0"},
            },
        },
    }

    spec = build_host_service_spec("runner-agent", service, paths=paths)

    exec_start_normalized = spec.exec_start.replace("\\\\", "/").replace("\\", "/")
    assert "runtime/host/bin/runner-agent" in exec_start_normalized
    assert spec.environment_file == str((tmp_path / ".env").resolve())
    assert spec.build_plan is not None
    assert spec.build_plan.kind == "go_binary"
    assert spec.build_plan.source_dir == Path(repo_path("runner-agent")).resolve()


def test_build_host_service_spec_rejects_legacy_exec_shape(tmp_path: Path) -> None:
    paths = HostRuntimePaths(project_root=repo_path(), output_root=tmp_path)

    with pytest.raises(ValueError, match="structured entrypoint mapping"):
        build_host_service_spec("gateway", {"runtime": "host", "exec": "/usr/bin/python3"}, paths=paths)
