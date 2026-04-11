from __future__ import annotations

import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.control_plane.adapters.connectors_contracts import (  # noqa: F401 re-export
    ConnectorInvokeRequest,
    ConnectorInvokeResponse,
    ConnectorResponse,
    ConnectorTestRequest,
    ConnectorTestResponse,
    ConnectorUpsertRequest,
)
from backend.control_plane.adapters.connectors_helpers import (  # noqa: F401 re-export
    _build_connector_actions,
    _connector_attention_reason,
    _matches_connector_list_filters,
    _resource_schema,
    _to_response,
)
from backend.control_plane.adapters.control_events import publish_control_event
from backend.control_plane.adapters.deps import get_current_admin, get_current_user, get_redis, get_tenant_db
from backend.control_plane.adapters.jobs.models import JobCreateRequest
from backend.control_plane.adapters.jobs.submission_service import submit_job
from backend.control_plane.adapters.ui_contracts import ResourceSchemaResponse
from backend.extensions.connector_kind_registry import validate_connector_config
from backend.extensions.connector_service import ConnectorService
from backend.kernel.contracts.errors import zen
from backend.kernel.contracts.tenant_claims import require_current_user_tenant_id
from backend.models.connector import Connector
from backend.platform.logging.redaction import sanitize_sensitive_data
from backend.platform.redis.client import CHANNEL_CONNECTOR_EVENTS, RedisClient
from backend.runtime.scheduling.quota_service import check_connector_quota

from .connectors_endpoint_policy import validate_connector_endpoint
from .connectors_queries import connector_stmt_for_tenant, load_connector_for_tenant

router = APIRouter(prefix="/api/v1/connectors", tags=["connectors"])


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


@router.get("/schema", response_model=ResourceSchemaResponse)
async def get_connector_schema(
    current_user: Annotated[dict[str, object], Depends(get_current_user)],
) -> ResourceSchemaResponse:
    del current_user
    return _resource_schema()


@router.post("", response_model=ConnectorResponse)
async def upsert_connector(
    payload: ConnectorUpsertRequest,
    current_user: Annotated[dict[str, object], Depends(get_current_admin)],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    redis: Annotated[RedisClient | None, Depends(get_redis)],
) -> ConnectorResponse:
    del redis
    tenant_id = require_current_user_tenant_id(current_user)
    result = await db.execute(connector_stmt_for_tenant(tenant_id).where(Connector.connector_id == payload.connector_id))
    connector = result.scalars().first()
    now = _utcnow()

    if connector is None:
        await check_connector_quota(db, tenant_id)

    try:
        validated_config = validate_connector_config(payload.kind, payload.config)
    except ValueError as e:
        raise zen(
            "ZEN-CONN-4001",
            str(e),
            status_code=400,
            recovery_hint="Check config schema for connector kind or register the kind if it's new",
            details={"kind": payload.kind, "config": sanitize_sensitive_data(payload.config)},
        ) from e
    validated_endpoint = validate_connector_endpoint(payload.endpoint, connector_id=payload.connector_id)

    connector, action = ConnectorService.upsert(
        connector,
        tenant_id=tenant_id,
        connector_id=payload.connector_id,
        name=payload.name,
        kind=payload.kind,
        status=payload.status,
        endpoint=validated_endpoint,
        profile=payload.profile,
        config=validated_config,
        now=now,
    )
    if action == "upserted":
        db.add(connector)

    await db.flush()
    response = _to_response(connector)
    await publish_control_event(
        CHANNEL_CONNECTOR_EVENTS,
        action,
        {"connector": response.model_dump(mode="json")},
        tenant_id=tenant_id,
    )
    return response


@router.get("", response_model=list[ConnectorResponse])
async def list_connectors(
    connector_id: str | None = None,
    status: str | None = None,
    attention: str | None = None,
    *,
    current_user: Annotated[dict[str, object], Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[ConnectorResponse]:
    tenant_id = require_current_user_tenant_id(current_user)
    query = connector_stmt_for_tenant(tenant_id)
    if connector_id:
        query = query.where(Connector.connector_id == connector_id)
    result = await db.execute(query.order_by(Connector.updated_at.desc()))
    connectors = [connector for connector in result.scalars().all() if _matches_connector_list_filters(connector, status=status, attention=attention)]
    return [_to_response(connector) for connector in connectors]


@router.post("/{id}/invoke", response_model=ConnectorInvokeResponse)
async def invoke_connector(
    id: str,
    payload: ConnectorInvokeRequest,
    current_user: Annotated[dict[str, object], Depends(get_current_admin)],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    redis: Annotated[RedisClient | None, Depends(get_redis)],
) -> ConnectorInvokeResponse:
    tenant_id = require_current_user_tenant_id(current_user)
    connector = await load_connector_for_tenant(db, tenant_id=tenant_id, connector_id=id)
    if connector is None:
        raise zen(
            "ZEN-CONN-4040",
            "Connector not found",
            status_code=404,
            recovery_hint="Refresh the connectors view and retry",
            details={"connector_id": id},
        )
    if connector.status not in {"configured", "online", "healthy"}:
        raise zen(
            "ZEN-CONN-4090",
            "Connector not ready",
            status_code=409,
            recovery_hint="Test or recover the connector before invoking it",
            details={"connector_id": id, "status": connector.status},
        )
    connector.endpoint = validate_connector_endpoint(connector.endpoint, connector_id=connector.connector_id)

    submitted = await submit_job(
        JobCreateRequest(
            kind="connector.invoke",
            connector_id=connector.connector_id,
            priority=60,
            required_capabilities=["connector.invoke"],
            source="connectors.invoke",
            payload={
                "connector_id": connector.connector_id,
                "connector_kind": connector.kind,
                "action": payload.action,
                "payload": payload.payload,
            },
            lease_seconds=payload.lease_seconds,
        ),
        current_user=current_user,
        db=db,
        redis=redis,
    )
    job_id = submitted.job_id
    invoked_at = _utcnow()
    ConnectorService.mark_invoked(connector, job_id=job_id, now=invoked_at)
    await db.flush()
    response = ConnectorInvokeResponse(
        connector_id=connector.connector_id,
        accepted=True,
        job_id=job_id,
        status="pending",
        message="job queued",
    )
    await publish_control_event(
        CHANNEL_CONNECTOR_EVENTS,
        "invoked",
        {
            "connector": _to_response(connector).model_dump(mode="json"),
            "job_id": job_id,
            "connector_action": payload.action,
            "status": "pending",
        },
        tenant_id=tenant_id,
    )
    return response


@router.post("/{id}/test", response_model=ConnectorTestResponse)
async def test_connector(
    id: str,
    payload: ConnectorTestRequest,
    current_user: Annotated[dict[str, object], Depends(get_current_admin)],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    redis: Annotated[RedisClient | None, Depends(get_redis)],
) -> ConnectorTestResponse:
    del payload, redis
    tenant_id = require_current_user_tenant_id(current_user)
    connector = await load_connector_for_tenant(db, tenant_id=tenant_id, connector_id=id)
    if connector is None:
        raise zen(
            "ZEN-CONN-4040",
            "Connector not found",
            status_code=404,
            recovery_hint="Refresh the connectors view and retry",
            details={"connector_id": id},
        )

    endpoint = connector.endpoint
    ok = connector.status in {"configured", "online", "healthy"}
    message = "connector ready"

    if endpoint:
        endpoint = validate_connector_endpoint(endpoint, connector_id=connector.connector_id)
    else:
        message = "connector has no endpoint; local/manual mode"

    checked_at = _utcnow()
    ConnectorService.mark_tested(
        connector,
        ok=ok,
        status="healthy" if ok else "error",
        message=message,
        checked_at=checked_at,
    )
    await db.flush()

    response = ConnectorTestResponse(
        connector_id=connector.connector_id,
        ok=ok,
        endpoint=endpoint,
        status=connector.status,
        message=message,
        checked_at=checked_at,
    )
    await publish_control_event(
        CHANNEL_CONNECTOR_EVENTS,
        "tested",
        {
            "connector": _to_response(connector).model_dump(mode="json"),
            "ok": response.ok,
            "status": response.status,
            "message": response.message,
        },
        tenant_id=tenant_id,
    )
    return response
