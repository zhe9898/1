from __future__ import annotations

import subprocess
import types
import zipfile
from pathlib import Path

import pytest

import scripts.backup as backup


class _FakeAESZipFile(zipfile.ZipFile):
    def __init__(self, file, mode="r", compression=zipfile.ZIP_DEFLATED, encryption=None):  # type: ignore[override]
        del encryption
        super().__init__(file, mode=mode, compression=compression)

    def setpassword(self, password: bytes) -> None:
        self.password = password


def _fake_pyzipper_module() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        AESZipFile=_FakeAESZipFile,
        ZIP_DEFLATED=zipfile.ZIP_DEFLATED,
        WZ_AES=99,
    )


def test_create_ashbox_backup_requires_external_password(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ASHBOX_PASSWORD", raising=False)
    with pytest.raises(RuntimeError, match="ASHBOX_PASSWORD"):
        backup.create_ashbox_backup()


def test_create_ashbox_backup_requires_pyzipper(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ASHBOX_PASSWORD", "strong-passphrase")
    monkeypatch.setattr(backup, "pyzipper", None)
    with pytest.raises(RuntimeError, match="pyzipper"):
        backup.create_ashbox_backup()


def test_create_ashbox_backup_keeps_sql_dump_in_memory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ASHBOX_PASSWORD", "strong-passphrase")
    monkeypatch.setenv("MEDIA_PATH", str(tmp_path / "media"))
    monkeypatch.setattr(backup, "pyzipper", _fake_pyzipper_module())

    (tmp_path / ".env").write_text("JWT_SECRET=test\n", encoding="utf-8")
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    (media_dir / "hello.txt").write_text("hello", encoding="utf-8")

    completed = subprocess.CompletedProcess(
        args=["docker", "exec"],
        returncode=0,
        stdout=b"select 1;\n",
        stderr=b"",
    )
    monkeypatch.setattr(backup.subprocess, "run", lambda *args, **kwargs: completed)

    archive_path = backup.create_ashbox_backup()

    assert archive_path.exists()
    assert not (tmp_path / "db_dump.sql").exists()
    assert not any(tmp_path.glob("ASHBOX_PASSWORD.generated.txt"))

    with zipfile.ZipFile(archive_path) as zf:
        assert ".env" in zf.namelist()
        assert "db_dump.sql" in zf.namelist()
        assert "media/hello.txt" in zf.namelist()
        assert zf.read("db_dump.sql") == b"select 1;\n"
