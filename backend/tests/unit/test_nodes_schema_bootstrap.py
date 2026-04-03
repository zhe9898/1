from __future__ import annotations

import datetime

from backend.api.nodes_schema import _build_bootstrap_commands
from backend.models.node import Node


def _node() -> Node:
    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    return Node(
        tenant_id="tenant-a",
        node_id="node-a",
        name="node-a",
        node_type="runner",
        address=None,
        profile="go-runner",
        executor="go-native",
        os="linux",
        arch="amd64",
        zone="lab-a",
        protocol_version="runner.v1",
        lease_version="job-lease.v1",
        max_concurrency=1,
        cpu_cores=4,
        memory_mb=4096,
        gpu_vram_mb=0,
        storage_mb=1024,
        drain_status="active",
        auth_token_version=1,
        enrollment_status="pending",
        status="offline",
        capabilities=[],
        accepted_kinds=[],
        worker_pools=[],
        cached_data_keys=[],
        metadata_json={},
        registered_at=now,
        last_seen_at=now,
        updated_at=now,
    )


def test_bootstrap_commands_do_not_embed_token_by_default(monkeypatch) -> None:
    monkeypatch.delenv("NODE_BOOTSTRAP_EMBED_TOKEN", raising=False)
    commands = _build_bootstrap_commands(_node(), "zkn_secret_token")

    assert "zkn_secret_token" not in commands["powershell"]
    assert "zkn_secret_token" not in commands["unix"]
    assert "<paste-one-time-node-token-here>" in commands["powershell"]


def test_bootstrap_commands_embed_token_when_opted_in(monkeypatch) -> None:
    monkeypatch.setenv("NODE_BOOTSTRAP_EMBED_TOKEN", "true")
    commands = _build_bootstrap_commands(_node(), "zkn_secret_token")

    assert "zkn_secret_token" in commands["powershell"]
    assert "zkn_secret_token" in commands["unix"]
