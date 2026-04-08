from __future__ import annotations

import json
from pathlib import Path

import yaml

from backend.kernel.profiles.public_profile import PUBLIC_PROFILE_SURFACE
from scripts.iac_core.profiles import PUBLIC_PROFILE_SURFACE as IAC_PUBLIC_PROFILE_SURFACE
from scripts.iac_core.profiles import resolve_requested_pack_keys


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_default_profile_surface_is_kernel_only() -> None:
    system_config = yaml.safe_load((REPO_ROOT / "system.yaml").read_text(encoding="utf-8"))
    assert system_config["deployment"]["available_profiles"] == ["gateway-kernel"]

    surfaces = json.loads((REPO_ROOT / "frontend" / "src" / "config" / "controlPlaneSurfaces.json").read_text(encoding="utf-8"))
    for surface in surfaces:
        assert surface["profiles"] == ["gateway-kernel"], surface["route_name"]


def test_runtime_public_profile_surface_excludes_legacy_presets() -> None:
    assert PUBLIC_PROFILE_SURFACE == ("gateway-kernel",)
    assert IAC_PUBLIC_PROFILE_SURFACE == PUBLIC_PROFILE_SURFACE


def test_pack_selection_requires_explicit_pack_keys() -> None:
    assert resolve_requested_pack_keys("gateway-iot") == ()
    assert resolve_requested_pack_keys("gateway-kernel", ["iot-pack", "ops-pack"]) == ("iot-pack", "ops-pack")
