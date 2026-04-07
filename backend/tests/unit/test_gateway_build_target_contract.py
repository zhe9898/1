from __future__ import annotations

from backend.tests.unit._repo_paths import repo_path


def test_dockerfile_uses_gateway_kernel_as_default_target() -> None:
    dockerfile = repo_path("backend", "Dockerfile").read_text(encoding="utf-8")
    assert "AS gateway-kernel" in dockerfile
    assert "FROM gateway-kernel AS gateway-default" in dockerfile
