# migrations/versions/

Standalone SQL migration scripts executed **outside** the Alembic migration chain.

These scripts are applied once by the deploy pipeline
(`scripts/bootstrap.py` → `postdeploy_verify.py`) **after** Alembic migrations
complete, because they may depend on the base table schema that Alembic creates.

## Naming convention

```
<NNN>_<slug>.sql
```

| File | Revises | Description |
|------|---------|-------------|
| `001_initial_schema.sql` | – | Baseline schema (tenants, users, nodes, jobs, job_attempts) |
| `002_retry_delay.sql` | 001 | Add retry_delay_seconds to jobs |
| `003_advanced_scheduling.sql` | 002 | Edge computing fields (data_locality_key, etc.) |

## Relationship with `backend/migrations/versions/` (Alembic)

- **Alembic** (`backend/migrations/versions/`) manages the authoritative Python-based
  migration chain (0001 … 0023+).  Alembic tracks its own revision history in the
  `alembic_version` table.
- **Standalone SQL** (this directory) handles schema additions that must be
  idempotent (`ADD COLUMN IF NOT EXISTS`) or executed via raw DDL outside Alembic
  (e.g., extensions, partitioning, pgvector indexes).

## Applying standalone scripts

```bash
psql $DATABASE_URL -f migrations/versions/003_advanced_scheduling.sql
```

Scripts must be idempotent. Do **not** insert rows into `alembic_version`.
