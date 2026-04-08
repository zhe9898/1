"""Alert evaluation engine.

Architecture: RULES stay in gateway, EXECUTION moves out.

Gateway responsibilities (this module):
  - Evaluate alert conditions against DB state
  - Persist fired Alert records
  - Enqueue alert.notify Jobs for execution by runner-agent

Runner-agent responsibilities (via Job kind="alert.notify"):
  - POST to webhook URLs
  - Send emails
  - Send push notifications
  - Any other I/O side-effects

The gateway NEVER makes outbound HTTP calls for alert notification.
"""

from __future__ import annotations

import datetime
import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.kernel.extensions.alert_actions import normalize_alert_action
from backend.models.alert import Alert, AlertRule
from backend.models.job import Job
from backend.models.node import Node

logger = logging.getLogger("zen70.alerts")


async def _fire_alert(
    db: AsyncSession,
    rule: AlertRule,
    message: str,
    details: dict,
    *,
    dedup_window_s: int,
) -> Alert | None:
    """Persist Alert and dispatch execution Job.

    The gateway stores the alert fact and enqueues a Job.
    The runner-agent executes the actual notification (webhook, etc.).
    """
    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    dedup_cutoff = now - datetime.timedelta(seconds=max(dedup_window_s, 0))
    recent_result = await db.execute(
        select(Alert.id).where(
            Alert.tenant_id == rule.tenant_id,
            Alert.rule_id == rule.id,
            Alert.message == message,
            Alert.triggered_at >= dedup_cutoff,
        )
    )
    if recent_result.first() is not None:
        logger.debug(
            "Suppress duplicate alert within dedup window: tenant=%s rule=%s window=%ss",
            rule.tenant_id,
            rule.id,
            dedup_window_s,
        )
        return None

    alert = Alert(
        tenant_id=rule.tenant_id,
        rule_id=rule.id,
        rule_name=rule.name,
        severity=rule.severity,
        message=message,
        details=details,
        notified=False,
        triggered_at=now,
    )
    db.add(alert)
    await db.flush()

    try:
        action = normalize_alert_action(rule.action or {"type": "log"})
    except ValueError as exc:
        logger.error("Invalid alert action for rule %s: %s", rule.id, exc)
        action = {"type": "log"}
    action_type = action.get("type", "log")

    if action_type == "log":
        # Log-only: no runner job needed, just record
        logger.warning("ALERT [%s] %s: %s", rule.severity.upper(), rule.name, message)
        alert.notified = True

    elif action_type == "webhook":
        # Execution moves out: dispatch a Job for the runner to execute
        await _enqueue_notify_job(db, alert=alert, action=action)
        # notified=False until runner reports completion

    else:
        logger.warning("Unknown alert action type '%s' for rule %s", action_type, rule.id)

    return alert


async def _enqueue_notify_job(
    db: AsyncSession,
    alert: Alert,
    action: dict,
) -> Job:
    """Enqueue an alert.notify Job to be executed by runner-agent.

    Job kind: alert.notify
    Payload contract:
      alert_id:   int
      rule_name:  str
      severity:   str
      message:    str
      details:    dict
      action:     dict  (webhook url, method, headers, etc.)
      triggered_at: ISO str

    The runner-agent owns the outbound HTTP call.
    Gateway only defines what needs to happen.
    """
    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    job = Job(
        tenant_id=alert.tenant_id,
        job_id=uuid.uuid4().hex,
        kind="alert.notify",
        source="gateway-alerting",
        status="pending",
        priority=80,  # high – alerts need timely delivery
        max_retries=3,
        payload={
            "alert_id": alert.id,
            "rule_name": alert.rule_name,
            "severity": alert.severity,
            "message": alert.message,
            "details": alert.details,
            "action": action,
            "triggered_at": alert.triggered_at.isoformat(),
        },
        created_at=now,
        updated_at=now,
    )
    db.add(job)
    await db.flush()
    logger.debug(
        "Enqueued alert.notify job %s for alert %s (rule=%s)",
        job.job_id,
        alert.id,
        alert.rule_name,
    )
    return job


async def evaluate_node_offline_rules(db: AsyncSession, tenant_id: str) -> list[Alert]:
    """Evaluate node_offline conditions.

    RULE LOGIC (gateway): query last_seen_at vs threshold
    EXECUTION (runner):   webhook POST dispatched as Job
    """
    rules_result = await db.execute(
        select(AlertRule).where(
            AlertRule.tenant_id == tenant_id,
            AlertRule.enabled.is_(True),
        )
    )
    rules = [r for r in rules_result.scalars().all() if r.condition.get("type") == "node_offline"]

    fired: list[Alert] = []
    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)

    for rule in rules:
        threshold_s = int(rule.condition.get("threshold_seconds", 120))
        dedup_window_s = int(rule.condition.get("dedup_window_seconds", 300))
        cutoff = now - datetime.timedelta(seconds=threshold_s)

        nodes_result = await db.execute(
            select(Node).where(
                Node.tenant_id == tenant_id,
                Node.enrollment_status == "approved",
                Node.last_seen_at < cutoff,
                Node.status != "offline",
            )
        )
        for node in nodes_result.scalars().all():
            lag = int((now - node.last_seen_at).total_seconds())
            alert = await _fire_alert(
                db,
                rule,
                f"Node '{node.name}' ({node.node_id}) missed heartbeat for {lag}s",
                {"node_id": node.node_id, "last_seen_at": node.last_seen_at.isoformat(), "lag_seconds": lag},
                dedup_window_s=dedup_window_s,
            )
            if alert is not None:
                fired.append(alert)

    return fired


async def evaluate_job_failure_rules(db: AsyncSession, tenant_id: str) -> list[Alert]:
    """Evaluate job_failure_rate conditions.

    RULE LOGIC (gateway): count failed/total in rolling window
    EXECUTION (runner):   webhook POST dispatched as Job
    """
    rules_result = await db.execute(
        select(AlertRule).where(
            AlertRule.tenant_id == tenant_id,
            AlertRule.enabled.is_(True),
        )
    )
    rules = [r for r in rules_result.scalars().all() if r.condition.get("type") == "job_failure_rate"]

    fired: list[Alert] = []
    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)

    for rule in rules:
        window_m = int(rule.condition.get("window_minutes", 60))
        threshold_pct = float(rule.condition.get("threshold_pct", 20))
        dedup_window_s = int(rule.condition.get("dedup_window_seconds", 300))
        since = now - datetime.timedelta(minutes=window_m)

        total = (
            await db.execute(
                select(func.count()).where(
                    Job.tenant_id == tenant_id,
                    Job.created_at >= since,
                    Job.status.in_(("completed", "failed")),
                )
            )
        ).scalar() or 0

        if total == 0:
            continue

        failed = (
            await db.execute(
                select(func.count()).where(
                    Job.tenant_id == tenant_id,
                    Job.created_at >= since,
                    Job.status == "failed",
                )
            )
        ).scalar() or 0

        pct = failed / total * 100
        if pct >= threshold_pct:
            alert = await _fire_alert(
                db,
                rule,
                f"Job failure rate {pct:.1f}% exceeds {threshold_pct}% (window={window_m}m)",
                {"failed": failed, "total": total, "pct": round(pct, 1), "window_minutes": window_m},
                dedup_window_s=dedup_window_s,
            )
            if alert is not None:
                fired.append(alert)

    return fired


async def run_alert_evaluation(db: AsyncSession, tenant_id: str) -> list[Alert]:
    """Run all evaluators for a tenant.

    Call from:
    - POST /api/v1/alerts/evaluate  (manual, admin)
    - A background sentinel job that polls on schedule
    Gateway does NOT run a perpetual asyncio loop for this.
    """
    fired: list[Alert] = []
    fired.extend(await evaluate_node_offline_rules(db, tenant_id))
    fired.extend(await evaluate_job_failure_rules(db, tenant_id))
    return fired
