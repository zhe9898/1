"""
ZEN70 结构化日志公共模块。

统一 JSON 格式（含 timestamp/level/caller/message/X-Request-ID），
供 redis_client、sentinel、config-compiler、bootstrap 等复用。
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone

_JSON_SECRET_PATTERN = re.compile(
    r'(?i)("(?:password|passwd|pwd|secret|token|access_token|refresh_token|api_key|apikey|client_secret)"\s*:\s*")[^"]+(")'
)
_INLINE_SECRET_PATTERN = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|token|access_token|refresh_token|api_key|apikey|client_secret)\b\s*([=:])\s*([^\s,;]+)"
)
_AUTHORIZATION_PATTERN = re.compile(r"(?i)\bauthorization\b\s*:\s*bearer\s+[^\s,;]+")
_EMAIL_PATTERN = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")


def _redact_sensitive_text(value: str) -> str:
    redacted = _JSON_SECRET_PATTERN.sub(r'\1[REDACTED]\2', value)
    redacted = _AUTHORIZATION_PATTERN.sub("authorization: Bearer [REDACTED]", redacted)
    redacted = _INLINE_SECRET_PATTERN.sub(r"\1\2[REDACTED]", redacted)
    redacted = _EMAIL_PATTERN.sub("[REDACTED_EMAIL]", redacted)
    return redacted


class RequestIDOverrideFilter(logging.Filter):
    """Apply adapter-level request id overrides after LogRecord creation."""

    def filter(self, record: logging.LogRecord) -> bool:
        request_id = getattr(record, "request_id_override", None) or getattr(record, "_zen_request_id", None)
        if request_id:
            setattr(record, "request_id", request_id)
            setattr(record, "_zen_request_id", request_id)
        return True


class JsonFormatter(logging.Formatter):
    """
    单行 JSON 日志格式化器；含 timestamp(UTC)、level、logger、caller、message、X-Request-ID。
    对齐 Loki 采集标准。
    """

    def format(self, record: logging.LogRecord) -> str:
        message = _redact_sensitive_text(record.getMessage())
        log_obj: dict[str, object] = {
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "caller": f"{record.pathname}:{record.lineno}",
            "message": message,
            "X-Request-ID": getattr(record, "request_id", None),
        }
        if record.exc_info:
            log_obj["exception"] = _redact_sensitive_text(self.formatException(record.exc_info))
        return json.dumps(log_obj, ensure_ascii=False)


def get_logger(name: str, request_id: str | None = None) -> logging.LoggerAdapter:
    """返回带 request_id 的 LoggerAdapter，用于结构化日志。"""
    base = logging.getLogger(name)
    base.setLevel(logging.INFO)
    if not any(isinstance(existing, RequestIDOverrideFilter) for existing in base.filters):
        base.addFilter(RequestIDOverrideFilter())
    if not base.handlers:
        h = logging.StreamHandler()
        h.setFormatter(JsonFormatter())
        base.addHandler(h)
    rid = request_id or str(uuid.uuid4())
    return logging.LoggerAdapter(base, {"request_id_override": rid})
