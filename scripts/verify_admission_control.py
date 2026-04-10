#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

# Add repo root to path so `backend` remains importable as a package.
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.kernel.packs.registry import PACK_DEFINITIONS
from backend.kernel.topology.profile_selection import CORE_ROUTER_NAMES, OPTIONAL_ROUTER_NAMES


def verify_admission_control() -> bool:
    print("=" * 80)
    print("ZEN70 Gateway Kernel - Admission Control Verification")
    print("=" * 80)
    print()

    print("1. Core Routers (Always Loaded)")
    print("-" * 80)
    expected_core = {
        "routes",
        "auth",
        "settings",
        "profile",
        "console",
        "nodes",
        "jobs",
        "connectors",
        "triggers",
        "reservations",
        "evaluations",
    }
    actual_core = set(CORE_ROUTER_NAMES)
    if actual_core != expected_core:
        print("[FAIL] Core routers mismatch!")
        print(f"   Expected: {sorted(expected_core)}")
        print(f"   Actual: {sorted(actual_core)}")
        return False
    print(f"[OK] Core routers correct: {len(actual_core)} routers")
    for router in sorted(actual_core):
        print(f"   - {router}")
    print()

    print("2. Optional Routers (Explicit Admission)")
    print("-" * 80)
    expected_optional: set[str] = set()
    actual_optional = set(OPTIONAL_ROUTER_NAMES)
    if actual_optional != expected_optional:
        print("[FAIL] Optional routers mismatch!")
        print(f"   Expected: {sorted(expected_optional)}")
        print(f"   Actual: {sorted(actual_optional)}")
        return False
    print(f"[OK] Optional routers correct: {len(actual_optional)} routers")
    print()

    print("3. Kernel Admission Whitelist (router_admission.py)")
    print("-" * 80)
    try:
        from backend.control_plane.app.router_admission import KERNEL_ALLOWED_OPTIONAL_ROUTERS, OPTIONAL_ROUTER_MODULES
    except ImportError as exc:
        print(f"[FAIL] Failed to import from router_admission.py: {exc}")
        return False

    if KERNEL_ALLOWED_OPTIONAL_ROUTERS != expected_optional:
        print("[FAIL] Whitelist mismatch!")
        print(f"   Expected: {expected_optional}")
        print(f"   Actual: {KERNEL_ALLOWED_OPTIONAL_ROUTERS}")
        return False
    print(f"[OK] Whitelist correct: {KERNEL_ALLOWED_OPTIONAL_ROUTERS}")
    print(f"[OK] Optional router module registry entries: {sorted(OPTIONAL_ROUTER_MODULES)}")
    print()

    print("4. Router Implementation Files")
    print("-" * 80)
    missing_modules: list[str] = []
    for router_name in actual_optional:
        module_path = OPTIONAL_ROUTER_MODULES.get(router_name)
        if not module_path:
            missing_modules.append(f"{router_name}:missing-module-mapping")
            continue
        module_file = (Path(__file__).parent.parent / module_path.replace(".", "/")).with_suffix(".py")
        if not module_file.exists():
            missing_modules.append(f"{router_name}:{module_file}")
    if missing_modules:
        print(f"[FAIL] Missing optional router modules: {missing_modules}")
        return False
    print(f"[OK] Optional router modules resolved for {len(actual_optional)} router(s)")
    print()

    print("5. Pack Contract Routers (Not Loaded by Default)")
    print("-" * 80)
    pack_routers = set()
    for pack_key, pack_def in PACK_DEFINITIONS.items():
        if not pack_def.routers:
            continue
        print(f"   {pack_key}:")
        for router in pack_def.routers:
            print(f"      - {router} (delivery_stage={pack_def.delivery_stage})")
            pack_routers.add(router)

    overlap = pack_routers & (actual_core | actual_optional)
    if overlap:
        print(f"[FAIL] Pack routers overlap with kernel routers: {overlap}")
        return False
    print("[OK] No overlap: Pack routers are separate from kernel routers")
    print()

    print("=" * 80)
    print("Summary")
    print("=" * 80)
    print(f"[OK] Core Routers: {len(actual_core)}")
    print(f"[OK] Optional Routers: {len(actual_optional)}")
    print(f"[OK] Total Kernel Routers: {len(actual_core) + len(actual_optional)}")
    print(f"[OK] Pack Contract Routers: {len(pack_routers)} (not loaded by default)")
    print()
    print("[OK] Admission Control verification PASSED")
    print()
    return True


if __name__ == "__main__":
    sys.exit(0 if verify_admission_control() else 1)
