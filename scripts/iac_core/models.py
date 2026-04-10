"""
iac_core.models — system.yaml 配置结构的 TypedDict 定义（ADR 0009 合规）。

职责:
  1. 为 IDE / mypy / pyright 提供静态类型提示
  2. 文档化 system.yaml 所有合法字段与嵌套结构
  3. 与 lint.py Tier 1 校验保持一一对应

ADR 0009 铁律:
  - 前后端 TypedDict / TypeScript Types 必须由契约驱动
  - 严禁 Any 类型裸奔
  - 绝不允许结构漂移
"""

from __future__ import annotations

from typing import TypedDict

# ===========================================================================
# 服务层
# ===========================================================================


class BuildConfig(TypedDict, total=False):
    """docker build 配置。"""

    context: str
    dockerfile: str
    target: str
    args: dict[str, str]


class HealthcheckConfig(TypedDict, total=False):
    """docker healthcheck 配置。"""

    test: list[str] | str
    interval: str
    timeout: str
    retries: int
    start_period: str


class UlimitsNofile(TypedDict, total=False):
    """nofile ulimit 软硬限制。"""

    soft: int
    hard: int


class UlimitsConfig(TypedDict, total=False):
    """ulimits 配置（法典 §3.3: 核心服务 ≥ 65536）。"""

    nofile: UlimitsNofile


class ResourceLimits(TypedDict, total=False):
    """deploy.resources.limits。"""

    cpus: str
    memory: str


class ResourceConfig(TypedDict, total=False):
    """deploy.resources。"""

    limits: ResourceLimits
    reservations: ResourceLimits


class DeployConfig(TypedDict, total=False):
    """deploy 块。"""

    resources: ResourceConfig
    replicas: int


class SecurityConfig(TypedDict, total=False):
    """安全基线配置（法典 §3.4）。"""

    apply_baseline: bool
    user: str


class LoggingDriverOptions(TypedDict, total=False):
    """日志驱动选项。"""

    max_size: str  # 法典 §2.5: 日志轮转防爆盘
    max_file: str
    tag: str


class LoggingConfig(TypedDict, total=False):
    """服务级日志配置（法典 §2.5）。"""

    driver: str
    options: LoggingDriverOptions


class HostBinaryBuildConfig(TypedDict, total=False):
    """runtime=host binary materialization plan."""

    type: str
    source_dir: str
    package: str
    output: str
    env: dict[str, str]
    trimpath: bool
    ldflags: str


class HostEntrypointConfig(TypedDict, total=False):
    """Declarative host-runtime process entrypoint."""

    type: str
    python: str
    module: str
    script: str
    path: str
    args: list[str]
    build: HostBinaryBuildConfig


class GatewayServeConfig(TypedDict, total=False):
    """Structured gateway serve semantics compiled into a host entrypoint."""

    engine: str
    app: str
    host: str
    port: int
    workers: int
    graceful_shutdown_seconds: int


class ServiceDef(TypedDict, total=False):
    """
    单个服务定义。

    Tier 1 必填（已启用服务）:
      - image 或 build（二选一）
      - container_name
      - networks（至少一个）
      - restart（unless-stopped | always）

    Tier 2 由 policy 引擎校验:
      - ulimits（核心服务 nofile ≥ 65536）
      - oom_score_adj（核心服务 -999）
      - read_only + tmpfs 配套
      - networks 不含 frontend_net（数据库/缓存）

    Tier 3 建议:
      - healthcheck / stop_grace_period / deploy.resources.limits / logging
    """

    enabled: bool
    runtime: str
    image: str
    build: BuildConfig
    serve: GatewayServeConfig
    entrypoint: HostEntrypointConfig
    container_name: str
    restart: str
    restart_sec: int
    networks: list[str]
    ports: list[str]
    volumes: list[str]
    environment: dict[str, str]
    extra_hosts: list[str]
    environment_file: str
    command: str | list[str]
    depends_on: list[str] | dict[str, dict[str, str]]
    healthcheck: HealthcheckConfig
    stop_grace_period: str
    deploy: DeployConfig
    security: SecurityConfig
    ulimits: UlimitsConfig
    oom_score_adj: int
    read_only: bool
    tmpfs: list[str]
    logging: LoggingConfig
    user: str
    group: str
    working_dir: str
    description: str
    port: int
    caddy_path: str
    after: str | list[str]
    cap_drop: list[str]
    cap_add: list[str]


# ===========================================================================
# 网络层
# ===========================================================================


class NetworkPlaneDef(TypedDict, total=False):
    """单个网络平面定义。"""

    driver: str
    internal: bool  # 法典 §3.3: backend_net 强制 internal: true


class NetworkConfig(TypedDict, total=False):
    """
    network 顶级配置。

    Tier 1 必填:
      - domain
      - planes.backend_net.internal == true
    """

    domain: str
    tunnel_enabled: bool
    planes: dict[str, NetworkPlaneDef]


# ===========================================================================
# 能力声明层
# ===========================================================================


class StorageCapability(TypedDict, total=False):
    """存储能力声明。"""

    media_path: str


class GpuCapability(TypedDict, total=False):
    """GPU 能力声明（法典 §1.2: 硬件以能力抽象）。"""

    enabled: bool
    device_ids: list[str]


class AgentCapability(TypedDict, total=False):
    """Local LLM Agent 插件（ADR 0008）。"""

    enabled: bool
    model_path: str


class CapabilitiesConfig(TypedDict, total=False):
    """capabilities 顶级配置。"""

    storage: StorageCapability
    gpu: GpuCapability
    agent: AgentCapability


# ===========================================================================
# 哨兵配置层
# ===========================================================================


class SentinelConfig(TypedDict, total=False):
    """
    sentinel 顶级配置。

    探针职责（法典 §3.2）: 滑动窗口检测 + UUID 三重交叉核验。
    """

    mount_container_map: dict[str, str]
    watch_targets: dict[str, list[str]]
    switch_container_map: dict[str, str]
    switch_service_ports: dict[str, int]
    models_path: str


# ===========================================================================
# 部署/拓扑层
# ===========================================================================


class DeploymentConfig(TypedDict, total=False):
    """deployment 顶级配置。"""

    profile: str  # nano | lite | standard | full


class TopologyConfig(TypedDict, total=False):
    """topology 顶级配置（ADR 0012 §4）。"""

    mode: str  # standalone | appliance | swarm | hybrid


# ===========================================================================
# 备份层
# ===========================================================================


class BackupConfig(TypedDict, total=False):
    """backup 顶级配置（法典 §3.7: 灾备级 GC）。"""

    enabled: bool
    s3_endpoint: str
    s3_bucket: str
    retention_days: int


# ===========================================================================
# 密钥层
# ===========================================================================


class SecretsConfig(TypedDict, total=False):
    """secrets 顶级配置（法典 §3.4: 双轨 JWT 轮转）。"""

    tunnel_token: str


# ===========================================================================
# 注册表层
# ===========================================================================


class RegistryConfig(TypedDict, total=False):
    """registry 顶级配置（法典 §1.3: 供应链自主）。"""

    enabled: bool
    url: str


class RedisRuntimeStateContract(TypedDict, total=False):
    pattern: str
    match: str
    role: str
    authoritative: bool
    decision_gate: bool
    description: str


class RuntimeAuthorityBoundaryContract(TypedDict, total=False):
    layer: str
    owner: str
    authority: str
    non_authority_roles: list[str]


class ControlPlanePersonaContract(TypedDict, total=False):
    key: str
    label: str
    description: str
    default_executor_contract: str
    allowed_executor_contracts: list[str]
    default_node_types: list[str]
    default_platforms: list[str]


class CanonicalExecutorContract(TypedDict, total=False):
    description: str
    supported_workload_kinds: list[str]
    requires_gpu: bool
    min_memory_mb: int
    min_cpu_cores: int
    max_concurrency_hint: int
    stability_tier: str


class RuntimeContractsConfig(TypedDict, total=False):
    control_plane_event_channels: list[str]
    browser_realtime_event_channels: list[str]
    internal_coordination_channels: list[str]
    authoritative_redis_runtime_state_allowed: bool
    redis_ephemeral_runtime_state: list[RedisRuntimeStateContract]
    runtime_authority_boundaries: list[RuntimeAuthorityBoundaryContract]
    control_plane_personas: list[ControlPlanePersonaContract]
    persona_to_default_executor_contract: dict[str, str]
    canonical_executor_contracts: dict[str, CanonicalExecutorContract]
    workload_kinds: list[str]


# ===========================================================================
# 根配置
# ===========================================================================


class SystemConfig(TypedDict, total=False):
    """
    system.yaml 根配置 — IaC 唯一事实来源。

    所有用户输入 → system.yaml → compiler → 产物。
    安装器绝不直接操作 .env 或 docker-compose.yml。
    """

    version: float | str
    config_version: int
    services: dict[str, ServiceDef]
    network: NetworkConfig
    networks: dict[str, NetworkPlaneDef]
    capabilities: CapabilitiesConfig
    sentinel: SentinelConfig
    deployment: DeploymentConfig
    topology: TopologyConfig
    backup: BackupConfig
    secrets: SecretsConfig
    registry: RegistryConfig
    runtime_contracts: RuntimeContractsConfig
