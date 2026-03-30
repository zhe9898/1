#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Admission Control Verification Script

验证 Gateway Kernel 的路由准入控制机制是否正确工作。
"""

import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from backend.core.gateway_profile import CORE_ROUTER_NAMES, OPTIONAL_ROUTER_NAMES


def verify_admission_control():
    """验证准入控制配置的一致性"""
    print("=" * 80)
    print("ZEN70 Gateway Kernel - Admission Control Verification")
    print("=" * 80)
    print()

    # 1. 验证核心路由
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
    }
    actual_core = set(CORE_ROUTER_NAMES)

    if actual_core == expected_core:
        print(f"[OK] Core routers correct: {len(actual_core)} routers")
        for router in sorted(actual_core):
            print(f"   - {router}")
    else:
        print(f"[FAIL] Core routers mismatch!")
        print(f"   Expected: {sorted(expected_core)}")
        print(f"   Actual: {sorted(actual_core)}")
        return False
    print()

    # 2. 验证可选路由
    print("2. Optional Routers (Explicit Admission)")
    print("-" * 80)
    expected_optional = {"cluster"}
    actual_optional = set(OPTIONAL_ROUTER_NAMES)

    if actual_optional == expected_optional:
        print(f"[OK] Optional routers correct: {len(actual_optional)} routers")
        for router in sorted(actual_optional):
            print(f"   - {router}")
    else:
        print(f"[FAIL] Optional routers mismatch!")
        print(f"   Expected: {sorted(expected_optional)}")
        print(f"   Actual: {sorted(actual_optional)}")
        return False
    print()

    # 3. 验证 main.py 的白名单
    print("3. Kernel Admission Whitelist (main.py)")
    print("-" * 80)
    try:
        from backend.api.main import (
            KERNEL_ALLOWED_OPTIONAL_ROUTERS,
            OPTIONAL_ROUTER_MODULES,
        )

        if KERNEL_ALLOWED_OPTIONAL_ROUTERS == {"cluster"}:
            print(f"[OK] Whitelist correct: {KERNEL_ALLOWED_OPTIONAL_ROUTERS}")
        else:
            print(f"[FAIL] Whitelist mismatch!")
            print(f"   Expected: {{'cluster'}}")
            print(f"   Actual: {KERNEL_ALLOWED_OPTIONAL_ROUTERS}")
            return False

        if "cluster" in OPTIONAL_ROUTER_MODULES:
            print(f"[OK] Module mapping exists: cluster -> {OPTIONAL_ROUTER_MODULES['cluster']}")
        else:
            print(f"[FAIL] Module mapping missing for 'cluster'")
            return False
    except ImportError as e:
        print(f"[FAIL] Failed to import from main.py: {e}")
        return False
    print()

    # 4. 验证 cluster 路由文件存在
    print("4. Router Implementation Files")
    print("-" * 80)
    cluster_file = Path(__file__).parent.parent / "backend" / "api" / "cluster.py"
    if cluster_file.exists():
        print(f"[OK] cluster.py exists: {cluster_file}")
    else:
        print(f"[FAIL] cluster.py not found: {cluster_file}")
        return False
    print()

    # 5. 验证 Pack 定义
    print("5. Pack Contract Routers (Not Loaded by Default)")
    print("-" * 80)
    try:
        from backend.core.pack_registry import PACK_DEFINITIONS

        pack_routers = set()
        for pack_key, pack_def in PACK_DEFINITIONS.items():
            if pack_def.routers:
                print(f"   {pack_key}:")
                for router in pack_def.routers:
                    print(f"      - {router} (delivery_stage={pack_def.delivery_stage})")
                    pack_routers.add(router)

        # 验证 Pack 路由不在核心路由或可选路由中
        overlap = pack_routers & (actual_core | actual_optional)
        if overlap:
            print(f"[FAIL] Pack routers overlap with kernel routers: {overlap}")
            return False
        else:
            print(f"[OK] No overlap: Pack routers are separate from kernel routers")
    except ImportError as e:
        print(f"[FAIL] Failed to import pack_registry: {e}")
        return False
    print()

    # 6. 总结
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
    success = verify_admission_control()
    sys.exit(0 if success else 1)
