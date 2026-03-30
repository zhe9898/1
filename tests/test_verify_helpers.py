from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
VERIFY_PATH = REPO_ROOT / "scripts" / "verify.py"


def _load_verify_module():
    spec = importlib.util.spec_from_file_location("zen70_verify_helpers", VERIFY_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_get_docker_client_retries_known_docker_failures(monkeypatch) -> None:
    verify_mod = _load_verify_module()

    class FakeDockerException(Exception):
        pass

    class FakeDockerModule:
        class errors:
            DockerException = FakeDockerException

        attempts = 0

        @classmethod
        def from_env(cls):
            cls.attempts += 1
            raise FakeDockerException("daemon unavailable")

    monkeypatch.setattr(verify_mod, "docker", FakeDockerModule)
    monkeypatch.setattr(verify_mod.time, "sleep", lambda _seconds: None)

    assert verify_mod.get_docker_client(retries=2, delay=0) is None
    assert FakeDockerModule.attempts == 2


def test_get_docker_client_does_not_swallow_programming_errors(monkeypatch) -> None:
    verify_mod = _load_verify_module()

    class FakeDockerModule:
        class errors:
            class DockerException(Exception):
                pass

        @staticmethod
        def from_env():
            raise ZeroDivisionError("boom")

    monkeypatch.setattr(verify_mod, "docker", FakeDockerModule)

    with pytest.raises(ZeroDivisionError, match="boom"):
        verify_mod.get_docker_client(retries=1, delay=0)
