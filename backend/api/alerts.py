"""Alert rules and alert history API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_admin, get_current_user, get_tenant_db
from backend.kernel.contracts.errors import zen
from backend.kernel.extensions.alert_actions import AlertActionModel, normalize_alert_action
from backend.kernel.extensions.alerting import run_alert_evaluation
from backend.models.alert import Alert, AlertRule

router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])


# 鈹€鈹€ Request / Response models 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€


class AlertRuleRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=255)
    condition: dict = Field(...)
    action: AlertActionModel
    severity: str = Field(default="warning")
    enabled: bool = Field(default=True)


class AlertRuleResponse(BaseModel):
    id: int
    name: str
    description: str | None
    condition: dict
    action: dict
    severity: str
    enabled: bool
    created_by: str
    created_at: str
    updated_at: str


class AlertResponse(BaseModel):
    id: int
    rule_id: int
    rule_name: str
    severity: str
    message: str
    details: dict
    notified: bool
    triggered_at: str
    resolved_at: str | None


def _rule_to_response(r: AlertRule) -> AlertRuleResponse:
    return AlertRuleResponse(
        id=r.id,
        name=r.name,
        description=r.description,
        condition=r.condition,
        action=r.action,
        severity=r.severity,
        enabled=r.enabled,
        created_by=r.created_by,
        created_at=r.created_at.isoformat(),
        updated_at=r.updated_at.isoformat(),
    )


def _alert_to_response(a: Alert) -> AlertResponse:
    return AlertResponse(
        id=a.id,
        rule_id=a.rule_id,
        rule_name=a.rule_name,
        severity=a.severity,
        message=a.message,
        details=a.details,
        notified=a.notified,
        triggered_at=a.triggered_at.isoformat(),
        resolved_at=a.resolved_at.isoformat() if a.resolved_at else None,
    )


# 鈹€鈹€ Alert Rules CRUD 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

VALID_SEVERITIES = {"info", "warning", "error", "critical"}
VALID_CONDITION_TYPES = {"node_offline", "job_failure_rate", "job_stuck", "quota_pct"}


def _validate_alert_rule_payload(payload: AlertRuleRequest) -> dict[str, object]:
    if payload.severity not in VALID_SEVERITIES:
        raise zen("ZEN-ALERT-4000", f"Invalid severity. Valid: {sorted(VALID_SEVERITIES)}", status_code=400)
    if payload.condition.get("type") not in VALID_CONDITION_TYPES:
        raise zen("ZEN-ALERT-4001", f"Invalid condition type. Valid: {sorted(VALID_CONDITION_TYPES)}", status_code=400)
    return normalize_alert_action(payload.action)


@router.get("/rules", response_model=list[AlertRuleResponse])
async def list_alert_rules(
    current_user: dict[str, str] = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[AlertRuleResponse]:
    result = await db.execute(select(AlertRule).where(AlertRule.tenant_id == current_user["tenant_id"]).order_by(AlertRule.created_at.desc()))
    return [_rule_to_response(r) for r in result.scalars().all()]


@router.post("/rules", response_model=AlertRuleResponse)
async def create_alert_rule(
    payload: AlertRuleRequest,
    current_user: dict[str, str] = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
) -> AlertRuleResponse:
    action = _validate_alert_rule_payload(payload)

    rule = AlertRule(
        tenant_id=current_user["tenant_id"],
        name=payload.name,
        description=payload.description,
        condition=payload.condition,
        action=action,
        severity=payload.severity,
        enabled=payload.enabled,
        created_by=current_user["username"],
    )
    db.add(rule)
    await db.flush()
    return _rule_to_response(rule)


@router.patch("/rules/{rule_id}", response_model=AlertRuleResponse)
async def update_alert_rule(
    rule_id: int,
    payload: AlertRuleRequest,
    current_user: dict[str, str] = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
) -> AlertRuleResponse:
    result = await db.execute(select(AlertRule).where(AlertRule.id == rule_id, AlertRule.tenant_id == current_user["tenant_id"]))
    rule = result.scalars().first()
    if rule is None:
        raise zen("ZEN-ALERT-4040", "Alert rule not found", status_code=404)
    action = _validate_alert_rule_payload(payload)

    rule.name = payload.name
    rule.description = payload.description
    rule.condition = payload.condition
    rule.action = action
    rule.severity = payload.severity
    rule.enabled = payload.enabled
    await db.flush()
    return _rule_to_response(rule)


@router.delete("/rules/{rule_id}")
async def delete_alert_rule(
    rule_id: int,
    current_user: dict[str, str] = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
) -> dict[str, str]:
    result = await db.execute(select(AlertRule).where(AlertRule.id == rule_id, AlertRule.tenant_id == current_user["tenant_id"]))
    rule = result.scalars().first()
    if rule is None:
        raise zen("ZEN-ALERT-4040", "Alert rule not found", status_code=404)
    await db.delete(rule)
    await db.flush()
    return {"status": "ok"}


# 鈹€鈹€ Alert History 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€


@router.get("", response_model=list[AlertResponse])
async def list_alerts(
    severity: str | None = Query(default=None),
    resolved: bool | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    current_user: dict[str, str] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[AlertResponse]:
    """List fired alerts (newest first)."""
    query = select(Alert).where(Alert.tenant_id == current_user["tenant_id"])
    if severity:
        query = query.where(Alert.severity == severity)
    if resolved is True:
        query = query.where(Alert.resolved_at.isnot(None))
    elif resolved is False:
        query = query.where(Alert.resolved_at.is_(None))
    result = await db.execute(query.order_by(desc(Alert.triggered_at)).limit(limit))
    return [_alert_to_response(a) for a in result.scalars().all()]


@router.post("/{alert_id}/resolve")
async def resolve_alert(
    alert_id: int,
    current_user: dict[str, str] = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
) -> dict[str, str]:
    """Mark an alert as resolved."""
    import datetime

    result = await db.execute(select(Alert).where(Alert.id == alert_id, Alert.tenant_id == current_user["tenant_id"]))
    alert = result.scalars().first()
    if alert is None:
        raise zen("ZEN-ALERT-4040", "Alert not found", status_code=404)
    alert.resolved_at = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    await db.flush()
    return {"status": "ok"}


@router.post("/evaluate")
async def trigger_evaluation(
    current_user: dict[str, str] = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
) -> dict[str, object]:
    """Manually trigger alert evaluation for this tenant (admin only)."""
    fired = await run_alert_evaluation(db, current_user["tenant_id"])
    return {"status": "ok", "fired": len(fired), "alerts": [_alert_to_response(a) for a in fired]}
