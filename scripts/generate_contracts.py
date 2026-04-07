from __future__ import annotations

import importlib
import json
import os
import sys
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


def _load_openapi_for_profile(profile: str) -> dict[str, object]:
    os.environ["GATEWAY_PROFILE"] = profile
    from backend.api import main as main_module  # noqa: WPS433

    reloaded = importlib.reload(main_module)
    return reloaded.app.openapi()


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
    profiles = ["gateway-kernel", "gateway-iot", "gateway-ops"]
    for profile in profiles:
        export_gateway_openapi(profile)
    write_metadata(profiles)


if __name__ == "__main__":
    main()
