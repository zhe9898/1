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
- Runtime deployment currently executes only chains marked `runtime_managed`
  through `python -m backend.scripts.migrate --managed-only`; this application
  chain stays manual until historical overlap reconciliation is complete.
