// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { reactive } from "vue";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ControlDashboard from "../src/views/ControlDashboard.vue";
import ConnectorsView from "../src/views/ConnectorsView.vue";
import JobsView from "../src/views/JobsView.vue";
import NodesView from "../src/views/NodesView.vue";
import { CONNECTORS, JOBS, NODES } from "../src/utils/api";

const mockState = vi.hoisted(() => ({
  route: { path: "/", fullPath: "/", query: {} as Record<string, string> },
  routerPush: vi.fn(),
  capabilitiesStore: {} as Record<string, unknown>,
  consoleStore: {} as Record<string, unknown>,
  nodesStore: {} as Record<string, unknown>,
  jobsStore: {} as Record<string, unknown>,
  connectorsStore: {} as Record<string, unknown>,
  eventsStore: {} as Record<string, unknown>,
}));

vi.mock("vue-router", () => ({
  useRoute: () => mockState.route,
  useRouter: () => ({ push: mockState.routerPush }),
  RouterLink: {
    name: "RouterLink",
    props: ["to"],
    template: "<a><slot /></a>",
  },
}));

vi.mock("@/stores/capabilities", () => ({
  useCapabilitiesStore: () => mockState.capabilitiesStore,
}));

vi.mock("@/stores/console", () => ({
  useConsoleStore: () => mockState.consoleStore,
}));

vi.mock("@/stores/nodes", () => ({
  useNodesStore: () => mockState.nodesStore,
}));

vi.mock("@/stores/jobs", () => ({
  useJobsStore: () => mockState.jobsStore,
}));

vi.mock("@/stores/connectors", () => ({
  useConnectorsStore: () => mockState.connectorsStore,
}));

vi.mock("@/stores/events", () => ({
  useEventsStore: () => mockState.eventsStore,
}));

const globalStubs = {
  BackendForm: {
    name: "BackendForm",
    props: ["title"],
    template: "<div data-test='backend-form'>{{ title }}</div>",
  },
  ControlActionDialog: {
    name: "ControlActionDialog",
    template: "<div data-test='action-dialog' />",
  },
};

function tone(key: string, label: string, toneValue = "info") {
  return { key, label, tone: toneValue };
}

function action(key: string, label: string) {
  return {
    key,
    label,
    endpoint: `/v1/mock/${key}`,
    method: "POST",
    enabled: true,
    requires_admin: true,
    reason: null,
    confirmation: null,
    fields: [],
  };
}

function resourceSchema(title: string, description: string) {
  return {
    product: "ZEN70 Gateway Kernel",
    profile: "gateway-kernel",
    runtime_profile: "gateway-kernel",
    resource: title.toLowerCase(),
    title,
    description,
    empty_state: `No ${title.toLowerCase()} available.`,
    policies: {
      resource_mode: "backend-driven",
      ui_mode: "operations",
      secret_delivery: { visibility: "one-time" },
    },
    submit_action: action("submit", "Submit"),
    sections: [],
  };
}

beforeEach(() => {
  mockState.route = reactive({ path: "/", fullPath: "/", query: {} as Record<string, string> });
  mockState.routerPush.mockReset();

  mockState.capabilitiesStore = {
    caps: {},
    fetchCapabilities: vi.fn().mockResolvedValue(undefined),
  };

  mockState.consoleStore = {
    hasMenu: true,
    loading: false,
    error: null,
    menu: [
      { route_name: "nodes", route_path: "/nodes", label: "Nodes", endpoint: NODES.list, enabled: true, requires_admin: true, reason: null },
      { route_name: "jobs", route_path: "/jobs", label: "Jobs", endpoint: JOBS.list, enabled: true, requires_admin: true, reason: null },
      { route_name: "connectors", route_path: "/connectors", label: "Connectors", endpoint: CONNECTORS.list, enabled: true, requires_admin: true, reason: null },
    ],
    profile: {
      profile: "gateway-kernel",
      packs: [
        {
          pack_key: "health",
          label: "Health Pack",
          category: "health",
          description: "Native health ingestion stays outside the kernel runtime.",
          delivery_stage: "mvp-skeleton",
          selected: false,
          inherited: false,
          services: ["health-sync"],
          router_names: ["health"],
          capability_keys: ["health.ingest"],
          selector_hints: ["capability=health.ingest"],
          deployment_boundary: "native client + pack service",
          runtime_owner: "pack",
          status_view: tone("available", "Available", "info"),
        },
      ],
    },
    overview: {
      generated_at: "2026-03-28T12:00:00Z",
      summary_cards: [
        {
          key: "pending-jobs",
          kicker: "Jobs",
          title: "Pending Jobs",
          value: 3,
          badge: "attention",
          detail: "Three jobs need placement",
          tone: "warning",
          tone_view: tone("warning", "Warning", "warning"),
          route: { route_path: "/jobs", query: { status: "pending" } },
        },
      ],
      attention: [
        {
          severity: "critical",
          severity_view: tone("critical", "Critical", "danger"),
          title: "Blocked Work",
          count: 1,
          reason: "One job has no eligible node",
          route: { route_path: "/jobs", query: { status: "pending" } },
        },
      ],
    },
    diagnostics: {
      node_health: [
        {
          node_id: "node-1",
          name: "Win Runner",
          node_type: "runner",
          executor: "go-native",
          os: "windows",
          arch: "amd64",
          zone: "cn-sh",
          status_view: tone("online", "Online", "success"),
          drain_status_view: tone("active", "Active", "info"),
          heartbeat_state_view: tone("fresh", "Fresh", "success"),
          capacity_state_view: tone("available", "Available", "info"),
          active_lease_count: 0,
          max_concurrency: 2,
          cpu_cores: 8,
          memory_mb: 16384,
          gpu_vram_mb: 0,
          storage_mb: 512000,
          reliability_score: 0.98,
          attention_reason: null,
          actions: [action("drain", "Drain")],
          last_seen_at: "2026-03-28T11:58:00Z",
          route: { route_path: "/nodes", query: { node_id: "node-1" } },
        },
      ],
      connector_health: [
        {
          connector_id: "mqtt-main",
          name: "MQTT Main",
          kind: "mqtt",
          status_view: tone("online", "Online", "success"),
          profile: "gateway-kernel",
          endpoint: "mqtt://broker",
          last_test_status: "ok",
          last_test_message: "healthy",
          last_invoke_status: "accepted",
          last_invoke_message: "job queued",
          attention_reason: null,
          actions: [action("test", "Test")],
          updated_at: "2026-03-28T12:00:00Z",
          route: { route_path: "/connectors", query: { connector_id: "mqtt-main" } },
        },
      ],
      unschedulable_jobs: [
        {
          job_id: "job-1",
          kind: "health.sync",
          priority: 90,
          priority_view: tone("high", "High", "warning"),
          source: "console",
          selectors: ["executor=native-client"],
          blocker_summary: ["no eligible ios node"],
          created_at: "2026-03-28T11:50:00Z",
          actions: [action("retry", "Retry")],
          route: { route_path: "/jobs", query: { job_id: "job-1" } },
        },
      ],
      stale_jobs: [],
      backlog_by_zone: [],
      backlog_by_capability: [],
      backlog_by_executor: [],
    },
    refresh: vi.fn().mockResolvedValue(undefined),
  };

  mockState.nodesStore = {
    items: [],
    schema: null,
    loading: false,
    submitting: false,
    actionLoading: {},
    error: null,
    lastProvisioned: null,
    lastUpdatedAt: 0,
    fetchSchema: vi.fn().mockResolvedValue(undefined),
    fetchNodes: vi.fn().mockResolvedValue(undefined),
    provisionNode: vi.fn().mockResolvedValue(undefined),
    runNodeAction: vi.fn().mockResolvedValue(undefined),
    applyNodeEvent: vi.fn(),
    clearProvisionedSecret: vi.fn(),
  };

  mockState.jobsStore = {
    items: [],
    schema: null,
    attemptsByJob: {},
    explanationsByJob: {},
    attemptsLoading: {},
    explainLoading: {},
    actionLoading: {},
    loading: false,
    submitting: false,
    error: null,
    fetchSchema: vi.fn().mockResolvedValue(undefined),
    fetchJobs: vi.fn().mockResolvedValue(undefined),
    fetchJobAttempts: vi.fn().mockResolvedValue([]),
    fetchJobExplain: vi.fn().mockResolvedValue(null),
    runJobAction: vi.fn().mockResolvedValue(undefined),
    createJob: vi.fn().mockResolvedValue(undefined),
    applyJobEvent: vi.fn(),
  };

  mockState.connectorsStore = {
    items: [],
    schema: null,
    loading: false,
    submitting: false,
    actionLoading: {},
    error: null,
    fetchSchema: vi.fn().mockResolvedValue(undefined),
    fetchConnectors: vi.fn().mockResolvedValue(undefined),
    upsertConnector: vi.fn().mockResolvedValue(undefined),
    runConnectorAction: vi.fn().mockResolvedValue(undefined),
    applyConnectorEvent: vi.fn(),
  };

  mockState.eventsStore = {
    revision: 0,
    items: [],
  };
});

describe("control-plane views", () => {
  it("renders backend-driven dashboard data and refreshes on mount", async () => {
    mockState.capabilitiesStore.caps = {
      nodes: { endpoint: NODES.list, enabled: true, status: "online" },
      jobs: { endpoint: JOBS.list, enabled: true, status: "online" },
      connectors: { endpoint: CONNECTORS.list, enabled: true, status: "online" },
    };

    const wrapper = mount(ControlDashboard, {
      global: { stubs: globalStubs },
    });
    await flushPromises();

    expect(mockState.consoleStore.refresh).toHaveBeenCalledTimes(1);
    expect(wrapper.text()).toContain("Operational Overview");
    expect(wrapper.text()).toContain("Pending Jobs");
    expect(wrapper.text()).toContain("Business Boundaries");
    expect(wrapper.text()).toContain("mvp-skeleton");
    expect(wrapper.text()).toContain("Blocked Work");
    expect(wrapper.text()).toContain("MQTT Main");
  });

  it("renders node fleet data from backend schema and provisioning receipt", async () => {
    mockState.route = reactive({
      path: "/nodes",
      fullPath: "/nodes?os=windows&attention=1",
      query: { os: "windows", attention: "1" },
    });
    mockState.nodesStore.schema = resourceSchema("Fleet Nodes", "Backend-owned fleet schema");
    mockState.nodesStore.items = [
      {
        node_id: "node-1",
        name: "Win Runner",
        node_type: "runner",
        address: "10.0.0.1",
        profile: "gateway-kernel",
        executor: "go-native",
        os: "windows",
        arch: "amd64",
        zone: "cn-sh",
        protocol_version: "v1",
        lease_version: "v2",
        agent_version: "1.0.0",
        max_concurrency: 2,
        active_lease_count: 1,
        cpu_cores: 8,
        memory_mb: 16384,
        gpu_vram_mb: 0,
        storage_mb: 512000,
        drain_status_view: tone("active", "Active", "info"),
        heartbeat_state_view: tone("fresh", "Fresh", "success"),
        capacity_state_view: tone("available", "Available", "info"),
        enrollment_status_view: tone("approved", "Approved", "success"),
        status_view: tone("online", "Online", "success"),
        drain_status: "active",
        heartbeat_state: "fresh",
        capacity_state: "available",
        enrollment_status: "approved",
        status: "online",
        health_reason: null,
        attention_reason: "healthy",
        capabilities: ["health.ingest"],
        metadata: { device: "windows-runner" },
        actions: [action("rotate_token", "Rotate Token")],
        registered_at: "2026-03-28T11:00:00Z",
        last_seen_at: "2026-03-28T12:00:00Z",
      },
    ];
    mockState.nodesStore.lastProvisioned = {
      node: mockState.nodesStore.items[0],
      node_token: "secret-token",
      auth_token_version: 1,
      bootstrap_commands: {
        powershell: "pwsh bootstrap",
        unix: "bash bootstrap",
      },
      bootstrap_notes: ["复制后妥善保存"],
      bootstrap_receipts: [],
    };

    const wrapper = mount(NodesView, {
      global: { stubs: globalStubs },
    });
    await flushPromises();

    expect(mockState.capabilitiesStore.fetchCapabilities).toHaveBeenCalledTimes(1);
    expect(mockState.nodesStore.fetchSchema).toHaveBeenCalledTimes(1);
    expect(mockState.nodesStore.fetchNodes).toHaveBeenCalledWith({ os: "windows", attention: "1" });
    expect(wrapper.text()).toContain("Fleet Nodes");
    expect(wrapper.text()).toContain("One-Time Credential");
    expect(wrapper.text()).toContain("secret-token");
    expect(wrapper.text()).toContain("os windows");
    expect(wrapper.text()).toContain("attention only");
    expect(wrapper.text()).toContain("Win Runner");
  });

  it("renders jobs from backend contracts and fetches attempts/explain on demand", async () => {
    mockState.route = reactive({
      path: "/jobs",
      fullPath: "/jobs?target_executor=swift-native",
      query: { target_executor: "swift-native" },
    });
    mockState.jobsStore.schema = resourceSchema("Operations Queue", "Backend-owned job schema");
    mockState.jobsStore.items = [
      {
        job_id: "job-1",
        kind: "health.sync",
        status: "pending",
        status_view: tone("pending", "Pending", "warning"),
        node_id: null,
        connector_id: null,
        idempotency_key: "job-1",
        priority: 90,
        target_os: "ios",
        target_arch: "arm64",
        target_executor: "swift-native",
        required_capabilities: ["health.ingest"],
        target_zone: "cn-sh",
        required_cpu_cores: 1,
        required_memory_mb: 256,
        required_gpu_vram_mb: null,
        required_storage_mb: 128,
        timeout_seconds: 120,
        max_retries: 2,
        retry_count: 0,
        estimated_duration_s: 30,
        source: "console",
        attempt: 0,
        payload: {},
        result: null,
        safe_error_code: null,
        safe_error_hint: null,
        lease_seconds: 30,
        leased_until: null,
        lease_state: "none",
        lease_state_view: tone("none", "None", "info"),
        attention_reason: null,
        actions: [action("explain", "Explain")],
        created_at: "2026-03-28T11:00:00Z",
        started_at: null,
        completed_at: null,
      },
    ];

    const wrapper = mount(JobsView, {
      global: { stubs: globalStubs },
    });
    await flushPromises();

    expect(mockState.jobsStore.fetchSchema).toHaveBeenCalledTimes(1);
    expect(mockState.jobsStore.fetchJobs).toHaveBeenCalledWith({ target_executor: "swift-native" });
    expect(wrapper.text()).toContain("Operations Queue");
    expect(wrapper.text()).toContain("executor swift-native");

    const showAttemptsButton = wrapper.findAll("button").find((button) => button.text() === "Show Attempts");
    expect(showAttemptsButton).toBeTruthy();
    await showAttemptsButton!.trigger("click");
    expect(mockState.jobsStore.fetchJobAttempts).toHaveBeenCalledWith("job-1");

    const explainButton = wrapper.findAll("button").find((button) => button.text() === "Explain");
    expect(explainButton).toBeTruthy();
    await explainButton!.trigger("click");
    expect(mockState.jobsStore.fetchJobExplain).toHaveBeenCalledWith("job-1");
  });

  it("renders connectors from backend schema and filtered list queries", async () => {
    mockState.route = reactive({
      path: "/connectors",
      fullPath: "/connectors?status=error",
      query: { status: "error" },
    });
    mockState.connectorsStore.schema = resourceSchema("Integration Hub", "Backend-owned connector schema");
    mockState.connectorsStore.items = [
      {
        connector_id: "mqtt-main",
        name: "MQTT Main",
        kind: "mqtt",
        status: "error",
        status_view: tone("error", "Error", "danger"),
        endpoint: "mqtt://broker",
        profile: "gateway-kernel",
        config: { host: "broker" },
        last_test_ok: false,
        last_test_status: "error",
        last_test_message: "auth failed",
        last_test_at: "2026-03-28T11:00:00Z",
        last_invoke_status: "failed",
        last_invoke_message: "timeout",
        last_invoke_job_id: "job-99",
        last_invoke_at: "2026-03-28T11:05:00Z",
        attention_reason: "connector auth broken",
        actions: [action("test", "Test")],
        created_at: "2026-03-28T10:00:00Z",
        updated_at: "2026-03-28T12:00:00Z",
      },
    ];

    const wrapper = mount(ConnectorsView, {
      global: { stubs: globalStubs },
    });
    await flushPromises();

    expect(mockState.connectorsStore.fetchSchema).toHaveBeenCalledTimes(1);
    expect(mockState.connectorsStore.fetchConnectors).toHaveBeenCalledWith({ status: "error" });
    expect(wrapper.text()).toContain("Integration Hub");
    expect(wrapper.text()).toContain("MQTT Main");
    expect(wrapper.text()).toContain("status error");
    expect(wrapper.text()).toContain("connector auth broken");
  });
});
