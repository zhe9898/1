from __future__ import annotations

import importlib.util
from pathlib import Path

COMPILER_PATH = Path(__file__).resolve().parents[3] / "scripts" / "compiler.py"
_SPEC = importlib.util.spec_from_file_location("zen70_compiler_cli", COMPILER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
_replace_text_artifact = _MODULE._replace_text_artifact


def test_replace_text_artifact_falls_back_when_atomic_replace_is_denied(
    tmp_path: Path,
    monkeypatch,
) -> None:
    tmp_file = tmp_path / "docker-compose.yml.tmp"
    target_file = tmp_path / "docker-compose.yml"
    tmp_file.write_text("new artifact\n", encoding="utf-8")
    target_file.write_text("old artifact\n", encoding="utf-8")

    original_replace = Path.replace

    def fake_replace(self: Path, target: str | Path) -> Path:
        if self == tmp_file and Path(target) == target_file:
            raise PermissionError("WinError 5")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fake_replace)

    _replace_text_artifact(tmp_file, target_file)

    assert target_file.read_text(encoding="utf-8") == "new artifact\n"
    assert not tmp_file.exists()
