from __future__ import annotations

from pathlib import Path

import jinja2
import pytest
from jinja2.sandbox import SandboxedEnvironment

from scripts.iac_core.renderer import create_jinja2_env, render_template


def test_create_jinja2_env_uses_sandbox_and_strict_undefined(tmp_path: Path) -> None:
    template_path = tmp_path / "service.yml.j2"
    template_path.write_text("service: {{ service_name }}\nmissing: {{ missing_value }}\n", encoding="utf-8")

    env = create_jinja2_env(tmp_path)

    assert isinstance(env, SandboxedEnvironment)
    with pytest.raises(jinja2.UndefinedError):
        render_template(env, "service.yml.j2", {"service_name": "gateway"})
