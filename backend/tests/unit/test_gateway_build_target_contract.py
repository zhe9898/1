from __future__ import annotations

from backend.tests.unit._repo_paths import repo_path


def test_dockerfile_uses_gateway_kernel_as_default_target() -> None:
    dockerfile = repo_path("backend", "Dockerfile").read_text(encoding="utf-8")
    assert "AS gateway-kernel" in dockerfile
    assert "FROM gateway-kernel AS gateway-default" in dockerfile


def test_dockerfile_applies_openssl_security_upgrades_in_gateway_base() -> None:
    dockerfile = repo_path("backend", "Dockerfile").read_text(encoding="utf-8")
    assert "apt-get install -y --no-install-recommends --only-upgrade" in dockerfile
    assert "libssl3t64" in dockerfile
    assert "openssl-provider-legacy" in dockerfile
    assert "rm -rf /var/lib/apt/lists/*" in dockerfile
