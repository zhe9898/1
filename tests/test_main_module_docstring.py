from __future__ import annotations

import ast

from tests._repo_paths import repo_path


def test_backend_main_module_docstring_is_readable() -> None:
    text = repo_path("backend", "api", "main.py").read_text(encoding="utf-8")
    module_doc = ast.get_docstring(ast.parse(text))
    assert module_doc is not None
    assert "FastAPI entrypoint" in module_doc
    assert "backend-driven control plane" in module_doc
