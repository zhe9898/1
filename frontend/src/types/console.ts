import type { ControlAction, StatusView } from "@/types/controlPlane";

export interface GatewayPackInfo {
  pack_key: string;
  label: string;
  category: string;
  description: string;
  delivery_stage: string;
  selected: boolean;
  inherited: boolean;
  services: string[];
  router_names: string[];
  capability_keys: string[];
  selector_hints: string[];
  deployment_boundary: string;
  runtime_owner: string;
  status_view: StatusView;
}

export interface GatewayProfileInfo {
  product: string;
  profile: string;
  runtime_profile: string;
  router_names: string[];
  console_route_names?: string[];
  capability_keys?: string[];
  requested_pack_keys?: string[];
  resolved_pack_keys?: string[];
  packs?: GatewayPackInfo[];
  cluster_enabled: boolean;
}

export interface ConsoleMenuItem {
  route_name: string;
  route_path: string;
  label: string;
  endpoint: string;
  enabled: boolean;
  requires_admin: boolean;
  reason: string | null;
}

export interface ConsoleMenuResponse {
  product: string;
  profile: string;
  runtime_profile: string;
  items: ConsoleMenuItem[];
}

export interface ConsoleRouteTarget {
  route_path: string;
  query: Record<string, string>;
}

export interface ConsoleSummaryCard {
  key: string;
  kicker: string;
  title: string;
  value: number;
  badge: string;
  detail: string;
  tone: string;
  tone_view: StatusView;
  route: ConsoleRouteTarget | null;
}

export interface OverviewBucket {
  total: number;
  active: number;
  pending: number;
  running: number;
  completed: number;
  failed: number;
  cancelled: number;
  degraded: number;
  offline: number;
  rejected: number;
  attention: number;
  stale: number;
  high_priority_backlog: number;
}

export interface OverviewAttentionItem {
  severity: string;
  severity_view: StatusView;
  title: string;
  count: number;
  reason: string;
  route: ConsoleRouteTarget;
}

export interface ConsoleOverviewResponse {
  product: string;
  profile: string;
  runtime_profile: string;
  nodes: OverviewBucket;
  jobs: OverviewBucket;
  connectors: OverviewBucket;
  summary_cards: ConsoleSummaryCard[];
  attention: OverviewAttentionItem[];
  generated_at: string;
}

export interface ConsoleDiagnosticsSegment {
  key: string;
  label: string;
  count: number;
  route: ConsoleRouteTarget;
}

export interface ConsoleNodeDiagnostic {
  node_id: string;
  name: string;
  node_type: string;
  executor: string;
  os: string;
  arch: string;
  zone: string | null;
  status: string;
  status_view: StatusView;
  enrollment_status: string;
  drain_status: string;
  drain_status_view: StatusView;
  heartbeat_state: string;
  heartbeat_state_view: StatusView;
  capacity_state: string;
  capacity_state_view: StatusView;
  active_lease_count: number;
  max_concurrency: number;
  cpu_cores: number;
  memory_mb: number;
  gpu_vram_mb: number;
  storage_mb: number;
  reliability_score: number;
  health_reason: string | null;
  attention_reason: string | null;
  actions: ControlAction[];
  last_seen_at: string;
  route: ConsoleRouteTarget;
}

export interface ConsoleConnectorDiagnostic {
  connector_id: string;
  name: string;
  kind: string;
  status: string;
  status_view: StatusView;
  profile: string;
  endpoint: string | null;
  last_test_status: string | null;
  last_test_message: string | null;
  last_invoke_status: string | null;
  last_invoke_message: string | null;
  attention_reason: string | null;
  actions: ControlAction[];
  updated_at: string;
  route: ConsoleRouteTarget;
}

export interface ConsoleStaleJobDiagnostic {
  job_id: string;
  kind: string;
  node_id: string | null;
  attempt: number;
  priority: number;
  source: string;
  leased_until: string | null;
  lease_state: string;
  lease_state_view: StatusView;
  attention_reason: string | null;
  actions: ControlAction[];
  route: ConsoleRouteTarget;
}

export interface ConsoleUnschedulableJobDiagnostic {
  job_id: string;
  kind: string;
  priority: number;
  priority_view: StatusView;
  source: string;
  selectors: string[];
  blocker_summary: string[];
  created_at: string;
  actions: ControlAction[];
  route: ConsoleRouteTarget;
}

export interface ConsoleDiagnosticsResponse {
  product: string;
  profile: string;
  runtime_profile: string;
  node_health: ConsoleNodeDiagnostic[];
  connector_health: ConsoleConnectorDiagnostic[];
  stale_jobs: ConsoleStaleJobDiagnostic[];
  unschedulable_jobs: ConsoleUnschedulableJobDiagnostic[];
  backlog_by_zone: ConsoleDiagnosticsSegment[];
  backlog_by_capability: ConsoleDiagnosticsSegment[];
  backlog_by_executor: ConsoleDiagnosticsSegment[];
  generated_at: string;
}
