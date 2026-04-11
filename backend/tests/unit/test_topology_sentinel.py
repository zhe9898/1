from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from pytest import MonkeyPatch
from pytest_mock import MockerFixture

from backend.platform.redis.constants import KEY_DISK_TAINT, KEY_HARDWARE_GPU_STATE
from backend.platform.redis.runtime_state import sentinel_override_key
from backend.sentinel.mount_runtime import MountTransitionAction, plan_mount_state_transition, resolve_debounced_mount_state
from backend.sentinel.topology_sentinel import (
    MountPoint,
    TopologySentinel,
)


@pytest.fixture
def mock_env(monkeypatch: MonkeyPatch) -> None:
    """最小 env，用于初始化 TopologySentinel。"""
    monkeypatch.setenv("REDIS_HOST", "localhost")
    monkeypatch.setenv("MOUNT_POINTS", "/tmp/mock_disk,1234-5678,1")
    monkeypatch.setenv("SWITCH_CONTAINER_MAP", '{"switch1": "container1"}')


def test_mount_point_basics(mocker: MockerFixture) -> None:
    mp = MountPoint("/tmp/nonexistent", "1234", 1)

    # Test check_exists
    mocker.patch.object(Path, "exists", return_value=True)
    assert mp.check_exists() is True

    # Test get_free_space
    mocker.patch("shutil.disk_usage", return_value=MagicMock(free=2 * 1024**3))
    assert mp.get_free_space() == 2 * 1024**3

    # Test get_uuid — 需要为 findmnt/blkid 的调用链提供足够 side_effect 值
    mocker.patch(
        "subprocess.run",
        side_effect=[
            MagicMock(returncode=0, stdout="/dev/sda1\n"),
            MagicMock(returncode=0, stdout="1234\n"),
            MagicMock(returncode=0, stdout="/dev/sda1\n"),
            MagicMock(returncode=0, stdout="1234\n"),
        ],
    )
    assert mp.get_uuid() == "1234"

    # Test verify_full
    ok, reason = mp.verify_full()
    assert ok is True
    assert reason == "ok"


def test_topology_sentinel_init_and_redis(mock_env: None, mocker: MockerFixture) -> None:
    mock_client_cls = mocker.patch("backend.sentinel.topology_sentinel.SyncRedisClient")
    instance: Any = mock_client_cls.return_value
    instance.ping.return_value = True

    sentinel = TopologySentinel()

    assert sentinel.redis_host == "localhost"
    assert len(sentinel.mounts) == 1
    assert sentinel.mounts[0].path == Path("/tmp/mock_disk")
    instance.connect.assert_called_once()
    assert sentinel._redis_ok() is True


def test_topology_sentinel_check_disk_usage(mock_env: None, mocker: MockerFixture) -> None:
    mocker.patch("backend.sentinel.topology_sentinel.SyncRedisClient")
    sentinel = TopologySentinel()
    sentinel._event_publisher.publish_signal = MagicMock(return_value=1)  # type: ignore[union-attr]
    sentinel._redis.kv.set = MagicMock()  # type: ignore[union-attr]
    sentinel._redis.kv.delete = MagicMock()  # type: ignore[union-attr]

    mock_usage = MagicMock(used=96, total=100)
    mocker.patch("shutil.disk_usage", return_value=mock_usage)

    # 修复后不再直接调用 _safe_container_action，改为设置 Taint 标志
    sentinel._check_disk_usage()

    # 验证 Taint 机制：标志位被设置 + Redis 事件发布
    assert sentinel.has_disk_taint is True
    sentinel._event_publisher.publish_signal.assert_called()  # type: ignore[union-attr]
    sentinel._redis.kv.set.assert_called_with(KEY_DISK_TAINT, "active", ex=300)  # type: ignore[union-attr]

    # 验证磁盘恢复时自动清除 Taint
    mock_usage_ok = MagicMock(used=80, total=100)
    mocker.patch("shutil.disk_usage", return_value=mock_usage_ok)
    sentinel._check_disk_usage()
    assert sentinel.has_disk_taint is False


def test_topology_sentinel_reconcile_loop(mock_env: None, mocker: MockerFixture) -> None:
    mocker.patch("backend.sentinel.topology_sentinel.SyncRedisClient")
    sentinel = TopologySentinel()

    sentinel._redis.ping.return_value = True  # type: ignore[union-attr]
    sentinel._redis.kv.get.return_value = None  # type: ignore[union-attr]

    def _hash_get_all(key: str) -> dict[str, str]:
        if key == KEY_HARDWARE_GPU_STATE:
            return {}
        if key == "switch:switch1":
            return {"state": "ON"}
        return {}

    sentinel._redis.hashes.get_all.side_effect = _hash_get_all  # type: ignore[union-attr]
    sentinel._event_publisher.publish_signal = MagicMock(return_value=1)  # type: ignore[union-attr]

    mocker.patch.object(sentinel, "_get_actual_running_containers", return_value=set())
    mock_action = mocker.patch.object(sentinel, "_safe_container_action")

    sentinel._reconcile_loop()

    mock_action.assert_called_with("container1", "start")


def test_topology_sentinel_run_once(mock_env: None, mocker: MockerFixture) -> None:
    mocker.patch("backend.sentinel.topology_sentinel.SyncRedisClient")
    sentinel = TopologySentinel()

    mock_disk = mocker.patch.object(sentinel, "_check_disk_usage")
    mock_mount = mocker.patch.object(sentinel, "_handle_mount")
    mock_gpu = mocker.patch.object(sentinel, "_check_gpu", return_value={"online": "true"})
    mock_reconcile = mocker.patch.object(sentinel, "_reconcile_loop")

    sentinel.run_once()

    mock_disk.assert_called_once()
    mock_mount.assert_called_once()
    mock_gpu.assert_called_once()
    mock_reconcile.assert_called_once()


def test_topology_sentinel_safe_action(mock_env: None, mocker: MockerFixture) -> None:
    mocker.patch("backend.sentinel.topology_sentinel.SyncRedisClient")
    sentinel = TopologySentinel()

    mocker.patch(
        "backend.sentinel.topology_sentinel._docker_api_post",
        return_value=(204, ""),
    )

    # Test stateful stop
    sentinel._safe_container_action("zen70-postgres", "stop")

    # Test pure IO pause
    sentinel._safe_container_action("some-io-container", "stop")

    # Test start
    sentinel._safe_container_action("some-container", "start")


def test_mount_runtime_requires_stable_window() -> None:
    assert resolve_debounced_mount_state([True, True, True], window_size=3) is not None
    assert resolve_debounced_mount_state([True, False, True], window_size=3) is None
    assert resolve_debounced_mount_state([True, True], window_size=3) is None


def test_mount_runtime_transition_plan_distinguishes_pending_recovery() -> None:
    assert plan_mount_state_transition(current_state="pending", target_state="offline").action is MountTransitionAction.NOOP
    assert plan_mount_state_transition(current_state="pending", target_state="online").action is MountTransitionAction.VERIFY_PENDING_ONLINE
    assert plan_mount_state_transition(current_state="offline", target_state="online").action is MountTransitionAction.MARK_ONLINE


def test_topology_sentinel_resizes_mount_cache_to_window_size(
    mock_env: None,
    monkeypatch: MonkeyPatch,
    mocker: MockerFixture,
) -> None:
    monkeypatch.setenv("DEBOUNCE_WINDOW", "5")
    mocker.patch("backend.sentinel.topology_sentinel.SyncRedisClient")

    sentinel = TopologySentinel()

    assert sentinel.mounts[0].state_cache.maxlen == 5


def test_topology_sentinel_pause_signal_sets_runtime_override_and_stops_container(
    mock_env: None,
    mocker: MockerFixture,
) -> None:
    mocker.patch("backend.sentinel.topology_sentinel.SyncRedisClient")
    sentinel = TopologySentinel()
    sentinel._safe_container_action = MagicMock()
    publish_route_meltdown = mocker.patch(
        "backend.sentinel.topology_sentinel.TopologyRuntimeIO.publish_route_meltdown"
    )
    sentinel._redis.kv.set = MagicMock()  # type: ignore[union-attr]

    sentinel._process_switch_event_message('{"switch":"switch1","state":"PAUSE"}')

    sentinel._redis.kv.set.assert_any_call(sentinel_override_key("switch1"), "OFF")  # type: ignore[union-attr]
    sentinel._redis.kv.set.assert_any_call(sentinel_override_key("container1"), "OFF")  # type: ignore[union-attr]
    sentinel._safe_container_action.assert_called_once_with("container1", "stop")
    publish_route_meltdown.assert_called_once_with("container1")


def test_topology_sentinel_restart_signal_restarts_container_without_persisting_override(
    mock_env: None,
    mocker: MockerFixture,
) -> None:
    mocker.patch("backend.sentinel.topology_sentinel.SyncRedisClient")
    sentinel = TopologySentinel()
    sentinel._safe_container_action = MagicMock()
    sentinel._redis.kv.set = MagicMock()  # type: ignore[union-attr]

    sentinel._process_switch_event_message('{"switch":"container1","state":"RESTART"}')

    sentinel._safe_container_action.assert_any_call("container1", "stop")
    sentinel._safe_container_action.assert_any_call("container1", "start")
    sentinel._redis.kv.set.assert_not_called()  # type: ignore[union-attr]


def test_topology_sentinel_on_signal_clears_runtime_override_without_container_side_effects(
    mock_env: None,
    mocker: MockerFixture,
) -> None:
    mocker.patch("backend.sentinel.topology_sentinel.SyncRedisClient")
    sentinel = TopologySentinel()
    sentinel._safe_container_action = MagicMock()
    sentinel._redis.kv.delete = MagicMock()  # type: ignore[union-attr]

    sentinel._process_switch_event_message('{"switch":"switch1","state":"ON"}')

    sentinel._redis.kv.delete.assert_called_once_with(  # type: ignore[union-attr]
        sentinel_override_key("switch1"),
        sentinel_override_key("container1"),
    )
    sentinel._safe_container_action.assert_not_called()
