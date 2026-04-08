#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


def main() -> int:
    parser = argparse.ArgumentParser(description="Export gateway OpenAPI schemas by profile")
    parser.add_argument(
        "--profiles",
        nargs="+",
        default=["gateway-kernel"],
        help="Profiles to export. Current runtime surface: gateway-kernel",
    )
    args = parser.parse_args()

    try:
        from scripts.generate_contracts import export_gateway_openapi, write_metadata
    except ImportError as exc:
        logger.error("failed to import contract generator: %s", exc)
        return 1

    for profile in args.profiles:
        contract_path, docs_path = export_gateway_openapi(profile)
        logger.info("wrote %s and %s", contract_path, docs_path)

    write_metadata(args.profiles)
    return 0


if __name__ == "__main__":
    sys.exit(main())
