"""Schema reference resolution for extension manifest files."""

from __future__ import annotations

import importlib

from pydantic import BaseModel


def load_model_ref(ref: str | None) -> type[BaseModel] | None:
    normalized = str(ref or "").strip()
    if not normalized:
        return None
    if ":" not in normalized:
        raise ValueError(f"Schema ref '{normalized}' must use module.path:ClassName format")
    module_name, attr_name = normalized.split(":", 1)
    module = importlib.import_module(module_name)
    resolved = getattr(module, attr_name, None)
    if resolved is None:
        raise ValueError(f"Schema ref '{normalized}' could not be resolved")
    if not isinstance(resolved, type) or not issubclass(resolved, BaseModel):
        raise ValueError(f"Schema ref '{normalized}' must resolve to a Pydantic BaseModel subclass")
    return resolved
