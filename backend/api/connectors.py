from __future__ import annotations

import datetime
import os
import uuid
from urllib.parse import urlparse

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from backend.api.action_contracts import ControlAction, ControlActionField
from backend.api.control_events import publish_control_event
from backend.api.deps import get_current_user, get_redis, get_tenant_db
from backend.api.ui_contracts import FormFieldOption, FormFieldSchema, FormSectionSchema, ResourceSchemaResponse, StatusView
from backend.core.connector_kind_registry import validate_connector_config
from backend.core.control_plane_state import connector_status_view
from backend.core.errors import zen
from backend.core.quota import check_connector_quota
from backend.core.gateway_profile import DEFAULT_PRODUCT_NAME, normalize_gateway_profile, to_public_profile
from backend.core.redis_client import CHANNEL_CONNECTOR_EVENTS, RedisClient
from backend.models.connector import Connector
from backend.models.job import Job
from backend.models.job_log import JobLog

router = APIRouter(prefix="/api/v1/connectors", tags=["connectors"])


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def _connector_stmt_for_tenant(tenant_id: str) -> Select[tuple[Connector]]:
    return select(Connector).where(Connector.tenant_id == tenant_id)


class ConnectorUpsertRequest(BaseModel):
    connector_id: str = Field(..., min_length=1, max_length=128)
    name: str = Field(..., min_length=1, max_length=128)
    kind: str = Field(..., min_length=1, max_length=64)
    status: str = "configured"
    endpoint: str | None = None
    profile: str = "manual"
    config: dict[str, object] = Field(default_factory=dict)


class ConnectorResponse(BaseModel):
    connector_id: str
    name: str
    kind: str
    status: str
    status_view: StatusView
    endpoint: str | None
    profile: str
    config: dict[str, object]
    last_test_ok: bool | None
    last_test_status: str | None
    last_test_message: str | None
    last_test_at: datetime.datetime | None
    last_invoke_status: str | None
    last_invoke_message: str | None
    last_invoke_job_id: str | None
    last_invoke_at: datetime.datetime | None
    attention_reason: str | None
    actions: list[ControlAction] = Field(default_factory=list)
    created_at: datetime.datetime
    updated_at: datetime.datetime


class ConnectorInvokeRequest(BaseModel):
    action: str = Field(..., min_length=1, max_length=64)
    payload: dict[str, object] = Field(default_factory=dict)
    lease_seconds: int = Field(default=30, ge=5, le=3600)


class ConnectorInvokeResponse(BaseModel):
    connector_id: str
    accepted: bool
    job_id: str
    status: str
    message: str


class ConnectorTestRequest(BaseModel):
    timeout_ms: int = Field(default=1500, ge=100, le=10000)


class ConnectorTestResponse(BaseModel):
    connector_id: str
    ok: bool
    endpoint: str | None
    status: str
    message: str
    checked_at: datetime.datetime


def _to_response(connector: Connector) -> ConnectorResponse:
    status = connector.status
    return ConnectorResponse(
        connector_id=connector.connector_id,
        name=connector.name,
        kind=connector.kind,
        status=connector.status,
        status_view=StatusView(**connector_status_view(status)),
        endpoint=connector.endpoint,
        profile=connector.profile,
        config=dict(connector.config or {}),
        last_test_ok=connector.last_test_ok,
        last_test_status=connector.last_test_status,
        last_test_message=connector.last_test_message,
        last_test_at=connector.last_test_at,
        last_invoke_status=connector.last_invoke_status,
        last_invoke_message=connector.last_invoke_message,
        last_invoke_job_id=connector.last_invoke_job_id,
        last_invoke_at=connector.last_invoke_at,
        attention_reason=_connector_attention_reason(connector),
        actions=_build_connector_actions(connector),
        created_at=connector.created_at,
        updated_at=connector.updated_at,
    )


def _build_connector_actions(connector: Connector) -> list[ControlAction]:
    status = connector.status
    can_test = status in {"configured", "online", "healthy", "auth_required", "error"}
    can_invoke = status in {"configured", "online", "healthy"}
    return [
        ControlAction(
            key="test",
            label="Test",
            endpoint=f"/v1/connectors/{connector.connector_id}/test",
            method="POST",
            enabled=can_test,
            requires_admin=False,
            reason=None if can_test else f"Connector status {status} does not allow testing",
            confirmation=None,
            fields=[
                ControlActionField(
                    key="timeout_ms",
                    label="Timeout (ms)",
                    input_type="number",
                    required=False,
                    placeholder="1500",
                    value=1500,
                )
            ],
        ),
        ControlAction(
            key="invoke",
            label="Invoke",
            endpoint=f"/v1/connectors/{connector.connector_id}/invoke",
            method="POST",
            enabled=can_invoke,
            requires_admin=False,
            reason=None if can_invoke else f"Connector status {status} is not ready for invoke",
            confirmation=None,
            fields=[
                ControlActionField(
                    key="action",
                    label="Action",
                    input_type="text",
                    required=True,
                    placeholder="ping",
                    value="ping",
                ),
                ControlActionField(
                    key="payload",
                    label="Payload JSON",
                    input_type="json",
                    required=False,
                    placeholder='{"from":"gateway-console"}',
                    value='{"from":"gateway-console"}',
                ),
                ControlActionField(
                    key="lease_seconds",
                    label="Lease Seconds",
                    input_type="number",
                    required=False,
                    placeholder="30",
                    value=30,
                ),
            ],
        ),
    ]


def _connector_attention_reason(connector: Connector) -> str | None:
    if connector.status in {"error", "auth_required"}:
        return connector.last_test_message or f"connector status={connector.status}"
    if connector.last_test_ok is False:
        return connector.last_test_message or "latest connector test failed"
    if connector.status == "configured":
        return "connector configured but not yet confirmed healthy"
    return None


def _matches_connector_list_filters(
    connector: Connector,
    *,
    status: str | None,
    attention: str | None,
) -> bool:
    if status and connector_status_view(connector.status)["key"] != status:
        return False
    if attention == "attention" and _connector_attention_reason(connector) is None:
        return False
    return True


def _resource_schema() -> ResourceSchemaResponse:
    runtime_profile = normalize_gateway_profile(os.getenv("GATEWAY_PROFILE", "gateway-kernel"))
    return ResourceSchemaResponse(
        product=DEFAULT_PRODUCT_NAME,
        profile=to_public_profile(runtime_profile),
        runtime_profile=runtime_profile,
        resource="connectors",
        title="Connectors",
        description="Register integrations, persist health, and run test or invoke actions from backend-owned contracts.",
        empty_state="No connectors match the current view.",
        policies={
            "ui_mode": "backend-driven",
            "resource_mode": "integration-center",
            "list_query_filters": {
                "connector_id": "exact",
                "status": "status-view",
                "attention": "derived-flag",
            },
            "submit_encoding": {"config": "json"},
        },
        submit_action=ControlAction(
            key="upsert",
            label="Save Connector",
            endpoint="/v1/connectors",
            method="POST",
            enabled=True,
            requires_admin=False,
            reason=None,
            confirmation=None,
            fields=[],
        ),
        sections=[
            FormSectionSchema(
                id="identity",
                label="Identity",
                description="Connector identity and integration type are backend-owned contract fields.",
                fields=[
                    FormFieldSchema(key="connector_id", label="Connector ID", required=True, placeholder="connector-id"),
                    FormFieldSchema(key="name", label="Name", required=True, placeholder="Kitchen Relay"),
                    FormFieldSchema(
                        key="kind",
                        label="Kind",
                        input_type="select",
                        required=True,
                        value="runner",
                        options=[
                            FormFieldOption(value="runner", label="Runner"),
                            FormFieldOption(value="http", label="HTTP"),
                            FormFieldOption(value="mqtt", label="MQTT"),
                            FormFieldOption(value="manual", label="Manual"),
                        ],
                    ),
                ],
            ),
            FormSectionSchema(
                id="runtime",
                label="Runtime",
                description="Profile, endpoint, status, and config shape are provided by the backend contract.",
                fields=[
                    FormFieldSchema(
                        key="status",
                        label="Status",
                        input_type="select",
                        required=True,
                        value="configured",
                        options=[
                            FormFieldOption(value="configured", label="Configured"),
                            FormFieldOption(value="online", label="Online"),
                            FormFieldOption(value="healthy", label="Healthy"),
                            FormFieldOption(value="auth_required", label="Auth Required"),
                            FormFieldOption(value="error", label="Error"),
                        ],
                    ),
                    FormFieldSchema(key="endpoint", label="Endpoint", input_type="url", placeholder="https://endpoint.example"),
                    FormFieldSchema(key="profile", label="Profile", value="manual", placeholder="manual"),
                    FormFieldSchema(
                        key="config",
                        label="Config JSON",
                        input_type="json",
                        required=False,
                        placeholder='{"headers":{"x-api-key":"..."} }',
                        value="{}",
                    ),
                ],
            ),
        ],
    )


@router.get("/schema", response_model=ResourceSchemaResponse)
async def get_connector_schema(
    current_user: dict[str, object] = Depends(get_current_user),
) -> ResourceSchemaResponse:
    del current_user
    return _resource_schema()


@router.post("", response_model=ConnectorResponse)
async def upsert_connector(
    payload: ConnectorUpsertRequest,
    current_user: dict[str, object] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
) -> ConnectorResponse:
    tenant_id = str(current_user.get("tenant_id") or "default")
    result = await db.execute(_connector_stmt_for_tenant(tenant_id).where(Connector.connector_id == payload.connector_id))
    connector = result.scalars().first()
    now = _utcnow()

    # Enforce connector quota (only on new connectors)
    if connector is None:
        await check_connector_quota(db, tenant_id)

    # Validate config against registered schema
    try:
        validated_config = validate_connector_config(payload.kind, payload.config)
    except ValueError as e:
        raise zen(
            "ZEN-CONN-4001",
            str(e),
            status_code=400,
            recovery_hint="Check config schema for connector kind or register the kind if it's new",
            details={"kind": payload.kind, "config": payload.config},
        ) from e

    if connector is None:
        connector = Connector(
            tenant_id=tenant_id,
            connector_id=payload.connector_id,
            name=payload.name,
            kind=payload.kind,
            status=payload.status,
            endpoint=payload.endpoint,
            profile=payload.profile,
            config=validated_config,
            last_test_ok=None,
            last_test_status=None,
            last_test_message=None,
            last_test_at=None,
            last_invoke_status=None,
            last_invoke_message=None,
            last_invoke_job_id=None,
            last_invoke_at=None,
            created_at=now,
            updated_at=now,
        )
        db.add(connector)
    else:
        connector.name = payload.name
        connector.kind = payload.kind
        connector.status = payload.status
        connector.endpoint = payload.endpoint
        connector.profile = payload.profile
        connector.config = validated_config
        connector.updated_at = now

    await db.flush()
    response = _to_response(connector)
    await publish_control_event(
        redis,
        CHANNEL_CONNECTOR_EVENTS,
        "upserted",
        {"connector": response.model_dump(mode="json")},
    )
    return response


@router.get("", response_model=list[ConnectorResponse])
async def list_connectors(
    connector_id: str | None = None,
    status: str | None = None,
    attention: str | None = None,
    current_user: dict[str, object] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[ConnectorResponse]:
    tenant_id = str(current_user.get("tenant_id") or "default")
    query = _connector_stmt_for_tenant(tenant_id)
    if connector_id:
        query = query.where(Connector.connector_id == connector_id)
    result = await db.execute(query.order_by(Connector.updated_at.desc()))
    connectors = [connector for connector in result.scalars().all() if _matches_connector_list_filters(connector, status=status, attention=attention)]
    return [_to_response(connector) for connector in connectors]


@router.post("/{id}/invoke", response_model=ConnectorInvokeResponse)
async def invoke_connector(
    id: str,
    payload: ConnectorInvokeRequest,
    current_user: dict[str, object] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
) -> ConnectorInvokeResponse:
    actor = str(current_user.get("sub") or current_user.get("username") or "unknown")
    tenant_id = str(current_user.get("tenant_id") or "default")
    result = await db.execute(_connector_stmt_for_tenant(tenant_id).where(Connector.connector_id == id))
    connector = result.scalars().first()
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

    job_id = str(uuid.uuid4())
    job = Job(
        tenant_id=connector.tenant_id,
        job_id=job_id,
        kind="connector.invoke",
        status="pending",
        connector_id=connector.connector_id,
        priority=60,
        required_capabilities=["connector.invoke"],
        source="connectors.invoke",
        created_by=actor,
        payload={
            "connector_id": connector.connector_id,
            "connector_kind": connector.kind,
            "action": payload.action,
            "payload": payload.payload,
        },
        lease_seconds=payload.lease_seconds,
    )
    db.add(job)
    db.add(
        JobLog(
            tenant_id=connector.tenant_id,
            job_id=job_id,
            level="info",
            message=f"invoke accepted for connector={connector.connector_id} action={payload.action}",
        )
    )
    connector.last_invoke_status = "pending"
    connector.last_invoke_message = "job queued"
    connector.last_invoke_job_id = job_id
    connector.last_invoke_at = _utcnow()
    connector.updated_at = connector.last_invoke_at
    await db.flush()
    response = ConnectorInvokeResponse(
        connector_id=connector.connector_id,
        accepted=True,
        job_id=job_id,
        status="pending",
        message="job queued",
    )
    await publish_control_event(
        redis,
        CHANNEL_CONNECTOR_EVENTS,
        "invoked",
        {
            "connector": _to_response(connector).model_dump(mode="json"),
            "job_id": job_id,
            "action": payload.action,
            "status": "pending",
        },
    )
    return response


@router.post("/{id}/test", response_model=ConnectorTestResponse)
async def test_connector(
    id: str,
    payload: ConnectorTestRequest,
    current_user: dict[str, object] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
) -> ConnectorTestResponse:
    tenant_id = str(current_user.get("tenant_id") or "default")
    del payload
    result = await db.execute(_connector_stmt_for_tenant(tenant_id).where(Connector.connector_id == id))
    connector = result.scalars().first()
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
        parsed = urlparse(endpoint)
        if parsed.scheme not in {"http", "https", "mqtt", "tcp"} or not parsed.netloc:
            ok = False
            message = "invalid endpoint format"
    else:
        message = "connector has no endpoint; local/manual mode"

    checked_at = _utcnow()
    connector.last_test_ok = ok
    connector.last_test_status = "healthy" if ok else "error"
    connector.last_test_message = message
    connector.last_test_at = checked_at
    connector.updated_at = checked_at
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
        redis,
        CHANNEL_CONNECTOR_EVENTS,
        "tested",
        {
            "connector": _to_response(connector).model_dump(mode="json"),
            "ok": response.ok,
            "status": response.status,
            "message": response.message,
        },
    )
    return response
