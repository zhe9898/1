from __future__ import annotations

import logging

from backend.api.main import app
from backend.middleware import RequestIDMiddleware, _request_id_ctx, record_factory
from backend.tests.unit._repo_paths import repo_path


def test_request_id_middleware_is_registered_outermost() -> None:
    assert app.user_middleware
    assert app.user_middleware[0].cls is RequestIDMiddleware


def test_record_factory_backfills_request_id_from_context() -> None:
    token = _request_id_ctx.set("req-test-123")
    try:
        record = record_factory("zen70.test", logging.INFO, __file__, 10, "message", (), None)
    finally:
        _request_id_ctx.reset(token)

    assert getattr(record, "_zen_request_id", None) == "req-test-123"
    assert getattr(record, "request_id", None) == "req-test-123"


def test_main_source_contains_no_known_mojibake_markers() -> None:
    text = repo_path("backend", "api", "main.py").read_text(encoding="utf-8")
    for marker in ("闁", "濞", "婵", "鍐", "鍙", "鏉", "銆"):
        assert marker not in text
