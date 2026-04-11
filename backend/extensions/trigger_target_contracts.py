"""Trigger target contracts for the unified trigger layer."""

from __future__ import annotations

import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class JobTriggerTarget(BaseModel):
    target_kind: Literal["job"] = "job"
    job_kind: str = Field(..., min_length=1, max_length=64)
    payload: dict[str, object] = Field(default_factory=dict)
    connector_id: str | None = None
    lease_seconds: int = Field(default=30, ge=5, le=3600)
    priority: int = Field(default=50, ge=0, le=100)
    queue_class: str | None = Field(default=None, pattern="^(realtime|interactive|batch|gpu-heavy|analytics)$")
    worker_pool: str | None = Field(default=None, pattern=r"^[a-z0-9](?:[a-z0-9._-]{0,63})$")
    source: str | None = Field(default=None, min_length=1, max_length=64)
    target_os: str | None = Field(default=None, min_length=1, max_length=64)
    target_arch: str | None = Field(default=None, min_length=1, max_length=64)
    target_executor: str | None = Field(default=None, min_length=1, max_length=64)
    required_capabilities: list[str] = Field(default_factory=list)
    target_zone: str | None = Field(default=None, min_length=1, max_length=128)
    required_cpu_cores: int | None = Field(default=None, ge=1, le=4096)
    required_memory_mb: int | None = Field(default=None, ge=1, le=8_388_608)
    required_gpu_vram_mb: int | None = Field(default=None, ge=1, le=8_388_608)
    required_storage_mb: int | None = Field(default=None, ge=1, le=134_217_728)
    timeout_seconds: int = Field(default=300, ge=5, le=86_400)
    max_retries: int = Field(default=0, ge=0, le=10)
    estimated_duration_s: int | None = Field(default=None, ge=1, le=86_400)
    data_locality_key: str | None = Field(default=None, max_length=255)
    max_network_latency_ms: int | None = Field(default=None, ge=1, le=60_000)
    prefer_cached_data: bool = False
    power_budget_watts: int | None = Field(default=None, ge=1, le=10_000)
    thermal_sensitivity: str | None = Field(default=None, pattern="^(low|normal|high)$")
    cloud_fallback_enabled: bool = False
    scheduling_strategy: str | None = Field(default=None, pattern="^(spread|binpack|locality|performance|balanced)$")
    affinity_labels: dict[str, str] = Field(default_factory=dict)
    affinity_rule: str | None = Field(default=None, pattern="^(required|preferred)$")
    anti_affinity_key: str | None = Field(default=None, max_length=128)
    parent_job_id: str | None = Field(default=None, max_length=128)
    depends_on: list[str] = Field(default_factory=list)
    gang_id: str | None = Field(default=None, max_length=128)
    batch_key: str | None = Field(default=None, max_length=128)
    preemptible: bool = True
    deadline_at: datetime.datetime | None = None
    sla_seconds: int | None = Field(default=None, ge=1, le=86_400 * 7)


class WorkflowTemplateTriggerTarget(BaseModel):
    target_kind: Literal["workflow_template"] = "workflow_template"
    template_id: str = Field(..., min_length=1, max_length=128)
    parameters: dict[str, object] = Field(default_factory=dict)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None


TriggerTargetContract = Annotated[JobTriggerTarget | WorkflowTemplateTriggerTarget, Field(discriminator="target_kind")]
