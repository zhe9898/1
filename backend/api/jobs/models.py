from __future__ import annotations

import datetime

from pydantic import BaseModel, Field

from backend.api.action_contracts import ControlAction
from backend.api.ui_contracts import StatusView


class JobCreateRequest(BaseModel):
    kind: str = Field(..., min_length=1, max_length=64)
    payload: dict[str, object] = Field(default_factory=dict)
    connector_id: str | None = None
    lease_seconds: int = Field(default=30, ge=5, le=3600)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
    priority: int = Field(default=50, ge=0, le=100)
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
    source: str = Field(default="console", min_length=1, max_length=64)
    # Edge computing
    data_locality_key: str | None = Field(default=None, max_length=255)
    max_network_latency_ms: int | None = Field(default=None, ge=1, le=60_000)
    prefer_cached_data: bool = False
    power_budget_watts: int | None = Field(default=None, ge=1, le=10_000)
    thermal_sensitivity: str | None = Field(default=None, pattern="^(low|normal|high)$")
    cloud_fallback_enabled: bool = False
    # Scheduling strategy and affinity
    scheduling_strategy: str | None = Field(default=None, pattern="^(spread|binpack|locality|performance|balanced)$")
    affinity_labels: dict[str, str] = Field(default_factory=dict)
    affinity_rule: str | None = Field(default=None, pattern="^(required|preferred)$")
    anti_affinity_key: str | None = Field(default=None, max_length=128)
    # Business scheduling
    parent_job_id: str | None = Field(default=None, max_length=128)
    depends_on: list[str] = Field(default_factory=list)
    gang_id: str | None = Field(default=None, max_length=128)
    batch_key: str | None = Field(default=None, max_length=128)
    preemptible: bool = True
    deadline_at: datetime.datetime | None = None
    sla_seconds: int | None = Field(default=None, ge=1, le=86_400 * 7)


class JobPullRequest(BaseModel):
    tenant_id: str = Field(default="default", min_length=1, max_length=64)
    node_id: str = Field(..., min_length=1, max_length=128)
    limit: int = Field(default=1, ge=1, le=50)
    accepted_kinds: list[str] = Field(default_factory=list)


class JobLeaseAckRequest(BaseModel):
    tenant_id: str = Field(default="default", min_length=1, max_length=64)
    node_id: str = Field(..., min_length=1, max_length=128)
    lease_token: str = Field(..., min_length=1, max_length=64)
    attempt: int = Field(..., ge=1, le=1_000_000)
    log: str | None = None


class JobResultRequest(JobLeaseAckRequest):
    result: dict[str, object] = Field(default_factory=dict)


class JobFailRequest(JobLeaseAckRequest):
    error: str = Field(..., min_length=1)
    failure_category: str | None = Field(default=None, max_length=32)
    error_details: dict[str, object] | None = Field(default=None)


class JobProgressRequest(JobLeaseAckRequest):
    progress: int = Field(..., ge=0, le=100)
    message: str | None = Field(default=None, max_length=255)


class JobRenewRequest(JobLeaseAckRequest):
    extend_seconds: int = Field(default=30, ge=5, le=3600)


class JobActionRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=255)


class JobRequeueRequest(BaseModel):
    reset_retry_count: bool = Field(default=True)
    increase_max_retries: int | None = Field(default=None, ge=0, le=10)
    reason: str = Field(..., min_length=1, max_length=255)


class DeadLetterQueueResponse(BaseModel):
    total: int
    items: list[JobResponse]


class DeadLetterQueueStatsResponse(BaseModel):
    total_count: int
    by_category: dict[str, int]
    by_connector: dict[str, int]
    by_kind: dict[str, int]
    oldest_entry: datetime.datetime | None
    newest_entry: datetime.datetime | None


class JobResponse(BaseModel):
    job_id: str
    kind: str
    status: str
    status_view: StatusView
    node_id: str | None
    connector_id: str | None
    idempotency_key: str | None
    priority: int
    target_os: str | None
    target_arch: str | None
    target_executor: str | None
    required_capabilities: list[str]
    target_zone: str | None
    required_cpu_cores: int | None
    required_memory_mb: int | None
    required_gpu_vram_mb: int | None
    required_storage_mb: int | None
    timeout_seconds: int
    max_retries: int
    retry_count: int
    attempt_count: int
    failure_category: str | None
    estimated_duration_s: int | None
    source: str
    attempt: int
    payload: dict[str, object]
    result: dict[str, object] | None
    error_message: str | None
    lease_seconds: int
    leased_until: datetime.datetime | None
    lease_state: str
    lease_state_view: StatusView
    attention_reason: str | None
    actions: list[ControlAction] = Field(default_factory=list)
    created_at: datetime.datetime
    started_at: datetime.datetime | None
    completed_at: datetime.datetime | None
    # Edge computing
    data_locality_key: str | None = None
    max_network_latency_ms: int | None = None
    prefer_cached_data: bool = False
    power_budget_watts: int | None = None
    thermal_sensitivity: str | None = None
    cloud_fallback_enabled: bool = False
    # Scheduling strategy and affinity
    scheduling_strategy: str | None = None
    affinity_labels: dict[str, str] = Field(default_factory=dict)
    affinity_rule: str | None = None
    anti_affinity_key: str | None = None
    # Business scheduling
    parent_job_id: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    gang_id: str | None = None
    batch_key: str | None = None
    preemptible: bool = True
    deadline_at: datetime.datetime | None = None
    sla_seconds: int | None = None
    retry_at: datetime.datetime | None = None


class JobLeaseResponse(JobResponse):
    lease_token: str


class JobAttemptResponse(BaseModel):
    attempt_id: str
    job_id: str
    node_id: str
    lease_token: str
    attempt_no: int
    status: str
    status_view: StatusView
    score: int
    error_message: str | None
    result_summary: dict[str, object] | None
    created_at: datetime.datetime
    started_at: datetime.datetime | None
    completed_at: datetime.datetime | None


class JobExplainDecisionResponse(BaseModel):
    node_id: str
    eligible: bool
    eligibility_view: StatusView
    score: int | None
    reasons: list[str] = Field(default_factory=list)
    active_lease_count: int
    max_concurrency: int
    executor: str
    os: str
    arch: str
    zone: str | None
    cpu_cores: int
    memory_mb: int
    gpu_vram_mb: int
    storage_mb: int
    drain_status: str
    drain_status_view: StatusView
    reliability_score: float
    status: str
    status_view: StatusView
    last_seen_at: datetime.datetime


class JobExplainResponse(BaseModel):
    job: JobResponse
    total_nodes: int
    eligible_nodes: int
    selected_node_id: str | None
    decisions: list[JobExplainDecisionResponse] = Field(default_factory=list)


class QueueLayerStats(BaseModel):
    count: int
    oldest: datetime.datetime | None


class QueueStatsResponse(BaseModel):
    by_priority: dict[str, QueueLayerStats]
    total_pending: int


class JobPriorityUpdateRequest(BaseModel):
    priority: int = Field(..., ge=0, le=100)
    reason: str = Field(..., min_length=1, max_length=255)


class JobPriorityUpdateResponse(BaseModel):
    job_id: str
    old_priority: int
    new_priority: int
    old_layer: str
    new_layer: str
    updated_at: datetime.datetime


class JobTypeStatsItem(BaseModel):
    pending: int
    leased: int
    completed: int
    failed: int
    canceled: int


class ConcurrentLimitInfo(BaseModel):
    current: int
    max: int


class JobTypeStatsResponse(BaseModel):
    scheduled: JobTypeStatsItem
    background: JobTypeStatsItem
    concurrent_limits: dict[str, ConcurrentLimitInfo]

