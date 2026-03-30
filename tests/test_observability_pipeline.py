import os
import subprocess
import requests
import pytest
from pathlib import Path
import yaml

PROJECT_ROOT = Path(__file__).parent.parent
COMPILER_SCRIPT = PROJECT_ROOT / "scripts" / "compiler.py"
SYSTEM_YAML = PROJECT_ROOT / "system.yaml"
OUTPUT_COMPOSE = PROJECT_ROOT / "docker-compose.yml"

def test_compiler_success():
    """Verify compiler can generate docker-compose.yml without errors."""
    result = subprocess.run(
        ["python", str(COMPILER_SCRIPT), "system.yaml", "-o", "."],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"Compiler failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    assert OUTPUT_COMPOSE.exists(), "docker-compose.yml was not generated"

def test_docker_compose_validity():
    """Verify the generated docker-compose.yml is valid according to architecture rules."""
    with open(OUTPUT_COMPOSE, "r", encoding="utf-8") as f:
        compose_data = yaml.safe_load(f)
    
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
