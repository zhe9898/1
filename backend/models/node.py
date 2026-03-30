from __future__ import annotations

import datetime

from sqlalchemy import JSON, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.user import Base


class Node(Base):
    """Control-plane node registration for runners and sidecars."""

    __tablename__ = "nodes"
    __table_args__ = (UniqueConstraint("tenant_id", "node_id", name="ux_nodes_tenant_node_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default", index=True)
    node_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    node_type: Mapped[str] = mapped_column(String(64), nullable=False, default="runner")
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    profile: Mapped[str] = mapped_column(String(64), nullable=False, default="go-runner")
    executor: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown")
    os: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown")
    arch: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown")
    zone: Mapped[str | None] = mapped_column(String(128), nullable=True)
    protocol_version: Mapped[str] = mapped_column(String(32), nullable=False, default="runner.v1")
    lease_version: Mapped[str] = mapped_column(String(32), nullable=False, default="job-lease.v1")
    agent_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    max_concurrency: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    cpu_cores: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    memory_mb: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    gpu_vram_mb: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    storage_mb: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    drain_status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", index=True)
    health_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    drain_until: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    auth_token_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    auth_token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    enrollment_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="online", index=True)
    capabilities: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    accepted_kinds: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    # Edge computing node attributes
    network_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bandwidth_mbps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cached_data_keys: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    power_capacity_watts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_power_watts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    thermal_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    cloud_connectivity: Mapped[str | None] = mapped_column(String(32), nullable=True)
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    registered_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=False),
        default=lambda: datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
        nullable=False,
    )
    last_seen_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=False),
        default=lambda: datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=False),
        default=lambda: datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
        onupdate=lambda: datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
        nullable=False,
    )
