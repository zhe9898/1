from __future__ import annotations

import importlib
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path

ARCH_VERSION = "V2.0"
COMPLIANCE = "strict-sre-ruleset"
REPO_ROOT = Path(__file__).resolve().parent.parent

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _repo_root() -> Path:
    return REPO_ROOT


def _write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


@contextmanager
def _temporary_gateway_profile(profile: str):
    previous_profile = os.environ.get("GATEWAY_PROFILE")
    previous_env = os.environ.get("ZEN70_ENV")
    try:
        os.environ["GATEWAY_PROFILE"] = profile
        os.environ["ZEN70_ENV"] = "development"
        yield
    finally:
        if previous_profile is None:
            os.environ.pop("GATEWAY_PROFILE", None)
        else:
            os.environ["GATEWAY_PROFILE"] = previous_profile
        if previous_env is None:
            os.environ.pop("ZEN70_ENV", None)
        else:
            os.environ["ZEN70_ENV"] = previous_env


def _load_openapi_for_profile(profile: str) -> dict[str, object]:
    from backend.api.deps import get_settings  # noqa: WPS433
    from backend.control_plane.app import factory as factory_module  # noqa: WPS433

    with _temporary_gateway_profile(profile):
        get_settings.cache_clear()
        reloaded_factory = importlib.reload(factory_module)
        app = reloaded_factory.create_app()
        return app.openapi()


def export_gateway_openapi(profile: str = "gateway-kernel") -> tuple[Path, Path]:
    """Export FastAPI OpenAPI schema for a specific gateway profile."""
    schema = _load_openapi_for_profile(profile)
    root = _repo_root()
    profile_suffix = profile.removeprefix("gateway-")
    contract_path = root / "contracts" / "openapi" / f"zen70-{profile}.openapi.json"
    docs_path = root / "docs" / f"openapi-{profile_suffix}.json"
    _write_json(contract_path, schema)
    _write_json(docs_path, schema)
    if profile == "gateway-kernel":
        _write_json(root / "docs" / "openapi.json", schema)
    return contract_path, docs_path


def write_metadata(profiles: list[str]) -> None:
    _write_json(
        _repo_root() / "contracts" / "metadata.json",
        {
            "generated_by": "ZEN70-AI-Agent",
            "architecture_version": ARCH_VERSION,
            "compliance": COMPLIANCE,
            "contracts": {
                "openapi": [f"openapi/zen70-{profile}.openapi.json" for profile in profiles],
                "triggers": [
                    "triggers/README.md",
                    "triggers/manual-trigger.example.json",
                ],
                "reservations": [
                    "reservations/README.md",
                    "reservations/manual-reservation.example.json",
                ],
            },
        },
    )


def main() -> None:
    from backend.api.deps import get_settings  # noqa: WPS433
    from scripts.freeze_openapi import sync_locked_snapshot  # noqa: WPS433

    profiles = ["gateway-kernel"]
    for profile in profiles:
        export_gateway_openapi(profile)
    write_metadata(profiles)
    with _temporary_gateway_profile("gateway-kernel"):
        get_settings.cache_clear()
        sync_locked_snapshot(quiet=True)


if __name__ == "__main__":
    main()
