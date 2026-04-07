import { defineStore } from "pinia";
import { computed, ref } from "vue";
import { CONSOLE, PROFILE } from "@/utils/api";
import { http } from "@/utils/http";
import { normalizeStatusView } from "@/utils/statusView";
import type {
  ConsoleConnectorDiagnostic,
  ConsoleDiagnosticsResponse,
  ConsoleDiagnosticsSegment,
  ConsoleMenuItem,
  ConsoleMenuResponse,
  ConsoleNodeDiagnostic,
  ConsoleOverviewResponse,
  ConsoleRouteTarget,
  ConsoleSummaryCard,
  ConsoleStaleJobDiagnostic,
  ConsoleUnschedulableJobDiagnostic,
  GatewayPackInfo,
  GatewayProfileInfo,
  OverviewAttentionItem,
  OverviewBucket,
} from "@/types/console";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function toStringValue(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function toBooleanValue(value: unknown, fallback = false): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function toTitleCase(value: string): string {
  if (!value) return value;
  return value.charAt(0).toUpperCase() + value.slice(1);
}

function normalizeRouteTarget(raw: unknown): ConsoleRouteTarget | null {
  if (!isRecord(raw)) return null;
  const routePath = toStringValue(raw.route_path);
  if (!routePath) return null;
  const queryRaw = isRecord(raw.query) ? raw.query : {};
  const query = Object.fromEntries(
    Object.entries(queryRaw)
      .filter(([, value]) => typeof value === "string")
      .map(([key, value]) => [key, value as string])
  );
  return {
    route_path: routePath,
    query,
  };
}

function normalizeMenuItem(raw: unknown): ConsoleMenuItem | null {
  if (!isRecord(raw)) return null;
  const routeName = toStringValue(raw.route_name);
  const routePath = toStringValue(raw.route_path);
  const label = toStringValue(raw.label);
  const endpoint = toStringValue(raw.endpoint);
  if (!routeName || !routePath || !label || !endpoint) return null;
  return {
    route_name: routeName,
    route_path: routePath,
    label,
    endpoint,
    enabled: toBooleanValue(raw.enabled, true),
    requires_admin: toBooleanValue(raw.requires_admin, false),
    reason: typeof raw.reason === "string" ? raw.reason : null,
  };
}

function normalizeMenuResponse(raw: unknown): ConsoleMenuResponse {
  if (!isRecord(raw)) {
    return {
      product: "ZEN70 Gateway Kernel",
      profile: "gateway-kernel",
      runtime_profile: "gateway-kernel",
      items: [],
    };
  }
  const itemsRaw = Array.isArray(raw.items) ? raw.items : [];
  const items = itemsRaw.map(normalizeMenuItem).filter((item): item is ConsoleMenuItem => item != null);
  return {
    product: toStringValue(raw.product, "ZEN70 Gateway Kernel"),
    profile: toStringValue(raw.profile, "gateway-kernel"),
    runtime_profile: toStringValue(raw.runtime_profile, "gateway-kernel"),
    items,
  };
}

function normalizeOverviewBucket(raw: unknown): OverviewBucket {
  if (!isRecord(raw)) {
    return {
      total: 0,
      active: 0,
      pending: 0,
      running: 0,
      completed: 0,
      failed: 0,
      degraded: 0,
      offline: 0,
      revoked: 0,
      attention: 0,
      stale: 0,
      high_priority_backlog: 0,
    };
  }
  return {
    total: Number(raw.total ?? 0) || 0,
    active: Number(raw.active ?? 0) || 0,
    pending: Number(raw.pending ?? 0) || 0,
    running: Number(raw.running ?? 0) || 0,
    completed: Number(raw.completed ?? 0) || 0,
    failed: Number(raw.failed ?? 0) || 0,
    degraded: Number(raw.degraded ?? 0) || 0,
    offline: Number(raw.offline ?? 0) || 0,
    revoked: Number(raw.revoked ?? 0) || 0,
    attention: Number(raw.attention ?? 0) || 0,
    stale: Number(raw.stale ?? 0) || 0,
    high_priority_backlog: Number(raw.high_priority_backlog ?? 0) || 0,
  };
}

function normalizeAttentionItem(raw: unknown): OverviewAttentionItem | null {
  if (!isRecord(raw)) return null;
  const severity = toStringValue(raw.severity);
  const title = toStringValue(raw.title);
  const reason = toStringValue(raw.reason);
  const route = normalizeRouteTarget(raw.route);
  if (!severity || !title || !reason || !route) return null;
  return {
    severity,
    severity_view: normalizeStatusView(raw.severity_view, severity, severity || "Info", severity === "critical" ? "danger" : severity),
    title,
    count: Number(raw.count ?? 0) || 0,
    reason,
    route,
  };
}

function normalizeSummaryCard(raw: unknown): ConsoleSummaryCard | null {
  if (!isRecord(raw)) return null;
  const key = toStringValue(raw.key);
  const kicker = toStringValue(raw.kicker);
  const title = toStringValue(raw.title);
  const badge = toStringValue(raw.badge);
  const detail = toStringValue(raw.detail);
  const tone = toStringValue(raw.tone, "info");
  if (!key || !kicker || !title || !badge || !detail) return null;
  return {
    key,
    kicker,
    title,
    value: Number(raw.value ?? 0) || 0,
    badge,
    detail,
    tone,
    tone_view: normalizeStatusView(raw.tone_view, tone, toTitleCase(tone), tone),
    route: normalizeRouteTarget(raw.route),
  };
}

function normalizeOverviewResponse(raw: unknown): ConsoleOverviewResponse {
  if (!isRecord(raw)) {
    return {
      product: "ZEN70 Gateway Kernel",
      profile: "gateway-kernel",
      runtime_profile: "gateway-kernel",
      nodes: normalizeOverviewBucket(null),
      jobs: normalizeOverviewBucket(null),
      connectors: normalizeOverviewBucket(null),
      summary_cards: [],
      attention: [],
      generated_at: new Date(0).toISOString(),
    };
  }
  const attentionRaw = Array.isArray(raw.attention) ? raw.attention : [];
  const summaryCardsRaw = Array.isArray(raw.summary_cards) ? raw.summary_cards : [];
  return {
    product: toStringValue(raw.product, "ZEN70 Gateway Kernel"),
    profile: toStringValue(raw.profile, "gateway-kernel"),
    runtime_profile: toStringValue(raw.runtime_profile, "gateway-kernel"),
    nodes: normalizeOverviewBucket(raw.nodes),
    jobs: normalizeOverviewBucket(raw.jobs),
    connectors: normalizeOverviewBucket(raw.connectors),
    summary_cards: summaryCardsRaw
      .map(normalizeSummaryCard)
      .filter((item): item is ConsoleSummaryCard => item != null),
    attention: attentionRaw
      .map(normalizeAttentionItem)
      .filter((item): item is OverviewAttentionItem => item != null),
    generated_at: toStringValue(raw.generated_at, new Date(0).toISOString()),
  };
}

function normalizeDiagnosticsSegment(raw: unknown): ConsoleDiagnosticsSegment | null {
  if (!isRecord(raw)) return null;
  const key = toStringValue(raw.key);
  const label = toStringValue(raw.label);
  const route = normalizeRouteTarget(raw.route);
  if (!key || !label || !route) return null;
  return {
    key,
    label,
    count: Number(raw.count ?? 0) || 0,
    route,
  };
}

function normalizeNodeDiagnostic(raw: unknown): ConsoleNodeDiagnostic | null {
  if (!isRecord(raw)) return null;
  const nodeId = toStringValue(raw.node_id);
  const name = toStringValue(raw.name);
  if (!nodeId || !name) return null;
  const route = normalizeRouteTarget(raw.route);
  if (!route) return null;
  return {
    node_id: nodeId,
    name,
    node_type: toStringValue(raw.node_type, "runner"),
    executor: toStringValue(raw.executor, "unknown"),
    os: toStringValue(raw.os, "unknown"),
    arch: toStringValue(raw.arch, "unknown"),
    zone: typeof raw.zone === "string" ? raw.zone : null,
    status: toStringValue(raw.status, "unknown"),
    status_view: normalizeStatusView(raw.status_view, toStringValue(raw.status, "unknown"), toStringValue(raw.status, "Unknown")),
    enrollment_status: toStringValue(raw.enrollment_status, "pending"),
    drain_status: toStringValue(raw.drain_status, "active"),
    drain_status_view: normalizeStatusView(raw.drain_status_view, toStringValue(raw.drain_status, "active"), toStringValue(raw.drain_status, "Active"), "info"),
    heartbeat_state: toStringValue(raw.heartbeat_state, "fresh"),
    heartbeat_state_view: normalizeStatusView(raw.heartbeat_state_view, toStringValue(raw.heartbeat_state, "fresh"), toStringValue(raw.heartbeat_state, "Fresh")),
    capacity_state: toStringValue(raw.capacity_state, "available"),
    capacity_state_view: normalizeStatusView(raw.capacity_state_view, toStringValue(raw.capacity_state, "available"), toStringValue(raw.capacity_state, "Available")),
    active_lease_count: Number(raw.active_lease_count ?? 0) || 0,
    max_concurrency: Number(raw.max_concurrency ?? 1) || 1,
    cpu_cores: Number(raw.cpu_cores ?? 0) || 0,
    memory_mb: Number(raw.memory_mb ?? 0) || 0,
    gpu_vram_mb: Number(raw.gpu_vram_mb ?? 0) || 0,
    storage_mb: Number(raw.storage_mb ?? 0) || 0,
    reliability_score: Number(raw.reliability_score ?? 0) || 0,
    health_reason: typeof raw.health_reason === "string" ? raw.health_reason : null,
    attention_reason: typeof raw.attention_reason === "string" ? raw.attention_reason : null,
    actions: Array.isArray(raw.actions) ? (raw.actions as ConsoleNodeDiagnostic["actions"]) : [],
    last_seen_at: toStringValue(raw.last_seen_at, new Date(0).toISOString()),
    route,
  };
}

function normalizeConnectorDiagnostic(raw: unknown): ConsoleConnectorDiagnostic | null {
  if (!isRecord(raw)) return null;
  const connectorId = toStringValue(raw.connector_id);
  const name = toStringValue(raw.name);
  const route = normalizeRouteTarget(raw.route);
  if (!connectorId || !name || !route) return null;
  return {
    connector_id: connectorId,
    name,
    kind: toStringValue(raw.kind, "unknown"),
    status: toStringValue(raw.status, "unknown"),
    status_view: normalizeStatusView(raw.status_view, toStringValue(raw.status, "unknown"), toStringValue(raw.status, "Unknown")),
    profile: toStringValue(raw.profile, "manual"),
    endpoint: typeof raw.endpoint === "string" ? raw.endpoint : null,
    last_test_status: typeof raw.last_test_status === "string" ? raw.last_test_status : null,
    last_test_message: typeof raw.last_test_message === "string" ? raw.last_test_message : null,
    last_invoke_status: typeof raw.last_invoke_status === "string" ? raw.last_invoke_status : null,
    last_invoke_message: typeof raw.last_invoke_message === "string" ? raw.last_invoke_message : null,
    attention_reason: typeof raw.attention_reason === "string" ? raw.attention_reason : null,
    actions: Array.isArray(raw.actions) ? (raw.actions as ConsoleConnectorDiagnostic["actions"]) : [],
    updated_at: toStringValue(raw.updated_at, new Date(0).toISOString()),
    route,
  };
}

function normalizeStaleJobDiagnostic(raw: unknown): ConsoleStaleJobDiagnostic | null {
  if (!isRecord(raw)) return null;
  const jobId = toStringValue(raw.job_id);
  const kind = toStringValue(raw.kind);
  const route = normalizeRouteTarget(raw.route);
  if (!jobId || !kind || !route) return null;
  return {
    job_id: jobId,
    kind,
    node_id: typeof raw.node_id === "string" ? raw.node_id : null,
    attempt: Number(raw.attempt ?? 0) || 0,
    priority: Number(raw.priority ?? 0) || 0,
    source: toStringValue(raw.source, "console"),
    leased_until: typeof raw.leased_until === "string" ? raw.leased_until : null,
    lease_state: toStringValue(raw.lease_state, "none"),
    lease_state_view: normalizeStatusView(raw.lease_state_view, toStringValue(raw.lease_state, "none"), toStringValue(raw.lease_state, "None")),
    attention_reason: typeof raw.attention_reason === "string" ? raw.attention_reason : null,
    actions: Array.isArray(raw.actions) ? (raw.actions as ConsoleStaleJobDiagnostic["actions"]) : [],
    route,
  };
}

function normalizeUnschedulableJobDiagnostic(raw: unknown): ConsoleUnschedulableJobDiagnostic | null {
  if (!isRecord(raw)) return null;
  const jobId = toStringValue(raw.job_id);
  const kind = toStringValue(raw.kind);
  const route = normalizeRouteTarget(raw.route);
  if (!jobId || !kind || !route) return null;
  return {
    job_id: jobId,
    kind,
    priority: Number(raw.priority ?? 0) || 0,
    priority_view: normalizeStatusView(raw.priority_view, "priority", "Priority", Number(raw.priority ?? 0) >= 80 ? "danger" : "warning"),
    source: toStringValue(raw.source, "console"),
    selectors: Array.isArray(raw.selectors)
      ? raw.selectors.filter((item): item is string => typeof item === "string")
      : [],
    blocker_summary: Array.isArray(raw.blocker_summary)
      ? raw.blocker_summary.filter((item): item is string => typeof item === "string")
      : [],
    created_at: toStringValue(raw.created_at, new Date(0).toISOString()),
    actions: Array.isArray(raw.actions) ? (raw.actions as ConsoleUnschedulableJobDiagnostic["actions"]) : [],
    route,
  };
}

function normalizeDiagnosticsResponse(raw: unknown): ConsoleDiagnosticsResponse {
  if (!isRecord(raw)) {
    return {
      product: "ZEN70 Gateway Kernel",
      profile: "gateway-kernel",
      runtime_profile: "gateway-kernel",
      node_health: [],
      connector_health: [],
      stale_jobs: [],
      unschedulable_jobs: [],
      backlog_by_zone: [],
      backlog_by_capability: [],
      backlog_by_executor: [],
      generated_at: new Date(0).toISOString(),
    };
  }
  return {
    product: toStringValue(raw.product, "ZEN70 Gateway Kernel"),
    profile: toStringValue(raw.profile, "gateway-kernel"),
    runtime_profile: toStringValue(raw.runtime_profile, "gateway-kernel"),
    node_health: Array.isArray(raw.node_health)
      ? raw.node_health.map(normalizeNodeDiagnostic).filter((item): item is ConsoleNodeDiagnostic => item != null)
      : [],
    connector_health: Array.isArray(raw.connector_health)
      ? raw.connector_health
          .map(normalizeConnectorDiagnostic)
          .filter((item): item is ConsoleConnectorDiagnostic => item != null)
      : [],
    stale_jobs: Array.isArray(raw.stale_jobs)
      ? raw.stale_jobs
          .map(normalizeStaleJobDiagnostic)
          .filter((item): item is ConsoleStaleJobDiagnostic => item != null)
      : [],
    unschedulable_jobs: Array.isArray(raw.unschedulable_jobs)
      ? raw.unschedulable_jobs
          .map(normalizeUnschedulableJobDiagnostic)
          .filter((item): item is ConsoleUnschedulableJobDiagnostic => item != null)
      : [],
    backlog_by_zone: Array.isArray(raw.backlog_by_zone)
      ? raw.backlog_by_zone
          .map(normalizeDiagnosticsSegment)
          .filter((item): item is ConsoleDiagnosticsSegment => item != null)
      : [],
    backlog_by_capability: Array.isArray(raw.backlog_by_capability)
      ? raw.backlog_by_capability
          .map(normalizeDiagnosticsSegment)
          .filter((item): item is ConsoleDiagnosticsSegment => item != null)
      : [],
    backlog_by_executor: Array.isArray(raw.backlog_by_executor)
      ? raw.backlog_by_executor
          .map(normalizeDiagnosticsSegment)
          .filter((item): item is ConsoleDiagnosticsSegment => item != null)
      : [],
    generated_at: toStringValue(raw.generated_at, new Date(0).toISOString()),
  };
}

function normalizeProfile(raw: unknown): GatewayProfileInfo | null {
  if (!isRecord(raw)) return null;
  const product = toStringValue(raw.product, "ZEN70 Gateway Kernel");
  const profile = toStringValue(raw.profile);
  const runtimeProfile = toStringValue(raw.runtime_profile);
  if (!profile || !runtimeProfile) return null;
  const routerNames = Array.isArray(raw.router_names)
    ? raw.router_names.filter((item): item is string => typeof item === "string")
    : [];
  const consoleRouteNames = Array.isArray(raw.console_route_names)
    ? raw.console_route_names.filter((item): item is string => typeof item === "string")
    : [];
  const capabilityKeys = Array.isArray(raw.capability_keys)
    ? raw.capability_keys.filter((item): item is string => typeof item === "string")
    : [];
  const requestedPackKeys = Array.isArray(raw.requested_pack_keys)
    ? raw.requested_pack_keys.filter((item): item is string => typeof item === "string")
    : [];
  const resolvedPackKeys = Array.isArray(raw.resolved_pack_keys)
    ? raw.resolved_pack_keys.filter((item): item is string => typeof item === "string")
    : [];
  const packs = Array.isArray(raw.packs)
    ? raw.packs
        .map((item): GatewayPackInfo | null => {
          if (!isRecord(item)) return null;
          const packKey = toStringValue(item.pack_key);
          const label = toStringValue(item.label);
          if (!packKey || !label) return null;
          return {
            pack_key: packKey,
            label,
            category: toStringValue(item.category, "pack"),
            description: toStringValue(item.description),
            delivery_stage: toStringValue(item.delivery_stage, "contract-only"),
            selected: toBooleanValue(item.selected, false),
            inherited: toBooleanValue(item.inherited, false),
            services: Array.isArray(item.services)
              ? item.services.filter((value): value is string => typeof value === "string")
              : [],
            router_names: Array.isArray(item.router_names)
              ? item.router_names.filter((value): value is string => typeof value === "string")
              : [],
            capability_keys: Array.isArray(item.capability_keys)
              ? item.capability_keys.filter((value): value is string => typeof value === "string")
              : [],
            selector_hints: Array.isArray(item.selector_hints)
              ? item.selector_hints.filter((value): value is string => typeof value === "string")
              : [],
            deployment_boundary: toStringValue(item.deployment_boundary),
            runtime_owner: toStringValue(item.runtime_owner),
            status_view: normalizeStatusView(
              item.status_view,
              packKey,
              label,
              toBooleanValue(item.selected, false) ? "success" : toBooleanValue(item.inherited, false) ? "info" : "neutral"
            ),
          };
        })
        .filter((item): item is GatewayPackInfo => item != null)
    : [];
  return {
    product,
    profile,
    runtime_profile: runtimeProfile,
    router_names: routerNames,
    console_route_names: consoleRouteNames,
    capability_keys: capabilityKeys,
    requested_pack_keys: requestedPackKeys,
    resolved_pack_keys: resolvedPackKeys,
    packs,
    cluster_enabled: toBooleanValue(raw.cluster_enabled, false),
  };
}

export const useConsoleStore = defineStore("console", () => {
  const profile = ref<GatewayProfileInfo | null>(null);
  const menu = ref<ConsoleMenuItem[]>([]);
  const overview = ref<ConsoleOverviewResponse | null>(null);
  const diagnostics = ref<ConsoleDiagnosticsResponse | null>(null);
  const loading = ref(false);
  const error = ref<string | null>(null);
  const lastUpdatedAt = ref(0);

  const hasMenu = computed(() => menu.value.length > 0);

  async function refresh(): Promise<void> {
    loading.value = true;
    error.value = null;
    try {
      const [diagnosticsRes, profileRes, menuRes, overviewRes] = await Promise.all([
        http.get<unknown>(CONSOLE.diagnostics),
        http.get<unknown>(PROFILE.current),
        http.get<unknown>(CONSOLE.menu),
        http.get<unknown>(CONSOLE.overview),
      ]);
      diagnostics.value = normalizeDiagnosticsResponse(diagnosticsRes.data);
      profile.value = normalizeProfile(profileRes.data);
      const normalizedMenu = normalizeMenuResponse(menuRes.data);
      menu.value = normalizedMenu.items;
      overview.value = normalizeOverviewResponse(overviewRes.data);
      profile.value ??= {
        product: normalizedMenu.product,
        profile: normalizedMenu.profile,
        runtime_profile: normalizedMenu.runtime_profile,
        router_names: [],
        console_route_names: normalizedMenu.items.map((item) => item.route_name),
        capability_keys: [],
        requested_pack_keys: [],
        resolved_pack_keys: [],
        packs: [],
        cluster_enabled: false,
      };
      lastUpdatedAt.value = Date.now();
    } catch (err: unknown) {
      error.value = err instanceof Error ? err.message : "load console state failed";
    } finally {
      loading.value = false;
    }
  }

  return {
    profile,
    menu,
    overview,
    diagnostics,
    loading,
    error,
    lastUpdatedAt,
    hasMenu,
    refresh,
  };
});
