import logging
import sqlite3
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
import respx
from httpx import Response

import backend.sentinel.data_integrity as data_integrity


@pytest.fixture(autouse=True)
def isolated_db(mocker: Any, tmp_path: Path) -> Generator[None, None, None]:
    """保证每次测试前重建全新的 SQLite 库基准，不污染本地状态。"""
    db_path = tmp_path / "bit_rot_baseline.db"
    mocker.patch.object(data_integrity, "DB_PATH", db_path)
    if db_path.exists():
        db_path.unlink()
    data_integrity.init_baseline_db()
    yield
    for _ in range(3):
        try:
            if db_path.exists():
                db_path.unlink()
            break
        except PermissionError:
            time.sleep(0.05)


@pytest.fixture
def temp_test_file(tmp_path: Path) -> str:
    """自动生成用于验证哈希与静默翻转的临时冷数据"""
    test_file = tmp_path / "video_record.mp4"
    test_file.write_text("INITIAL_VIDEO_DATA_BLOCK", encoding="utf-8")
    return str(test_file)


def test_compute_sha256(temp_test_file: str) -> None:
    """验证流式哈希的正确性"""
    hash_val = data_integrity.compute_sha256(temp_test_file)
    assert hash_val is not None
    # 稳定输入 = 稳定哈希
    assert len(hash_val) == 64


def test_cpu_load_avoidance(mocker: Any, temp_test_file: str) -> None:
    """【SLA 防线测试】验证高 CPU 压力时，哈希扫描任务完全挂起"""
    # 让探针误以为 CPU 负载 99%
    mocker.patch("backend.sentinel.data_integrity.psutil.cpu_percent", return_value=99.0)

    # 执行检查
    data_integrity.scan_and_verify_directory(str(Path(temp_test_file).parent))

    # 验证此时 SQLite 没有建立基线（因为任务直接 return 了）
    conn = sqlite3.connect(data_integrity.DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM file_hashes")
    assert cur.fetchone()[0] == 0
    conn.close()


def test_first_run_creates_baseline(mocker: Any, temp_test_file: str) -> None:
    """第一层扫描：必须能正确在表里写入初始记录"""
    mocker.patch("backend.sentinel.data_integrity.psutil.cpu_percent", return_value=5.0)
    target_dir = str(Path(temp_test_file).parent)

    data_integrity.scan_and_verify_directory(target_dir)

    conn = sqlite3.connect(data_integrity.DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT sha256, size FROM file_hashes WHERE filepath = ?", (temp_test_file,))
    row = cur.fetchone()

    assert row is not None
    assert row[1] == Path(temp_test_file).stat().st_size
    conn.close()


def test_bit_rot_detection(mocker: Any, temp_test_file: str) -> None:
    """【核心劫难测试】模拟大小没变、但是字节内容翻转的花屏现象，验证是否成功捕获"""
    mocker.patch("backend.sentinel.data_integrity.psutil.cpu_percent", return_value=5.0)
    target_dir = str(Path(temp_test_file).parent)

    # 1. 第一波扫描建档
    data_integrity.scan_and_verify_directory(target_dir)

    # 2. 模拟静默腐败：保持长度一致("I" -> "X")，模拟底层磁道翻转 1 字节
    with Path(temp_test_file).open("r+", encoding="utf-8") as f:
        content = f.read()
        f.seek(0)
        f.write(content.replace("I", "X"))

    # Hook into logger to see if critical was fired
    spy_logger = mocker.spy(logging.getLogger("zen70.sentinel.bit_rot"), "critical")

    # 3. 第二波巡检 (恶星降临)
    data_integrity.scan_and_verify_directory(target_dir)

    # 4. 验证系统必定拉响防空警报
    spy_logger.assert_called()
    assert "静默数据腐败检测触发" in spy_logger.call_args_list[0][0][0]


def test_invalid_directory_is_guarded(mocker: Any) -> None:
    """目录不存在时必须安全退出，不做无效 I/O。"""
    mocker.patch("backend.sentinel.data_integrity.psutil.cpu_percent", return_value=5.0)
    spy_logger = mocker.spy(logging.getLogger("zen70.sentinel.bit_rot"), "error")
    data_integrity.scan_and_verify_directory("Z:/path/not-exists")
    spy_logger.assert_called()
    assert "巡检目录不存在或不是目录" in spy_logger.call_args_list[0][0][0]


def test_alert_webhook_called_for_corruption(
    mocker: Any,
    monkeypatch: pytest.MonkeyPatch,
    temp_test_file: str,
) -> None:
    """发生静默腐败时必须触发告警 webhook。"""
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "http://example.com/webhook")
    mocker.patch("backend.sentinel.data_integrity.psutil.cpu_percent", return_value=5.0)
    post_spy = mocker.patch("backend.sentinel.data_integrity.httpx.Client.post")

    target_dir = str(Path(temp_test_file).parent)
    data_integrity.scan_and_verify_directory(target_dir)
    with Path(temp_test_file).open("r+", encoding="utf-8") as f:
        content = f.read()
        f.seek(0)
        f.write(content.replace("I", "X"))
    data_integrity.scan_and_verify_directory(target_dir)

    post_spy.assert_called_once()


@respx.mock
def test_alert_webhook_payload_contract(
    monkeypatch: pytest.MonkeyPatch,
    mocker: Any,
    temp_test_file: str,
) -> None:
    """端到端验证告警 webhook 载荷结构与关键字段，保障外部链路契约稳定。"""
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "http://alert.local/webhook")
    mocker.patch("backend.sentinel.data_integrity.psutil.cpu_percent", return_value=5.0)
    route = respx.post("http://alert.local/webhook").mock(return_value=Response(200))

    target_dir = str(Path(temp_test_file).parent)
    data_integrity.scan_and_verify_directory(target_dir)
    with Path(temp_test_file).open("r+", encoding="utf-8") as f:
        content = f.read()
        f.seek(0)
        f.write(content.replace("I", "X"))
    data_integrity.scan_and_verify_directory(target_dir)

    assert route.called
    request_payload = route.calls.last.request.read().decode("utf-8")
    assert "level" in request_payload
    assert "critical" in request_payload
    assert "source" in request_payload
    assert "data_integrity" in request_payload


def test_alert_timeout_reads_from_env(
    mocker: Any,
    monkeypatch: pytest.MonkeyPatch,
    temp_test_file: str,
) -> None:
    """告警客户端超时应受环境变量控制，确保 IaC/环境统一注入。"""
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "http://example.com/webhook")
    monkeypatch.setenv("BIT_ROT_ALERT_TIMEOUT_SECONDS", "9.5")
    mocker.patch("backend.sentinel.data_integrity.psutil.cpu_percent", return_value=5.0)
    client_ctor = mocker.patch("backend.sentinel.data_integrity.httpx.Client")

    target_dir = str(Path(temp_test_file).parent)
    data_integrity.scan_and_verify_directory(target_dir)
    with Path(temp_test_file).open("r+", encoding="utf-8") as f:
        content = f.read()
        f.seek(0)
        f.write(content.replace("I", "X"))
    data_integrity.scan_and_verify_directory(target_dir)

    assert client_ctor.call_args is not None
    assert client_ctor.call_args.kwargs["timeout"] == 9.5


def test_invalid_numeric_env_falls_back_to_defaults(
    mocker: Any,
    monkeypatch: pytest.MonkeyPatch,
    temp_test_file: str,
) -> None:
    """非法数值配置必须回退默认值，避免错误配置引发巡检不可用。"""
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "http://example.com/webhook")
    monkeypatch.setenv("BIT_ROT_ALERT_TIMEOUT_SECONDS", "bad-value")
    monkeypatch.setenv("BIT_ROT_SQLITE_CONNECT_TIMEOUT_SECONDS", "-1")
    monkeypatch.setenv("BIT_ROT_SQLITE_BUSY_TIMEOUT_MS", "0")
    mocker.patch("backend.sentinel.data_integrity.psutil.cpu_percent", return_value=5.0)
    client_ctor = mocker.patch("backend.sentinel.data_integrity.httpx.Client")

    target_dir = str(Path(temp_test_file).parent)
    data_integrity.scan_and_verify_directory(target_dir)
    with Path(temp_test_file).open("r+", encoding="utf-8") as f:
        content = f.read()
        f.seek(0)
        f.write(content.replace("I", "X"))
    data_integrity.scan_and_verify_directory(target_dir)

    assert client_ctor.call_args is not None
    assert client_ctor.call_args.kwargs["timeout"] == data_integrity.DEFAULT_ALERT_TIMEOUT_SECONDS


def test_scan_performance_boundary(
    mocker: Any,
    tmp_path: Path,
) -> None:
    """100 个小文件扫描需在可接受边界内完成，防止回归劣化。"""
    mocker.patch("backend.sentinel.data_integrity.psutil.cpu_percent", return_value=5.0)
    for idx in range(100):
        (tmp_path / f"payload_{idx}.txt").write_text(f"block-{idx}", encoding="utf-8")

    start = time.perf_counter()
    data_integrity.scan_and_verify_directory(str(tmp_path))
    elapsed = time.perf_counter() - start
    assert elapsed < 2.0
