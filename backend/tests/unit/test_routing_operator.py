from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest import MonkeyPatch
from pytest_mock import MockerFixture

from backend.sentinel.routing_operator import RoutingOperator


@pytest.fixture
def mock_env(monkeypatch: MonkeyPatch) -> None:
    """RoutingOperator 测试最小 env。"""
    monkeypatch.setenv("REDIS_HOST", "localhost")
    monkeypatch.setenv("CADDY_ADMIN_URL", "http://localhost:2019/load")
    monkeypatch.setenv("SWITCH_SERVICE_PORTS", '{"switch1": "8080"}')
    monkeypatch.setenv("SWITCH_CONTAINER_MAP", '{"switch1": "container1"}')


@pytest.mark.asyncio
async def test_compile_routes(mock_env: None, mocker: MockerFixture, tmp_path: Path) -> None:
    op = RoutingOperator()
    op.project_root = tmp_path
    op.routes_state_file = tmp_path / "runtime" / "control-plane" / "routes.json"

    mock_run = mocker.patch("subprocess.run")
    await op._compile_routes([{"path": "/switch1/*", "target": "container1:8080"}])

    routes_file = tmp_path / "runtime" / "control-plane" / "routes.json"
    assert routes_file.exists()
    assert json.loads(routes_file.read_text(encoding="utf-8")) == [{"path": "/switch1/*", "target": "container1:8080"}]
    mock_run.assert_called_once()
    called_args = mock_run.call_args.args[0]
    assert "--render-target" in called_args
    assert "caddy" in called_args
    assert "--dynamic-routes-file" in called_args
    assert str(routes_file) in called_args


@pytest.mark.asyncio
async def test_reload_caddy_success(mock_env: None, mocker: MockerFixture, tmp_path: Path) -> None:
    op = RoutingOperator()
    op.project_root = tmp_path

    caddy_dir = tmp_path / "config"
    caddy_dir.mkdir()
    caddyfile = caddy_dir / "Caddyfile"
    caddyfile.write_bytes(b"test caddyfile")

    mock_post = mocker.patch("httpx.AsyncClient.post", new_callable=AsyncMock)
    mock_post.return_value = MagicMock(status_code=200)

    await op._reload_caddy()

    mock_post.assert_awaited_once_with(
        "http://localhost:2019/load",
        headers={"Content-Type": "text/caddyfile"},
        content=b"test caddyfile",
    )


@pytest.mark.asyncio
async def test_reload_caddy_failure(mock_env: None, mocker: MockerFixture, tmp_path: Path) -> None:
    op = RoutingOperator()
    op.project_root = tmp_path

    caddy_dir = tmp_path / "config"
    caddy_dir.mkdir()
    caddyfile = caddy_dir / "Caddyfile"
    caddyfile.write_bytes(b"test caddyfile")

    mock_post = mocker.patch("httpx.AsyncClient.post", new_callable=AsyncMock)
    mock_post.return_value = MagicMock(status_code=500, text="error")

    await op._reload_caddy()
    mock_post.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_redis(mock_env: None, mocker: MockerFixture) -> None:
    mock_redis_cls = mocker.patch("redis.asyncio.Redis")
    mock_redis_cls.return_value = MagicMock()
    op = RoutingOperator()
    r = await op._get_redis()
    assert r is not None
