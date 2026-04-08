"""CLI entrypoint for governed Alembic migration execution."""

from __future__ import annotations

import argparse
import logging

from backend.platform.db.migration_governance import ordered_migration_chains
from backend.platform.db.migration_runner import run_governed_migrations


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run repository-governed Alembic migrations.")
    parser.add_argument("--chain", action="append", help="Specific migration chain key to run. May be provided multiple times.")
    parser.add_argument("--managed-only", action="store_true", help="Run only chains marked runtime-managed.")
    parser.add_argument("--all-chains", action="store_true", help="Run every migration chain in governance order.")
    parser.add_argument("--revision", default="head", help="Alembic revision target. Defaults to head.")
    parser.add_argument("--list", action="store_true", help="List known migration chains and exit.")
    return parser


def _list_chains() -> None:
    for chain in ordered_migration_chains():
        lifecycle = "runtime-managed" if chain.runtime_managed else "manual-only"
        print(f"{chain.key}\t{lifecycle}\t{chain.version_table}\t{chain.config_path}")


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.list:
        _list_chains()
        return

    if args.all_chains and args.managed_only:
        parser.error("--all-chains and --managed-only are mutually exclusive")
    if args.all_chains and args.chain:
        parser.error("--all-chains cannot be combined with --chain")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    runtime_managed_only = args.managed_only or (not args.all_chains and not args.chain)
    executed = run_governed_migrations(
        chain_keys=args.chain,
        runtime_managed_only=runtime_managed_only,
        revision=args.revision,
    )
    print("executed chains:", ", ".join(executed) if executed else "<none>")


if __name__ == "__main__":
    main()
