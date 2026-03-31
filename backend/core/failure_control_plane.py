"""Failure Control Plane — node quarantine, burst detection, connector cooling, kind circuit breaking.

In-memory singleton tracker with governance audit trail. All control state
is process-local and resets on restart, which is acceptable for an MVP:
the control plane is a *fast-react* layer, not persistent policy.
Persistent bans belong in the database.

Governance additions (v2):
- Every state transition (quarantine, cooling, circuit, burst) is recorded
  in an in-memory timeline deque with structured metadata.
- ``governance_timeline()`` returns the full or filtered event history.
- ``governance_stats()`` returns aggregate governance KPIs.
- Callers (dispatch/lifecycle) can emit to the DB AuditLog table via the
  ``pending_audit_events`` property — flushed by the API layer after commit.

Thread-safety: all mutations go through ``asyncio.Lock`` so the FastAPI
event-loop can safely read/write concurrently.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
from collections import defaultdict, deque
from typing import NamedTuple

logger = logging.getLogger(__name__)

# ── Thresholds (overridable via env in future) ───────────────────────

NODE_QUARANTINE_THRESHOLD = 5  # consecutive failures before quarantine
NODE_QUARANTINE_DURATION_S = 300  # 5 min quarantine window

CONNECTOR_COOLING_THRESHOLD = 10  # failures inside window → cooling
CONNECTOR_COOLING_WINDOW_S = 300  # 5 min sliding window
CONNECTOR_COOLING_DURATION_S = 120  # 2 min cool-off

KIND_CIRCUIT_THRESHOLD = 15  # failures inside window → circuit open
KIND_CIRCUIT_WINDOW_S = 300  # 5 min sliding window
KIND_CIRCUIT_OPEN_DURATION_S = 60  # 1 min open state before half-open

BURST_WINDOW_S = 300  # global burst detection window
BURST_THRESHOLD = 20  # failures in window to trigger burst alert

# Governance timeline capacity
_GOVERNANCE_TIMELINE_MAX = 2000


class FailureEvent(NamedTuple):
    ts: datetime.datetime
    category: str
    job_id: str


class GovernanceEvent(NamedTuple):
    """Structured audit record for topology governance."""

    ts: datetime.datetime
    event_type: str  # quarantine, release, cooling, circuit_open, circuit_reset, burst
    resource_type: str  # node, connector, kind, global
    resource_id: str
    details: dict[str, object]


class FailureControlPlane:
    """In-memory failure tracking for nodes, connectors, and job kinds.

    v2: includes a governance timeline for audit/compliance and a
    ``pending_audit_events`` buffer for DB-level audit log emission.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()

        # ── Node quarantine ──────────────────────────────────────────
        self._node_consecutive: dict[str, int] = defaultdict(int)
        self._quarantine_until: dict[str, datetime.datetime] = {}

        # ── Connector cooling ────────────────────────────────────────
        self._connector_events: dict[str, deque[FailureEvent]] = defaultdict(
            lambda: deque(maxlen=200)
        )
        self._connector_cool_until: dict[str, datetime.datetime] = {}

        # ── Kind circuit breaker ─────────────────────────────────────
        self._kind_events: dict[str, deque[FailureEvent]] = defaultdict(
            lambda: deque(maxlen=200)
        )
        self._kind_circuit: dict[str, tuple[str, datetime.datetime]] = {}
        # state: "closed" | "open" | "half-open"

        # ── Global burst detection ───────────────────────────────────
        self._global_events: deque[FailureEvent] = deque(maxlen=500)

        # ── Governance timeline ──────────────────────────────────────
        self._governance_timeline: deque[GovernanceEvent] = deque(
            maxlen=_GOVERNANCE_TIMELINE_MAX
        )
        # Buffer for DB-level audit entries. Callers drain after commit.
        self._pending_audit_events: list[dict[str, object]] = []

    def _record_governance(
        self,
        event_type: str,
        resource_type: str,
        resource_id: str,
        now: datetime.datetime,
        details: dict[str, object] | None = None,
        *,
        actor: str = "system",
    ) -> None:
        """Append a governance event to the in-memory timeline and pending buffer."""
        merged_details = {**(details or {}), "actor": actor}
        evt = GovernanceEvent(
            ts=now,
            event_type=event_type,
            resource_type=resource_type,
            resource_id=resource_id,
            details=merged_details,
        )
        self._governance_timeline.append(evt)
        self._pending_audit_events.append({
            "action": f"fcp.{event_type}",
            "resource_type": resource_type,
            "resource_id": resource_id,
            "actor": actor,
            "details": {
                "event_type": event_type,
                "resource_id": resource_id,
                **merged_details,
            },
        })

    @property
    def pending_audit_events(self) -> list[dict[str, object]]:
        """Drain and return buffered audit entries for DB commit."""
        events = list(self._pending_audit_events)
        self._pending_audit_events.clear()
        return events

    # ── Record a failure ─────────────────────────────────────────────

    async def record_failure(
        self,
        *,
        node_id: str,
        job_id: str,
        category: str,
        connector_id: str | None = None,
        kind: str | None = None,
        now: datetime.datetime,
    ) -> dict[str, object]:
        """Record a failure event and return actions taken.

        Returns dict with possible keys:
          node_quarantined, connector_cooled, kind_circuit_opened, burst_detected
        """
        async with self._lock:
            actions: dict[str, object] = {}
            evt = FailureEvent(ts=now, category=category, job_id=job_id)

            actions.update(self._update_node(node_id, now))
            if connector_id:
                actions.update(self._update_connector(connector_id, evt, now))
            if kind:
                actions.update(self._update_kind(kind, evt, now))
            actions.update(self._update_burst(evt, now))

            return actions

    async def record_success(self, *, node_id: str, now: datetime.datetime) -> None:
        """Reset consecutive failure counter on success."""
        async with self._lock:
            self._node_consecutive[node_id] = 0

    # ── Node quarantine ──────────────────────────────────────────────

    def _update_node(
        self, node_id: str, now: datetime.datetime
    ) -> dict[str, object]:
        self._node_consecutive[node_id] += 1
        count = self._node_consecutive[node_id]
        if count >= NODE_QUARANTINE_THRESHOLD:
            until = now + datetime.timedelta(seconds=NODE_QUARANTINE_DURATION_S)
            self._quarantine_until[node_id] = until
            self._node_consecutive[node_id] = 0
            logger.warning(
                "node %s quarantined until %s after %d consecutive failures",
                node_id,
                until.isoformat(),
                count,
            )
            self._record_governance(
                "quarantine",
                "node",
                node_id,
                now,
                {
                    "until": until.isoformat(),
                    "consecutive_failures": count,
                    "duration_s": NODE_QUARANTINE_DURATION_S,
                },
            )
            return {"node_quarantined": until.isoformat()}
        return {}

    async def is_node_quarantined(
        self, node_id: str, *, now: datetime.datetime
    ) -> bool:
        """Check if node is currently in quarantine."""
        async with self._lock:
            until = self._quarantine_until.get(node_id)
            if until is None:
                return False
            if now >= until:
                del self._quarantine_until[node_id]
                return False
            return True

    # ── Connector cooling ────────────────────────────────────────────

    def _update_connector(
        self,
        connector_id: str,
        evt: FailureEvent,
        now: datetime.datetime,
    ) -> dict[str, object]:
        q = self._connector_events[connector_id]
        q.append(evt)
        cutoff = now - datetime.timedelta(seconds=CONNECTOR_COOLING_WINDOW_S)
        recent = [e for e in q if e.ts >= cutoff]
        if len(recent) >= CONNECTOR_COOLING_THRESHOLD:
            until = now + datetime.timedelta(seconds=CONNECTOR_COOLING_DURATION_S)
            self._connector_cool_until[connector_id] = until
            logger.warning(
                "connector %s cooled until %s (%d failures in window)",
                connector_id,
                until.isoformat(),
                len(recent),
            )
            self._record_governance(
                "cooling",
                "connector",
                connector_id,
                now,
                {
                    "until": until.isoformat(),
                    "failures_in_window": len(recent),
                    "duration_s": CONNECTOR_COOLING_DURATION_S,
                },
            )
            return {"connector_cooled": until.isoformat()}
        return {}

    async def is_connector_cooling(
        self, connector_id: str, *, now: datetime.datetime
    ) -> bool:
        async with self._lock:
            until = self._connector_cool_until.get(connector_id)
            if until is None:
                return False
            if now >= until:
                del self._connector_cool_until[connector_id]
                return False
            return True

    # ── Kind circuit breaker ─────────────────────────────────────────

    def _update_kind(
        self,
        kind: str,
        evt: FailureEvent,
        now: datetime.datetime,
    ) -> dict[str, object]:
        q = self._kind_events[kind]
        q.append(evt)
        cutoff = now - datetime.timedelta(seconds=KIND_CIRCUIT_WINDOW_S)
        recent = [e for e in q if e.ts >= cutoff]

        state, since = self._kind_circuit.get(kind, ("closed", now))

        if state == "closed" and len(recent) >= KIND_CIRCUIT_THRESHOLD:
            self._kind_circuit[kind] = ("open", now)
            logger.warning(
                "kind '%s' circuit OPEN (%d failures in window)", kind, len(recent)
            )
            self._record_governance(
                "circuit_open",
                "kind",
                kind,
                now,
                {"failures_in_window": len(recent), "open_duration_s": KIND_CIRCUIT_OPEN_DURATION_S},
            )
            return {"kind_circuit_opened": kind}

        if state == "open":
            elapsed = (now - since).total_seconds()
            if elapsed >= KIND_CIRCUIT_OPEN_DURATION_S:
                self._kind_circuit[kind] = ("half-open", now)

        return {}

    async def get_kind_circuit_state(self, kind: str, *, now: datetime.datetime) -> str:
        """Return circuit state: 'closed', 'open', or 'half-open'."""
        async with self._lock:
            if kind not in self._kind_circuit:
                return "closed"
            state, since = self._kind_circuit[kind]
            if state == "open":
                if (now - since).total_seconds() >= KIND_CIRCUIT_OPEN_DURATION_S:
                    self._kind_circuit[kind] = ("half-open", now)
                    return "half-open"
            if state == "half-open":
                # After one successful execution the caller should reset
                pass
            return state

    async def reset_kind_circuit(self, kind: str, *, actor: str = "system") -> None:
        """Reset circuit to closed (called after a successful execution in half-open)."""
        async with self._lock:
            prev = self._kind_circuit.pop(kind, None)
            if prev:
                now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
                self._record_governance(
                    "circuit_reset",
                    "kind",
                    kind,
                    now,
                    {"previous_state": prev[0]},
                    actor=actor,
                )

    # ── Global burst detection ───────────────────────────────────────

    def _update_burst(
        self, evt: FailureEvent, now: datetime.datetime
    ) -> dict[str, object]:
        self._global_events.append(evt)
        cutoff = now - datetime.timedelta(seconds=BURST_WINDOW_S)
        recent = [e for e in self._global_events if e.ts >= cutoff]
        if len(recent) >= BURST_THRESHOLD:
            logger.error(
                "FAILURE BURST detected: %d failures in %ds window",
                len(recent),
                BURST_WINDOW_S,
            )
            self._record_governance(
                "burst",
                "global",
                "system",
                now,
                {"failures_in_window": len(recent), "window_s": BURST_WINDOW_S},
            )
            return {"burst_detected": len(recent)}
        return {}

    # ── Burst query (synchronous, lock-free for hot path) ────────────

    def is_in_burst(self, *, now: datetime.datetime) -> bool:
        """Quick check if we're in a failure burst. Lock-free read for dispatch hot path."""
        cutoff = now - datetime.timedelta(seconds=BURST_WINDOW_S)
        recent = sum(1 for e in self._global_events if e.ts >= cutoff)
        return recent >= BURST_THRESHOLD

    # ── Admin: manual quarantine release ─────────────────────────────

    async def release_quarantine(self, node_id: str, *, actor: str = "admin") -> bool:
        """Manually release a node from quarantine. Returns True if node was quarantined."""
        async with self._lock:
            if node_id in self._quarantine_until:
                del self._quarantine_until[node_id]
                self._node_consecutive[node_id] = 0
                logger.info("node %s manually released from quarantine by %s", node_id, actor)
                now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
                self._record_governance(
                    "release",
                    "node",
                    node_id,
                    now,
                    {"trigger": "manual_admin"},
                    actor=actor,
                )
                return True
            return False

    # ── Diagnostics ──────────────────────────────────────────────────

    async def snapshot(self, *, now: datetime.datetime) -> dict[str, object]:
        """Return a diagnostic snapshot of all control plane state."""
        async with self._lock:
            quarantined = {
                nid: until.isoformat()
                for nid, until in self._quarantine_until.items()
                if until > now
            }
            cooled = {
                cid: until.isoformat()
                for cid, until in self._connector_cool_until.items()
                if until > now
            }
            circuits = {
                k: state for k, (state, _) in self._kind_circuit.items()
            }
            return {
                "quarantined_nodes": quarantined,
                "cooled_connectors": cooled,
                "kind_circuits": circuits,
                "global_recent_failures": len(self._global_events),
                "governance_event_count": len(self._governance_timeline),
            }

    async def governance_timeline(
        self,
        *,
        event_type: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        since: datetime.datetime | None = None,
        limit: int = 200,
    ) -> list[dict[str, object]]:
        """Query the governance event timeline with optional filters.

        Returns events in reverse chronological order (newest first).
        """
        async with self._lock:
            events = list(self._governance_timeline)

        # Apply filters
        if since is not None:
            events = [e for e in events if e.ts >= since]
        if event_type is not None:
            events = [e for e in events if e.event_type == event_type]
        if resource_type is not None:
            events = [e for e in events if e.resource_type == resource_type]
        if resource_id is not None:
            events = [e for e in events if e.resource_id == resource_id]

        # Reverse chronological, limited
        events.reverse()
        events = events[:limit]

        return [
            {
                "ts": e.ts.isoformat(),
                "event_type": e.event_type,
                "resource_type": e.resource_type,
                "resource_id": e.resource_id,
                "details": e.details,
            }
            for e in events
        ]

    async def governance_stats(self, *, now: datetime.datetime) -> dict[str, object]:
        """Return aggregate governance KPIs for observability dashboards."""
        async with self._lock:
            events = list(self._governance_timeline)

        # Count event types
        type_counts: dict[str, int] = defaultdict(int)
        for e in events:
            type_counts[e.event_type] += 1

        # Recent window (last hour)
        hour_ago = now - datetime.timedelta(hours=1)
        recent = [e for e in events if e.ts >= hour_ago]
        recent_type_counts: dict[str, int] = defaultdict(int)
        for e in recent:
            recent_type_counts[e.event_type] += 1

        return {
            "total_events": len(events),
            "event_type_counts": dict(type_counts),
            "last_hour": {
                "total": len(recent),
                "by_type": dict(recent_type_counts),
            },
        }


# ── Module-level singleton ───────────────────────────────────────────

_instance: FailureControlPlane | None = None


def get_failure_control_plane() -> FailureControlPlane:
    """Return the process-wide FailureControlPlane singleton."""
    global _instance
    if _instance is None:
        _instance = FailureControlPlane()
    return _instance
