from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from backend.kernel.contracts.errors import zen
from backend.kernel.execution.job_type_separation import SCHEDULED_JOB_SOURCES, get_job_type, get_max_concurrent_limit
from backend.models.job import Job
from backend.platform.db.advisory_locks import acquire_transaction_advisory_locks


def _source_filter(job_type: str) -> ColumnElement[bool]:
    if job_type == "scheduled":
        return Job.source.in_(list(SCHEDULED_JOB_SOURCES))
    return ~Job.source.in_(list(SCHEDULED_JOB_SOURCES))


def _limit_error_message(*, job_type: str, current: int, limit: int, scope: str) -> str:
    scope_desc = {
        "global": "system-wide",
        "per_tenant": "for your tenant",
        "per_connector": "for this connector",
    }.get(scope, scope)
    return (
        f"Concurrent {job_type} job limit reached ({current}/{limit} {scope_desc}). "
        f"Please wait for running jobs to complete or contact administrator to increase limit."
    )


@dataclass(frozen=True, slots=True)
class ConcurrencyViolation:
    job_type: str
    scope: str
    current: int
    limit: int
    tenant_id: str
    connector_id: str | None = None

    @property
    def error_code(self) -> str:
        return {
            "global": "ZEN-JOB-4096",
            "per_tenant": "ZEN-JOB-4097",
            "per_connector": "ZEN-JOB-4098",
        }[self.scope]

    def to_http_exception(self) -> Exception:
        details: dict[str, object] = {
            "job_type": self.job_type,
            "current": self.current,
            "limit": self.limit,
        }
        if self.scope == "per_tenant":
            details["tenant_id"] = self.tenant_id
        if self.connector_id:
            details["connector_id"] = self.connector_id
        return zen(
            self.error_code,
            _limit_error_message(
                job_type=self.job_type,
                current=self.current,
                limit=self.limit,
                scope=self.scope,
            ),
            status_code=429,
            recovery_hint="Wait for running jobs to complete or contact administrator",
            details=details,
        )

    def audit_reason(self) -> str:
        if self.scope == "per_connector" and self.connector_id:
            return f"concurrency_limit:{self.scope}:{self.connector_id}"
        return f"concurrency_limit:{self.scope}"


@dataclass(slots=True)
class _TypeBudget:
    global_count: int
    tenant_count: int
    connector_counts: dict[str, int] = field(default_factory=dict)
    connector_counts_loaded: bool = False


class JobConcurrencyLeaseWindow:
    """Shared concurrency gate for submission and dispatch write paths."""

    def __init__(self, *, db: AsyncSession, tenant_id: str) -> None:
        self._db = db
        self._tenant_id = tenant_id
        self._budgets: dict[str, _TypeBudget] = {}
        self._locked_scopes: set[tuple[str, tuple[str, ...]]] = set()

    async def assert_capacity(
        self,
        *,
        job_type: str,
        connector_id: str | None = None,
    ) -> None:
        violation = await self.check_capacity(job_type=job_type, connector_id=connector_id)
        if violation is not None:
            raise violation.to_http_exception()

    async def assert_capacity_for_job(self, job: Job) -> None:
        await self.assert_capacity(job_type=get_job_type(job), connector_id=job.connector_id)

    async def check_capacity_for_job(self, job: Job) -> ConcurrencyViolation | None:
        return await self.check_capacity(job_type=get_job_type(job), connector_id=job.connector_id)

    async def check_capacity(
        self,
        *,
        job_type: str,
        connector_id: str | None = None,
    ) -> ConcurrencyViolation | None:
        budget = await self._ensure_budget(job_type=job_type, connector_id=connector_id)

        global_limit = get_max_concurrent_limit(job_type, "global")
        if budget.global_count >= global_limit:
            return ConcurrencyViolation(
                job_type=job_type,
                scope="global",
                current=budget.global_count,
                limit=global_limit,
                tenant_id=self._tenant_id,
            )

        tenant_limit = get_max_concurrent_limit(job_type, "per_tenant")
        if budget.tenant_count >= tenant_limit:
            return ConcurrencyViolation(
                job_type=job_type,
                scope="per_tenant",
                current=budget.tenant_count,
                limit=tenant_limit,
                tenant_id=self._tenant_id,
            )

        if connector_id:
            connector_limit = get_max_concurrent_limit(job_type, "per_connector")
            connector_count = int(budget.connector_counts.get(connector_id, 0) or 0)
            if connector_count >= connector_limit:
                return ConcurrencyViolation(
                    job_type=job_type,
                    scope="per_connector",
                    current=connector_count,
                    limit=connector_limit,
                    tenant_id=self._tenant_id,
                    connector_id=connector_id,
                )

        return None

    def note_lease_granted(self, job: Job) -> None:
        self.note_capacity_consumed(job_type=get_job_type(job), connector_id=job.connector_id)

    def note_capacity_consumed(self, *, job_type: str, connector_id: str | None = None) -> None:
        budget = self._budgets.get(job_type)
        if budget is None:
            return
        budget.global_count += 1
        budget.tenant_count += 1
        if connector_id:
            budget.connector_counts[connector_id] = int(budget.connector_counts.get(connector_id, 0) or 0) + 1

    async def _ensure_budget(
        self,
        *,
        job_type: str,
        connector_id: str | None,
    ) -> _TypeBudget:
        await self._ensure_scope_locks(job_type=job_type, connector_id=connector_id)
        budget = self._budgets.get(job_type)
        if budget is None:
            budget = await self._load_budget(job_type, include_connector_counts=connector_id is not None)
            self._budgets[job_type] = budget
        elif connector_id and not budget.connector_counts_loaded:
            budget.connector_counts = await self._load_connector_counts(job_type)
            budget.connector_counts_loaded = True
        return budget

    async def _ensure_scope_locks(self, *, job_type: str, connector_id: str | None) -> None:
        requested = [
            ("jobs.concurrent.global", (job_type,)),
            ("jobs.concurrent.tenant", (job_type, self._tenant_id)),
        ]
        if connector_id:
            requested.append(("jobs.concurrent.connector", (job_type, self._tenant_id, connector_id)))
        fresh = [spec for spec in requested if spec not in self._locked_scopes]
        if not fresh:
            return
        await acquire_transaction_advisory_locks(self._db, fresh)
        self._locked_scopes.update(fresh)

    async def _load_budget(self, job_type: str, *, include_connector_counts: bool) -> _TypeBudget:
        source_predicate = _source_filter(job_type)
        counts_stmt = select(
            func.public.zen70_global_leased_jobs_count(job_type).label("global_count"),
            func.count().filter(Job.tenant_id == self._tenant_id).label("tenant_count"),
        ).where(
            Job.status == "leased",
            source_predicate,
        )
        try:
            counts_row = (await self._db.execute(counts_stmt)).one()
        except (SQLAlchemyError, OSError, RuntimeError, TypeError, ValueError) as exc:
            raise zen(
                "ZEN-JOB-5032",
                "Global concurrent limit function is unavailable",
                status_code=503,
                recovery_hint="Apply the latest Alembic migrations before accepting job submissions",
                details={"job_type": job_type, "migration_required": True},
            ) from exc
        connector_counts = await self._load_connector_counts(job_type) if include_connector_counts else {}
        return _TypeBudget(
            global_count=int(counts_row.global_count or 0),
            tenant_count=int(counts_row.tenant_count or 0),
            connector_counts=connector_counts,
            connector_counts_loaded=include_connector_counts,
        )

    async def _load_connector_counts(self, job_type: str) -> dict[str, int]:
        connector_stmt = (
            select(Job.connector_id, func.count())
            .where(
                Job.status == "leased",
                Job.tenant_id == self._tenant_id,
                Job.connector_id.is_not(None),
                _source_filter(job_type),
            )
            .group_by(Job.connector_id)
        )
        try:
            connector_rows = (await self._db.execute(connector_stmt)).all()
        except (SQLAlchemyError, OSError, RuntimeError, TypeError, ValueError) as exc:
            raise zen(
                "ZEN-JOB-5032",
                "Global concurrent limit function is unavailable",
                status_code=503,
                recovery_hint="Apply the latest Alembic migrations before accepting job submissions",
                details={"job_type": job_type, "migration_required": True},
            ) from exc
        return {str(connector_id): int(count or 0) for connector_id, count in connector_rows if connector_id}


def build_job_concurrency_window(*, db: AsyncSession, tenant_id: str) -> JobConcurrencyLeaseWindow:
    return JobConcurrencyLeaseWindow(db=db, tenant_id=tenant_id)


def export_job_concurrency_contract() -> dict[str, object]:
    return {
        "service": "backend.kernel.execution.job_concurrency_service.JobConcurrencyLeaseWindow",
        "entrypoints": [
            "assert_capacity",
            "assert_capacity_for_job",
            "check_capacity",
            "check_capacity_for_job",
            "note_lease_granted",
        ],
        "scopes": ["global", "per_tenant", "per_connector"],
        "lock_namespaces": [
            "jobs.concurrent.global",
            "jobs.concurrent.tenant",
            "jobs.concurrent.connector",
        ],
        "active_status": "leased",
        "count_source": "public.zen70_global_leased_jobs_count(text)",
    }
