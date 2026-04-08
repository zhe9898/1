"""Tests for failure control plane governance audit trail.

Covers:
- Governance events are recorded for quarantine, cooling, circuit, burst
- Timeline querying with filters
- Stats aggregation
- release_quarantine governance event
- circuit_reset governance event
- pending_audit_events buffer draining
"""

from __future__ import annotations

import asyncio
import datetime

from backend.kernel.scheduling.failure_control_plane import (
    BURST_THRESHOLD,
    CONNECTOR_COOLING_THRESHOLD,
    KIND_CIRCUIT_THRESHOLD,
    NODE_QUARANTINE_THRESHOLD,
    FailureControlPlane,
)


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def _run(coro):
    """Run an async coroutine synchronously."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestGovernanceNodeQuarantine:
    def test_quarantine_emits_governance_event(self) -> None:
        """NODE_QUARANTINE_THRESHOLD consecutive failures → governance event."""
        fcp = FailureControlPlane()
        now = _utcnow()
        for i in range(NODE_QUARANTINE_THRESHOLD):
            actions = _run(
                fcp.record_failure(
                    node_id="n1",
                    job_id=f"j-{i}",
                    category="runtime",
                    now=now,
                )
            )
        assert "node_quarantined" in actions
        # Check governance timeline
        timeline = _run(fcp.governance_timeline())
        assert len(timeline) >= 1
        quarantine_events = [e for e in timeline if e["event_type"] == "quarantine"]
        assert len(quarantine_events) == 1
        assert quarantine_events[0]["resource_type"] == "node"
        assert quarantine_events[0]["resource_id"] == "n1"
        assert "consecutive_failures" in quarantine_events[0]["details"]

    def test_manual_release_emits_governance_event(self) -> None:
        """Manual quarantine release records a governance event."""
        fcp = FailureControlPlane()
        now = _utcnow()
        # Quarantine the node first
        for i in range(NODE_QUARANTINE_THRESHOLD):
            _run(fcp.record_failure(node_id="n1", job_id=f"j-{i}", category="runtime", now=now))
        # Release
        released = _run(fcp.release_quarantine("n1"))
        assert released is True
        timeline = _run(fcp.governance_timeline())
        release_events = [e for e in timeline if e["event_type"] == "release"]
        assert len(release_events) == 1
        assert release_events[0]["details"]["trigger"] == "manual_admin"


class TestGovernanceConnectorCooling:
    def test_cooling_emits_governance_event(self) -> None:
        """Exceeding connector cooling threshold emits governance event."""
        fcp = FailureControlPlane()
        now = _utcnow()
        for i in range(CONNECTOR_COOLING_THRESHOLD):
            _run(
                fcp.record_failure(
                    node_id="n1",
                    job_id=f"j-{i}",
                    category="connector",
                    connector_id="c1",
                    now=now + datetime.timedelta(seconds=i),
                )
            )
        timeline = _run(fcp.governance_timeline())
        cooling_events = [e for e in timeline if e["event_type"] == "cooling"]
        assert len(cooling_events) == 1
        assert cooling_events[0]["resource_type"] == "connector"
        assert cooling_events[0]["resource_id"] == "c1"


class TestGovernanceKindCircuit:
    def test_circuit_open_emits_governance_event(self) -> None:
        """Kind circuit breaker opening emits governance event."""
        fcp = FailureControlPlane()
        now = _utcnow()
        for i in range(KIND_CIRCUIT_THRESHOLD):
            _run(
                fcp.record_failure(
                    node_id=f"n-{i % 3}",
                    job_id=f"j-{i}",
                    category="kind",
                    kind="http.request",
                    now=now + datetime.timedelta(seconds=i),
                )
            )
        timeline = _run(fcp.governance_timeline())
        circuit_events = [e for e in timeline if e["event_type"] == "circuit_open"]
        assert len(circuit_events) == 1
        assert circuit_events[0]["resource_id"] == "http.request"

    def test_circuit_reset_emits_governance_event(self) -> None:
        """Resetting a kind circuit records a governance event."""
        fcp = FailureControlPlane()
        now = _utcnow()
        for i in range(KIND_CIRCUIT_THRESHOLD):
            _run(
                fcp.record_failure(
                    node_id="n1",
                    job_id=f"j-{i}",
                    category="kind",
                    kind="http.request",
                    now=now + datetime.timedelta(seconds=i),
                )
            )
        _run(fcp.reset_kind_circuit("http.request"))
        timeline = _run(fcp.governance_timeline())
        reset_events = [e for e in timeline if e["event_type"] == "circuit_reset"]
        assert len(reset_events) == 1
        assert reset_events[0]["details"]["previous_state"] == "open"


class TestGovernanceBurst:
    def test_burst_emits_governance_event(self) -> None:
        """Global burst detection emits governance event."""
        fcp = FailureControlPlane()
        now = _utcnow()
        # Need to spread across nodes to avoid quarantine blocking burst
        for i in range(BURST_THRESHOLD):
            _run(
                fcp.record_failure(
                    node_id=f"n-{i}",  # unique nodes to avoid quarantine
                    job_id=f"j-{i}",
                    category="runtime",
                    now=now + datetime.timedelta(seconds=i),
                )
            )
        timeline = _run(fcp.governance_timeline())
        burst_events = [e for e in timeline if e["event_type"] == "burst"]
        assert len(burst_events) >= 1


class TestGovernanceTimeline:
    def test_timeline_filtering_by_event_type(self) -> None:
        """Timeline can be filtered by event_type."""
        fcp = FailureControlPlane()
        now = _utcnow()
        # Create quarantine event
        for i in range(NODE_QUARANTINE_THRESHOLD):
            _run(fcp.record_failure(node_id="n1", job_id=f"j-{i}", category="runtime", now=now))
        # Create cooling event
        for i in range(CONNECTOR_COOLING_THRESHOLD):
            _run(
                fcp.record_failure(
                    node_id=f"n-{i + 10}",
                    job_id=f"j-c-{i}",
                    category="connector",
                    connector_id="c1",
                    now=now + datetime.timedelta(seconds=i),
                )
            )

        all_events = _run(fcp.governance_timeline())
        assert len(all_events) >= 2

        quarantine_only = _run(fcp.governance_timeline(event_type="quarantine"))
        for e in quarantine_only:
            assert e["event_type"] == "quarantine"

        cooling_only = _run(fcp.governance_timeline(event_type="cooling"))
        for e in cooling_only:
            assert e["event_type"] == "cooling"

    def test_timeline_filtering_by_resource_id(self) -> None:
        """Timeline can be filtered by resource_id."""
        fcp = FailureControlPlane()
        now = _utcnow()
        for i in range(NODE_QUARANTINE_THRESHOLD):
            _run(fcp.record_failure(node_id="n1", job_id=f"j-{i}", category="runtime", now=now))
        for i in range(NODE_QUARANTINE_THRESHOLD):
            _run(
                fcp.record_failure(
                    node_id="n2",
                    job_id=f"j2-{i}",
                    category="runtime",
                    now=now + datetime.timedelta(minutes=10),
                )
            )

        n1_events = _run(fcp.governance_timeline(resource_id="n1"))
        for e in n1_events:
            assert e["resource_id"] == "n1"

    def test_timeline_since_filter(self) -> None:
        """Timeline can be filtered by since timestamp."""
        fcp = FailureControlPlane()
        now = _utcnow()
        old = now - datetime.timedelta(hours=2)
        # Old quarantine
        for i in range(NODE_QUARANTINE_THRESHOLD):
            _run(fcp.record_failure(node_id="old-n", job_id=f"old-{i}", category="runtime", now=old))
        # Recent quarantine
        for i in range(NODE_QUARANTINE_THRESHOLD):
            _run(fcp.record_failure(node_id="new-n", job_id=f"new-{i}", category="runtime", now=now))

        recent = _run(fcp.governance_timeline(since=now - datetime.timedelta(minutes=5)))
        assert all(e["resource_id"] != "old-n" for e in recent)

    def test_timeline_limit(self) -> None:
        """Timeline respects the limit parameter."""
        fcp = FailureControlPlane()
        now = _utcnow()
        # Create multiple events
        for n in range(5):
            for i in range(NODE_QUARANTINE_THRESHOLD):
                _run(
                    fcp.record_failure(
                        node_id=f"n-{n}",
                        job_id=f"j-{n}-{i}",
                        category="runtime",
                        now=now + datetime.timedelta(seconds=n * 10 + i),
                    )
                )
        _all_events = _run(fcp.governance_timeline())  # noqa: F841
        limited = _run(fcp.governance_timeline(limit=2))
        assert len(limited) <= 2

    def test_timeline_reverse_chronological(self) -> None:
        """Timeline returns events newest-first."""
        fcp = FailureControlPlane()
        now = _utcnow()
        for n in range(3):
            for i in range(NODE_QUARANTINE_THRESHOLD):
                _run(
                    fcp.record_failure(
                        node_id=f"n-{n}",
                        job_id=f"j-{n}-{i}",
                        category="runtime",
                        now=now + datetime.timedelta(minutes=n),
                    )
                )
        events = _run(fcp.governance_timeline())
        if len(events) >= 2:
            # First (newest) should be >= second
            assert events[0]["ts"] >= events[1]["ts"]


class TestGovernanceStats:
    def test_stats_counts_event_types(self) -> None:
        """Stats correctly aggregate event type counts."""
        fcp = FailureControlPlane()
        now = _utcnow()
        for i in range(NODE_QUARANTINE_THRESHOLD):
            _run(fcp.record_failure(node_id="n1", job_id=f"j-{i}", category="runtime", now=now))

        stats = _run(fcp.governance_stats(now=now))
        assert stats["total_events"] >= 1
        assert stats["event_type_counts"].get("quarantine", 0) >= 1
        assert "last_hour" in stats


class TestPendingAuditEvents:
    def test_pending_events_buffered(self) -> None:
        """State transitions buffer audit events for DB emission."""
        fcp = FailureControlPlane()
        now = _utcnow()
        for i in range(NODE_QUARANTINE_THRESHOLD):
            _run(fcp.record_failure(node_id="n1", job_id=f"j-{i}", category="runtime", now=now))

        events = fcp.pending_audit_events
        assert len(events) >= 1
        assert events[0]["action"] == "fcp.quarantine"

    def test_pending_events_drain_clears(self) -> None:
        """Draining pending_audit_events clears the buffer."""
        fcp = FailureControlPlane()
        now = _utcnow()
        for i in range(NODE_QUARANTINE_THRESHOLD):
            _run(fcp.record_failure(node_id="n1", job_id=f"j-{i}", category="runtime", now=now))

        events = fcp.pending_audit_events
        assert len(events) >= 1
        # Second drain should be empty
        events2 = fcp.pending_audit_events
        assert len(events2) == 0

    def test_snapshot_includes_governance_count(self) -> None:
        """Snapshot response includes governance event count."""
        fcp = FailureControlPlane()
        now = _utcnow()
        for i in range(NODE_QUARANTINE_THRESHOLD):
            _run(fcp.record_failure(node_id="n1", job_id=f"j-{i}", category="runtime", now=now))

        snapshot = _run(fcp.snapshot(now=now))
        assert "governance_event_count" in snapshot
        assert snapshot["governance_event_count"] >= 1
