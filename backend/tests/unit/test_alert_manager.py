from __future__ import annotations

from typing import Any

import pytest
import respx
from httpx import Response

from backend.models.system import SystemLog
from backend.tests.factories import AlertPayloadFactory, MockUserFactory
from backend.workers.alert_manager import (
    push_to_bark,
    trigger_alert_endpoint,
)


@pytest.fixture
def mock_db_session(mocker: Any) -> Any:
    """Mock database session to prevent actual DB writes during testing"""
    mock_session = mocker.AsyncMock()
    # Configure so (await db.execute(...)).scalars().all() works
    mock_scalars = mocker.MagicMock()
    mock_scalars.all.return_value = []

    # We use mocker.MagicMock() for the result of await db.execute()
    mock_execute_result = mocker.MagicMock()
    mock_execute_result.scalars.return_value = mock_scalars

    mock_session.execute.return_value = mock_execute_result
    # session.add() 是 SQLAlchemy 同步方法，显式覆盖为 MagicMock 防止
    # AsyncMock 自动生成协程导致 "coroutine was never awaited" RuntimeWarning
    mock_session.add = mocker.MagicMock()
    return mock_session


@pytest.fixture
def mock_settings() -> Any:
    """Mock app settings if needed by dependencies"""

    class Settings:
        pass

    return Settings()


@pytest.fixture
def mock_user() -> Any:
    """Mock User JWT payload（法典 5.1.2：由工厂生成，无硬编码）"""
    return MockUserFactory.build()


@pytest.mark.asyncio
@respx.mock
async def test_push_to_bark_critical() -> None:
    """验证遇到 Critical 级别灾难时，是否注入了穿透参数 (Sound, TimeSensitive)"""
    bark_url = "https://api.day.app/mock_key"

    # Mock the Bark API endpoint to return 200 OK
    route = respx.get(f"{bark_url}/System%20Failure/Disk%20Rot%20Detected").mock(return_value=Response(200))

    await push_to_bark(bark_url, "System Failure", "Disk Rot Detected", "critical")

    # Assert
    assert route.called
    request = route.calls.last.request
    assert request.url.params["sound"] == "alarm"
    assert request.url.params["level"] == "timeSensitive"


@pytest.mark.asyncio
@respx.mock
async def test_push_to_bark_warning() -> None:
    """验证普通 Warning 是否为静默推送（不带警报音）"""
    bark_url = "https://api.day.app/mock_key"
    route = respx.get(f"{bark_url}/High%20Load/CPU%20is%20hot").mock(return_value=Response(200))

    await push_to_bark(bark_url, "High Load", "CPU is hot", "warning")

    assert route.called
    request = route.calls.last.request
    assert "sound" not in request.url.params
    assert "level" not in request.url.params


@pytest.mark.asyncio
@respx.mock
async def test_alert_manager_info_no_push(mock_db_session, mock_settings, mock_user, mocker) -> None:  # type: ignore[no-untyped-def]
    """验证信息级 (Info) 仅做内网写库日志，绝对不发起外部网络请求骚扰用户"""
    payload = AlertPayloadFactory.build(level="info", title="User Login", message="Admin logged in")

    # Patch async gathers/pushes just in case
    mock_bark = mocker.patch("backend.workers.alert_manager.push_to_bark")
    mock_sc = mocker.patch("backend.workers.alert_manager.push_to_serverchan")

    res = await trigger_alert_endpoint(payload, mock_settings, mock_db_session, mock_user)  # type: ignore[func-returns-value]

    assert res["status"] == "logged"  # type: ignore[index]
    assert "channels" not in res  # type: ignore[operator]

    # Assert purely database commit happened
    mock_db_session.add.assert_called_once()
    mock_db_session.commit.assert_called_once()

    added_obj = mock_db_session.add.call_args[0][0]
    assert isinstance(added_obj, SystemLog)
    assert added_obj.action == "ALERT_INFO"

    # MUST NOT push
    mock_bark.assert_not_called()
    mock_sc.assert_not_called()


@pytest.mark.asyncio
@respx.mock
async def test_alert_manager_critical_dispatch(mock_db_session, mock_settings, mock_user, mocker, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """验证高危事件并行触发 Bark 和 Server酱"""
    payload = AlertPayloadFactory.build(level="critical", title="POWER LOSS", message="UPS dying")

    monkeypatch.setenv("BARK_URL", "http://bark.dev/key")
    monkeypatch.setenv("SERVER_CHAN_KEY", "SCT_xxx")

    mock_bark = mocker.patch("backend.workers.alert_manager.push_to_bark", return_value=None)
    mock_sc = mocker.patch("backend.workers.alert_manager.push_to_serverchan", return_value=None)

    res = await trigger_alert_endpoint(payload, mock_settings, mock_db_session, mock_user)  # type: ignore[func-returns-value]

    # In asyncio.create_task(asyncio.wait(tasks)), execution depends on loop timing in test.
    # But we can verify it returned dispatched properly
    assert res["status"] == "alert_dispatched"  # type: ignore[index]
    assert res["channels"] == 2  # type: ignore[index]

    mock_db_session.add.assert_called_once()
    mock_bark.assert_called_once_with("http://bark.dev/key", "POWER LOSS", "UPS dying", "critical", icon_url="")
    mock_sc.assert_called_once_with("https://sctapi.ftqq.com", "SCT_xxx", "POWER LOSS", "UPS dying")


@pytest.mark.asyncio
@respx.mock
async def test_alert_manager_channel_failure_is_isolated(mock_db_session, mock_settings, mock_user, mocker, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    payload = AlertPayloadFactory.build(level="critical", title="POWER LOSS", message="UPS dying")
    monkeypatch.setenv("BARK_URL", "http://bark.dev/key")
    monkeypatch.setenv("SERVER_CHAN_KEY", "SCT_xxx")

    mocker.patch("backend.workers.alert_manager.push_to_bark", side_effect=RuntimeError("bark down"))
    mock_sc = mocker.patch("backend.workers.alert_manager.push_to_serverchan", return_value=None)

    res = await trigger_alert_endpoint(payload, mock_settings, mock_db_session, mock_user)  # type: ignore[func-returns-value]

    assert res["status"] == "alert_dispatched"  # type: ignore[index]
    assert res["channels"] == 2  # type: ignore[index]
    mock_sc.assert_called_once()
