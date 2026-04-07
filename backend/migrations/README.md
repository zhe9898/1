# Application Migration Chain

`backend/migrations/versions/` is the application-schema Alembic chain for
model-backed control-plane evolution.

## Governance

- New model-backed schema work must land here.
- Cross-stream overlap with `backend/alembic/versions/` is treated as legacy
  debt and is explicitly tracked in
  `backend/core/migration_governance.py`.
- Any newly introduced overlap outside the approved manifest fails unit tests.
- This chain writes its own Alembic version state to
  `alembic_version_application`; it must never share a version table with the
  legacy chain.

## Relationship With `backend/alembic/versions/`

- `backend/alembic/versions/` remains in the repo for historical lineage and
  legacy bootstrap/device-memory migrations.
- It is not allowed to silently take ownership of new application tables.
- The canonical owner for overlapping control-plane tables is the application
  chain unless the governance manifest says otherwise.
- Runtime deployment executes both governed chains in order through
  `python -m backend.scripts.migrate --managed-only`.
- The application chain contains the guarded overlap migrations and the final
  `0026_dual_chain_reconciliation` fence, so legacy-first databases can now be
  brought forward without duplicate-table failures.
