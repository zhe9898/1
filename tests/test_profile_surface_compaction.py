from __future__ import annotations

import json
from pathlib import Path

import yaml

from backend.core.pack_registry import PROFILE_ALIASES, PUBLIC_PROFILE_SURFACE
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

    for legacy in ("gateway", "gateway-iot", "gateway-ops"):
        assert legacy in PROFILE_ALIASES
        assert legacy not in PUBLIC_PROFILE_SURFACE


def test_legacy_profile_inputs_collapse_into_kernel_plus_packs() -> None:
    assert resolve_requested_pack_keys("gateway-iot") == ("iot-pack",)
    assert resolve_requested_pack_keys("gateway-ops") == ("ops-pack",)
