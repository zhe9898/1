from __future__ import annotations

import json
from unittest.mock import MagicMock

from pytest_mock import MockerFixture

from backend.sentinel.disk_guardian import (
    REDIS_CHANNEL_DISK,
    REDIS_KEY_DISK_READONLY,
    _clear_readonly_if_set,
    _publish_disk_event,
    _set_readonly_flag,
    check_and_act,
    get_system_disk_usage,
)


def test_get_system_disk_usage_success(mocker: MockerFixture) -> None:
    mock_usage = MagicMock(total=100 * 1024**3, used=60 * 1024**3)
    mocker.patch("shutil.disk_usage", return_value=mock_usage)
    t, u, pct = get_system_disk_usage("/")
    assert t == 100.0
    assert u == 60.0
    assert pct == 60.0


def test_get_system_disk_usage_error(mocker: MockerFixture) -> None:
    mocker.patch("shutil.disk_usage", side_effect=OSError("test"))
    t, u, pct = get_system_disk_usage("/")
    assert t == 0.0
    assert u == 0.0
    assert pct == 0.0


def test_check_and_act_ok(mocker: MockerFixture) -> None:
    mocker.patch(
        "backend.sentinel.disk_guardian.get_system_disk_usage",
        return_value=(100, 50, 50.0),
    )
    mock_redis = MagicMock()
    mock_clear = mocker.patch("backend.sentinel.disk_guardian._clear_readonly_if_set")
    result = check_and_act(mock_redis, "/")
    assert result == "ok"
    mock_clear.assert_called_once_with(mock_redis, 50.0)


def test_check_and_act_critical(mocker: MockerFixture) -> None:
    mocker.patch(
        "backend.sentinel.disk_guardian.get_system_disk_usage",
        return_value=(100, 96, 96.0),
    )
    mock_redis = MagicMock()
    mock_pub = mocker.patch("backend.sentinel.disk_guardian._publish_disk_event")
    mock_set = mocker.patch("backend.sentinel.disk_guardian._set_readonly_flag")

    result = check_and_act(mock_redis, "/")
    assert result == "critical"
    mock_pub.assert_called_once_with(mock_redis, "critical", 96.0)
    mock_set.assert_called_once_with(mock_redis, True)


def test_check_and_act_warning(mocker: MockerFixture) -> None:
    mocker.patch(
        "backend.sentinel.disk_guardian.get_system_disk_usage",
        return_value=(100, 92, 92.0),
    )
    mock_redis = MagicMock()
    mock_pub = mocker.patch("backend.sentinel.disk_guardian._publish_disk_event")

    result = check_and_act(mock_redis, "/")
    assert result == "warning"
    mock_pub.assert_called_once_with(mock_redis, "warning", 92.0)


def test_publish_disk_event() -> None:
    mock_redis = MagicMock()
    _publish_disk_event(mock_redis, "critical", 96.0)
    mock_redis.publish.assert_called_once()
    args, _ = mock_redis.publish.call_args
    assert args[0] == REDIS_CHANNEL_DISK
    payload = json.loads(args[1])
    assert payload["level"] == "critical"
    assert payload["action"] == "readonly_lockdown"


def test_set_readonly_flag() -> None:
    mock_redis = MagicMock()
    _set_readonly_flag(mock_redis, True)
    mock_redis.set.assert_called_with(REDIS_KEY_DISK_READONLY, "1")

    mock_redis.reset_mock()
    _set_readonly_flag(mock_redis, False)
    mock_redis.delete.assert_called_with(REDIS_KEY_DISK_READONLY)


def test_clear_readonly_if_set(mocker: MockerFixture) -> None:
    mock_redis = MagicMock()
    mock_redis.get.return_value = "1"

    mock_set = mocker.patch("backend.sentinel.disk_guardian._set_readonly_flag")
    _clear_readonly_if_set(mock_redis, 50.0)

    mock_set.assert_called_once_with(mock_redis, False)
