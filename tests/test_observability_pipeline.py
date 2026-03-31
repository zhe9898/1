import os
import subprocess
import shutil
import sys
import tempfile

import requests
import pytest
from pathlib import Path
import yaml

PROJECT_ROOT = Path(__file__).parent.parent
COMPILER_SCRIPT = PROJECT_ROOT / "scripts" / "compiler.py"
SYSTEM_YAML = PROJECT_ROOT / "system.yaml"
OUTPUT_COMPOSE = PROJECT_ROOT / "docker-compose.yml"

# We need a system.yaml with ops-pack enabled for observability tests.
# The compiler reads packs from system.yaml; we create a temp copy with ops-pack.
# IMPORTANT: We compile to a temp output dir to avoid overwriting the real docker-compose.yml.
_OPS_COMPOSE: dict | None = None


def _compile_with_ops_pack() -> dict:
    """Run compiler with ops-pack enabled and return parsed compose data."""
    global _OPS_COMPOSE
    if _OPS_COMPOSE is not None:
        return _OPS_COMPOSE

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_root = Path(tmpdir)
        # Copy system.yaml and enable ops-pack
        sys_cfg = yaml.safe_load(SYSTEM_YAML.read_text(encoding="utf-8"))
        deployment = sys_cfg.get("deployment", {})
        packs = list(deployment.get("packs") or [])
        if "ops-pack" not in packs:
            packs.append("ops-pack")
        deployment["packs"] = packs
        sys_cfg["deployment"] = deployment

        tmp_system = tmp_root / "system.yaml"
        tmp_system.write_text(yaml.safe_dump(sys_cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")

        # Output to temp dir so we don't pollute the real project files
        result = subprocess.run(
            [sys.executable, str(COMPILER_SCRIPT), str(tmp_system), "-o", str(tmp_root)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, f"Compiler failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"

        compose_path = tmp_root / "docker-compose.yml"
        assert compose_path.exists(), "docker-compose.yml was not generated in temp dir"
        compose_data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))

    _OPS_COMPOSE = compose_data
    return compose_data


def test_compiler_success():
    """Verify compiler can generate docker-compose.yml without errors."""
    result = subprocess.run(
        [sys.executable, str(COMPILER_SCRIPT), "system.yaml", "-o", "."],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"Compiler failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    assert OUTPUT_COMPOSE.exists(), "docker-compose.yml was not generated"

def test_docker_compose_validity():
    """Verify the generated docker-compose.yml is valid according to architecture rules."""
    compose_data = _compile_with_ops_pack()
    
    services = compose_data.get("services", {})
    
    # Check for SRE Observability components
    assert "docker-proxy" in services, "docker-proxy service missing"
    assert "categraf" in services, "categraf service missing"
    assert "loki" in services, "loki service missing"
    assert "promtail" in services, "promtail service missing"
    assert "alertmanager" in services, "alertmanager service missing"
    assert "vmalert" in services, "vmalert service missing"
    
    # Check categraf TCP connection to docker-proxy
    categraf_env = services["categraf"].get("environment", [])
    has_tcp = any("DOCKER_HOST=tcp://docker-proxy:2375" in env for env in categraf_env)
    assert has_tcp, "Categraf must use TCP proxy for Docker sock"
    
    # Check docker-proxy actually mounts the real socket
    proxy_volumes = services["docker-proxy"].get("volumes", [])
    has_sock = any("/var/run/docker.sock:/var/run/docker.sock" in vol for vol in proxy_volumes)
    assert has_sock, "docker-proxy must mount real sock"

    # Make sure categraf does NOT mount the real socket anymore
    categraf_volumes = services["categraf"].get("volumes", [])
    has_real_sock = any("/var/run/docker.sock" in vol for vol in categraf_volumes)
    assert not has_real_sock, "Categraf must not mount real sock directly"

@pytest.mark.skipif(os.environ.get("RUN_LIVE_TESTS") != "1", reason="Live tests require running containers. Set RUN_LIVE_TESTS=1 to run.")
def test_live_observability_pipeline():
    """Verify observability endpoints are alive."""
    # 1. VictoriaMetrics
    vm_resp = requests.get("http://localhost:8428/api/v1/targets", timeout=3)
    assert vm_resp.status_code == 200, "VictoriaMetrics targets API unreachable"

    # 2. Loki
    loki_resp = requests.get("http://localhost:3100/ready", timeout=3)
    assert loki_resp.status_code == 200, "Loki readiness probe failed"

    # 3. Alertmanager
    am_resp = requests.get("http://localhost:9093/-/ready", timeout=3)
    assert am_resp.status_code == 200, "Alertmanager readiness probe failed"

    # 4. Grafana
    gf_resp = requests.get("http://localhost:3000/api/health", timeout=3)
    assert gf_resp.status_code == 200, "Grafana health API unreachable"
