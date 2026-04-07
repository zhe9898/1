from __future__ import annotations

import ast
from pathlib import Path


def test_backend_main_module_docstring_is_readable() -> None:
    text = Path("backend/api/main.py").read_text(encoding="utf-8")
    module_doc = ast.get_docstring(ast.parse(text))
    assert module_doc is not None
    assert "Gateway API (FastAPI) entry module." in module_doc
    assert "control-plane HTTP surface" in module_doc
    assert "Request-ID propagation" in module_doc
