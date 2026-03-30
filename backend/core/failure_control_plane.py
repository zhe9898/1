"""Failure Control Plane — node quarantine, burst detection, connector cooling, kind circuit breaking.

In-memory singleton tracker. All state is process-local and resets on restart,
which is acceptable for an MVP: the control plane is a *fast-react* layer,
not persistent policy. Persistent bans belong in the database.

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


class FailureEvent(NamedTuple):
    ts: datetime.datetime
    category: str
    job_id: str


class FailureControlPlane:
    """In-memory failure tracking for nodes, connectors, and job kinds."""

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

    async def reset_kind_circuit(self, kind: str) -> None:
        """Reset circuit to closed (called after a successful execution in half-open)."""
        async with self._lock:
            self._kind_circuit.pop(kind, None)

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
            return {"burst_detected": len(recent)}
        return {}

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
            }


# ── Module-level singleton ───────────────────────────────────────────

_instance: FailureControlPlane | None = None


def get_failure_control_plane() -> FailureControlPlane:
    """Return the process-wide FailureControlPlane singleton."""
    global _instance
    if _instance is None:
        _instance = FailureControlPlane()
    return _instance
