from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from backend.api.deps import get_current_user, get_tenant_db
from backend.api.evaluations_helpers import (  # noqa: F401 – re-export
    EvaluationCreateRequest,
    EvaluationResponse,
    _build_evaluation_actions,
    _resource_schema,
    _to_response,
)
from backend.api.ui_contracts import ResourceSchemaResponse
from backend.kernel.contracts.errors import zen
from backend.models.software_evaluation import SoftwareEvaluation

router = APIRouter(prefix="/api/v1/evaluations", tags=["evaluations"])


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def _eval_stmt_for_tenant(tenant_id: str) -> Select[tuple[SoftwareEvaluation]]:
    return select(SoftwareEvaluation).where(SoftwareEvaluation.tenant_id == tenant_id)


@router.get("/schema", response_model=ResourceSchemaResponse)
async def get_evaluation_schema(
    current_user: dict[str, object] = Depends(get_current_user),
) -> ResourceSchemaResponse:
    del current_user
    return _resource_schema()


@router.post("", response_model=EvaluationResponse, status_code=201)
async def create_evaluation(
    payload: EvaluationCreateRequest,
    current_user: dict[str, object] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> EvaluationResponse:
    tenant_id = str(current_user.get("tenant_id") or "default")
    evaluator = str(current_user.get("sub") or current_user.get("username") or "unknown")

    existing = await db.execute(_eval_stmt_for_tenant(tenant_id).where(SoftwareEvaluation.evaluation_id == payload.evaluation_id))
    if existing.scalars().first() is not None:
        raise zen(
            "ZEN-EVAL-4090",
            f"Evaluation '{payload.evaluation_id}' already exists",
            status_code=409,
            recovery_hint="Use a unique evaluation_id or update the existing record",
            details={"evaluation_id": payload.evaluation_id},
        )

    evaluation = SoftwareEvaluation(
        tenant_id=tenant_id,
        evaluation_id=payload.evaluation_id,
        software_id=payload.software_id,
        branch=payload.branch,
        rating=payload.rating,
        category=payload.category,
        comment=payload.comment,
        evaluator=evaluator,
        status="submitted",
    )
    db.add(evaluation)
    await db.commit()
    await db.refresh(evaluation)
    return _to_response(evaluation)


@router.get("", response_model=list[EvaluationResponse])
async def list_evaluations(
    software_id: str | None = None,
    branch: str | None = None,
    category: str | None = None,
    status: str | None = None,
    current_user: dict[str, object] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[EvaluationResponse]:
    tenant_id = str(current_user.get("tenant_id") or "default")
    query = _eval_stmt_for_tenant(tenant_id)
    if software_id:
        query = query.where(SoftwareEvaluation.software_id == software_id)
    if branch:
        query = query.where(SoftwareEvaluation.branch == branch)
    if category:
        query = query.where(SoftwareEvaluation.category == category)
    if status:
        query = query.where(SoftwareEvaluation.status == status)
    result = await db.execute(query.order_by(SoftwareEvaluation.created_at.desc()))
    return [_to_response(ev) for ev in result.scalars().all()]


@router.get("/{evaluation_id}", response_model=EvaluationResponse)
async def get_evaluation(
    evaluation_id: str,
    current_user: dict[str, object] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> EvaluationResponse:
    tenant_id = str(current_user.get("tenant_id") or "default")
    result = await db.execute(_eval_stmt_for_tenant(tenant_id).where(SoftwareEvaluation.evaluation_id == evaluation_id))
    evaluation = result.scalars().first()
    if evaluation is None:
        raise zen(
            "ZEN-EVAL-4040",
            f"Evaluation '{evaluation_id}' not found",
            status_code=404,
            recovery_hint="Verify the evaluation_id and try again",
            details={"evaluation_id": evaluation_id},
        )
    return _to_response(evaluation)


@router.delete("/{evaluation_id}", status_code=204)
async def delete_evaluation(
    evaluation_id: str,
    current_user: dict[str, object] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> None:
    tenant_id = str(current_user.get("tenant_id") or "default")
    result = await db.execute(_eval_stmt_for_tenant(tenant_id).where(SoftwareEvaluation.evaluation_id == evaluation_id))
    evaluation = result.scalars().first()
    if evaluation is None:
        raise zen(
            "ZEN-EVAL-4041",
            f"Evaluation '{evaluation_id}' not found",
            status_code=404,
            recovery_hint="Verify the evaluation_id and try again",
            details={"evaluation_id": evaluation_id},
        )
    if evaluation.status not in {"submitted", "rejected"}:
        raise zen(
            "ZEN-EVAL-4090",
            f"Evaluation '{evaluation_id}' cannot be deleted while status is '{evaluation.status}'",
            status_code=409,
            recovery_hint="Only evaluations in 'submitted' or 'rejected' status can be deleted",
            details={
                "evaluation_id": evaluation_id,
                "status": evaluation.status,
                "allowed_statuses": ["submitted", "rejected"],
            },
        )
    await db.delete(evaluation)
    await db.commit()
