from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
COMPILER = PROJECT_ROOT / "scripts" / "compiler.py"


def test_compiler_caddy_render_target_only_writes_caddyfile(tmp_path: Path) -> None:
    output_dir = tmp_path / "caddy-only"
    routes_file = tmp_path / "runtime-routes.json"
    routes_file.write_text('[{"path": "/switch1/*", "target": "container1:8080"}]\n', encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"

    result = subprocess.run(
        [
            sys.executable,
            str(COMPILER),
            "system.yaml",
            "-o",
            str(output_dir),
            "--render-target",
            "caddy",
            "--dynamic-routes-file",
            str(routes_file),
        ],
        cwd=str(PROJECT_ROOT),
        timeout=60,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )

    assert result.returncode == 0, f"compiler failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    caddyfile = output_dir / "config" / "Caddyfile"
    assert caddyfile.exists()
    caddy_text = caddyfile.read_text(encoding="utf-8")
    assert "/switch1/*" in caddy_text
    assert "https://{$MACHINE_API_INTERNAL_HOST:caddy}" in caddy_text
    assert "tls internal" in caddy_text
    assert not (output_dir / "docker-compose.yml").exists()
    assert not (output_dir / ".env").exists()
    assert not (output_dir / "render-manifest.json").exists()
    assert not (output_dir / "runtime" / "secrets" / "users.acl").exists()


def test_compiler_all_render_target_ignores_runtime_dynamic_routes(tmp_path: Path) -> None:
    output_dir = tmp_path / "full-render"
    routes_file = tmp_path / "runtime-routes.json"
    routes_file.write_text('[{"path": "/switch1/*", "target": "container1:8080"}]\n', encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"

    result = subprocess.run(
        [
            sys.executable,
            str(COMPILER),
            "system.yaml",
            "-o",
            str(output_dir),
            "--dynamic-routes-file",
            str(routes_file),
        ],
        cwd=str(PROJECT_ROOT),
        timeout=60,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )

    assert result.returncode == 0, f"compiler failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    caddyfile = output_dir / "config" / "Caddyfile"
    assert caddyfile.exists()
    caddy_text = caddyfile.read_text(encoding="utf-8")
    assert "/switch1/*" not in caddy_text
    assert "https://{$MACHINE_API_INTERNAL_HOST:caddy}" in caddy_text
    assert "tls internal" in caddy_text


def test_compiler_http_listener_redirects_api_and_machine_traffic(tmp_path: Path) -> None:
    output_dir = tmp_path / "full-render"
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"

    result = subprocess.run(
        [sys.executable, str(COMPILER), "system.yaml", "-o", str(output_dir), "--render-target", "caddy"],
        cwd=str(PROJECT_ROOT),
        timeout=60,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )

    assert result.returncode == 0, f"compiler failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    caddy_text = (output_dir / "config" / "Caddyfile").read_text(encoding="utf-8")
    http_block = caddy_text.split(":80 {", 1)[1]
    assert "handle @machine_api_denied {" in http_block
    assert 'respond "machine api forbidden" 403' in http_block
    assert http_block.count("redir https://{host}{uri} 308") >= 4
    assert "reverse_proxy gateway:8000" not in http_block


def test_compiler_emits_external_acl_path_in_env(tmp_path: Path) -> None:
    output_dir = tmp_path / "full-render"
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["ZEN70_SECRET_STATE_DIR"] = str(tmp_path / "secure-state")

    result = subprocess.run(
        [sys.executable, str(COMPILER), "system.yaml", "-o", str(output_dir)],
        cwd=str(PROJECT_ROOT),
        timeout=60,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )

    assert result.returncode == 0, f"compiler failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    env_text = (output_dir / ".env").read_text(encoding="utf-8")
    compose_text = (output_dir / "docker-compose.yml").read_text(encoding="utf-8")
    acl_line = next(line for line in env_text.splitlines() if line.startswith("REDIS_ACL_FILE="))
    acl_path = Path(acl_line.split("=", 1)[1].strip())
    assert acl_path.is_absolute()
    assert not str(acl_path).startswith(str(PROJECT_ROOT))
    assert str(tmp_path / "secure-state").replace("\\", "/") in str(acl_path).replace("\\", "/")
    assert "${REDIS_ACL_FILE}: {}" not in compose_text
    assert not (output_dir / "runtime" / "secrets" / "users.acl").exists()


def test_compiler_rejects_repo_scoped_acl_output_path(tmp_path: Path) -> None:
    output_dir = tmp_path / "full-render"
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["ZEN70_SECRET_STATE_DIR"] = str(PROJECT_ROOT / "runtime" / "secrets")

    result = subprocess.run(
        [sys.executable, str(COMPILER), "system.yaml", "-o", str(output_dir)],
        cwd=str(PROJECT_ROOT),
        timeout=60,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )

    assert result.returncode != 0
    assert "Refusing to write Redis ACL" in (result.stdout + result.stderr)
