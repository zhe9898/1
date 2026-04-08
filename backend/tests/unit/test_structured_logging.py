from __future__ import annotations

import json
import logging
import sys

import backend.middleware  # noqa: F401
from backend.platform.logging.structured import JsonFormatter, get_logger


class TestJsonFormatter:
    def test_output_is_valid_json(self) -> None:
        fmt = JsonFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=42,
            msg="hello %s",
            args=("world",),
            exc_info=None,
        )
        output = fmt.format(record)
        parsed = json.loads(output)
        assert isinstance(parsed, dict)

    def test_contains_required_fields(self) -> None:
        fmt = JsonFormatter()
        record = logging.LogRecord(
            name="zen70.test",
            level=logging.WARNING,
            pathname="/app/test.py",
            lineno=10,
            msg="test message",
            args=(),
            exc_info=None,
        )
        parsed = json.loads(fmt.format(record))
        assert "timestamp" in parsed
        assert parsed["level"] == "WARNING"
        assert parsed["logger"] == "zen70.test"
        assert "test.py:10" in parsed["caller"]
        assert parsed["message"] == "test message"
        assert "X-Request-ID" in parsed

    def test_timestamp_is_utc_iso8601(self) -> None:
        fmt = JsonFormatter()
        record = logging.LogRecord(
            name="t",
            level=logging.INFO,
            pathname="t.py",
            lineno=1,
            msg="t",
            args=(),
            exc_info=None,
        )
        parsed = json.loads(fmt.format(record))
        assert parsed["timestamp"].endswith("Z")
        assert "+" not in parsed["timestamp"]

    def test_request_id_injected_from_record(self) -> None:
        fmt = JsonFormatter()
        record = logging.LogRecord(
            name="t",
            level=logging.INFO,
            pathname="t.py",
            lineno=1,
            msg="t",
            args=(),
            exc_info=None,
        )
        record.request_id = "rid-abc-123"
        parsed = json.loads(fmt.format(record))
        assert parsed["X-Request-ID"] == "rid-abc-123"

    def test_request_id_none_when_missing(self) -> None:
        fmt = JsonFormatter()
        record = logging.LogRecord(
            name="t",
            level=logging.INFO,
            pathname="t.py",
            lineno=1,
            msg="t",
            args=(),
            exc_info=None,
        )
        parsed = json.loads(fmt.format(record))
        assert parsed["X-Request-ID"] is None

    def test_exception_info_captured(self) -> None:
        fmt = JsonFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            record = logging.LogRecord(
                name="t",
                level=logging.ERROR,
                pathname="t.py",
                lineno=1,
                msg="fail",
                args=(),
                exc_info=sys.exc_info(),
            )
        parsed = json.loads(fmt.format(record))
        assert "exception" in parsed
        assert "ValueError" in parsed["exception"]
        assert "boom" in parsed["exception"]

    def test_chinese_message_not_escaped(self) -> None:
        fmt = JsonFormatter()
        record = logging.LogRecord(
            name="t",
            level=logging.INFO,
            pathname="t.py",
            lineno=1,
            msg="纾佺洏宸叉弧",
            args=(),
            exc_info=None,
        )
        output = fmt.format(record)
        assert "纾佺洏宸叉弧" in output
        assert "\\u" not in output

    def test_sensitive_fields_are_redacted(self) -> None:
        fmt = JsonFormatter()
        try:
            raise ValueError("password=hunter2 token=abc123 user@example.com authorization: Bearer xyz")
        except ValueError:
            record = logging.LogRecord(
                name="t",
                level=logging.ERROR,
                pathname="t.py",
                lineno=1,
                msg='{"password":"hunter2","token":"abc123","email":"user@example.com"}',
                args=(),
                exc_info=sys.exc_info(),
            )
        parsed = json.loads(fmt.format(record))
        assert "[REDACTED]" in parsed["message"]
        assert "[REDACTED_EMAIL]" in parsed["message"]
        assert "hunter2" not in parsed["message"]
        assert "abc123" not in parsed["message"]
        assert "[REDACTED]" in parsed["exception"]
        assert "[REDACTED_EMAIL]" in parsed["exception"]


class TestGetLogger:
    def test_returns_logger_adapter(self) -> None:
        lg = get_logger("test.module1")
        assert isinstance(lg, logging.LoggerAdapter)

    def test_handler_uses_json_formatter(self) -> None:
        lg = get_logger("test.module2_unique")
        base = lg.logger
        assert len(base.handlers) >= 1
        assert isinstance(base.handlers[0].formatter, JsonFormatter)

    def test_default_level_is_info(self) -> None:
        lg = get_logger("test.module3_unique")
        assert lg.logger.level == logging.INFO

    def test_request_id_auto_generated_when_none(self) -> None:
        lg = get_logger("test.module4_unique", request_id=None)
        assert "request_id_override" in lg.extra  # type: ignore[operator]
        assert len(lg.extra["request_id_override"]) > 0  # type: ignore[arg-type, index]

    def test_request_id_preserved_when_provided(self) -> None:
        lg = get_logger("test.module5_unique", request_id="custom-rid-999")
        assert lg.extra["request_id_override"] == "custom-rid-999"  # type: ignore[index]

    def test_request_id_override_does_not_conflict_with_log_record_factory(self) -> None:
        handler = logging.Handler()
        captured: dict[str, object] = {}

        def emit(record: logging.LogRecord) -> None:
            captured["request_id"] = getattr(record, "request_id", None)
            captured["_zen_request_id"] = getattr(record, "_zen_request_id", None)

        handler.emit = emit  # type: ignore[method-assign]
        lg = get_logger("test.module6_unique", request_id="custom-rid-123")
        base = lg.logger
        original_handlers = list(base.handlers)
        base.handlers = [handler]
        try:
            lg.info("hello")
        finally:
            base.handlers = original_handlers

        assert captured["request_id"] == "custom-rid-123"
        assert captured["_zen_request_id"] == "custom-rid-123"
