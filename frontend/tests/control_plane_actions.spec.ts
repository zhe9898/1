// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent, reactive } from "vue";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ControlDashboard from "../src/views/ControlDashboard.vue";
import ConnectorsView from "../src/views/ConnectorsView.vue";
import JobsView from "../src/views/JobsView.vue";
import NodesView from "../src/views/NodesView.vue";

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

function resourceSchema(title: string) {
  return {
    product: "ZEN70 Gateway Kernel",
    profile: "gateway-kernel",
    runtime_profile: "gateway-kernel",
    resource: title.toLowerCase(),
    title,
    description: `${title} come from backend-owned schema`,
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

const BackendFormStub = defineComponent({
  name: "BackendForm",
  props: {
    title: { type: String, default: "" },
  },
  emits: ["submit", "invalid"],
  setup(_, { emit }) {
    function submit(): void {
      emit("submit", {
        node_id: "node-new",
        name: "Node New",
        node_type: "runner",
        profile: "gateway-kernel",
        executor: "swift-native",
        os: "ios",
        arch: "arm64",
        zone: "cn-sh",
        kind: "health.sync",
        connector_id: "connector-main",
        payload: { sync: true },
        priority: 80,
        target_executor: "swift-native",
        capabilities: ["health.ingest"],
        required_capabilities: ["health.ingest"],
        config: { host: "broker" },
      });
    }
    return { submit };
  },
  template: "<div data-test='backend-form'><button data-test='backend-form-submit' @click='submit'>Submit</button></div>",
});

const ControlActionDialogStub = defineComponent({
  name: "ControlActionDialog",
  props: {
    action: { type: Object, default: null },
    submitting: { type: Boolean, default: false },
  },
  emits: ["submit", "close", "invalid"],
  setup(_, { emit }) {
    function submit(): void {
      emit("submit", { reason: "operator-confirmed", timeout_ms: 5000, action: "ping" });
    }
    function close(): void {
      emit("close");
    }
    return { submit, close };
  },
  template: `
    <div data-test="action-dialog">
      <button v-if="action" data-test="dialog-submit" @click="submit">Submit Action</button>
      <button v-if="action" data-test="dialog-close" @click="close">Close Action</button>
    </div>
  `,
});

beforeEach(() => {
  mockState.route = reactive({ path: "/", fullPath: "/", query: {} as Record<string, string> });
  mockState.routerPush.mockReset();

  mockState.capabilitiesStore = {
    caps: {
      nodes: { endpoint: "/v1/nodes", enabled: true, status: "online" },
      jobs: { endpoint: "/v1/jobs", enabled: true, status: "online" },
      connectors: { endpoint: "/v1/connectors", enabled: true, status: "online" },
    },
    fetchCapabilities: vi.fn().mockResolvedValue(undefined),
  };

  mockState.consoleStore = {
    hasMenu: true,
    loading: false,
    error: null,
    menu: [],
    profile: { profile: "gateway-kernel", packs: [] },
    overview: {
      generated_at: "2026-03-28T12:00:00Z",
      summary_cards: [],
      attention: [],
    },
    diagnostics: {
      node_health: [
        {
          node_id: "node-1",
          name: "Node One",
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
          storage_mb: 102400,
          reliability_score: 0.99,
          attention_reason: null,
          actions: [action("drain", "Drain")],
          last_seen_at: "2026-03-28T12:00:00Z",
          route: { route_path: "/nodes", query: { node_id: "node-1" } },
        },
      ],
      connector_health: [],
      unschedulable_jobs: [],
      stale_jobs: [],
      backlog_by_zone: [],
      backlog_by_capability: [],
      backlog_by_executor: [],
    },
    refresh: vi.fn().mockResolvedValue(undefined),
  };

  mockState.nodesStore = {
    items: [
      {
        node_id: "node-1",
        name: "Node One",
        node_type: "runner",
        address: null,
        profile: "gateway-kernel",
        executor: "go-native",
        os: "windows",
        arch: "amd64",
        zone: "cn-sh",
        protocol_version: "runner.v1",
        lease_version: "job-lease.v1",
        agent_version: "runner-agent.v1",
        max_concurrency: 2,
        active_lease_count: 0,
        cpu_cores: 8,
        memory_mb: 16384,
        gpu_vram_mb: 0,
        storage_mb: 102400,
        drain_status: "active",
        drain_status_view: tone("active", "Active", "info"),
        health_reason: null,
        heartbeat_state: "fresh",
        heartbeat_state_view: tone("fresh", "Fresh", "success"),
        capacity_state: "available",
        capacity_state_view: tone("available", "Available", "info"),
        attention_reason: null,
        enrollment_status: "approved",
        enrollment_status_view: tone("approved", "Approved", "success"),
        status: "online",
        status_view: tone("online", "Online", "success"),
        capabilities: ["health.ingest"],
        metadata: { team: "ops" },
        actions: [action("drain", "Drain")],
        registered_at: "2026-03-28T10:00:00Z",
        last_seen_at: "2026-03-28T12:00:00Z",
      },
    ],
    schema: resourceSchema("Fleet Nodes"),
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
    items: [
      {
        job_id: "job-1",
        kind: "health.sync",
        status: "pending",
        status_view: tone("pending", "Pending", "warning"),
        node_id: null,
        connector_id: "connector-main",
        idempotency_key: "job-1-key",
        priority: 80,
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
        actions: [action("retry", "Retry Now"), action("explain", "Explain")],
        created_at: "2026-03-28T11:00:00Z",
        started_at: null,
        completed_at: null,
      },
    ],
    schema: resourceSchema("Operations Queue"),
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
    items: [
      {
        connector_id: "connector-main",
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
        last_invoke_job_id: "job-9",
        last_invoke_at: "2026-03-28T11:05:00Z",
        attention_reason: "auth broken",
        actions: [action("invoke", "Invoke"), action("test", "Test")],
        created_at: "2026-03-28T10:00:00Z",
        updated_at: "2026-03-28T12:00:00Z",
      },
    ],
    schema: resourceSchema("Integration Hub"),
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

  mockState.eventsStore = reactive({
    revision: 0,
    items: [] as Array<{ ev: { type: string; data: unknown } }>,
  });
});

const globalStubs = {
  BackendForm: BackendFormStub,
  ControlActionDialog: ControlActionDialogStub,
};

describe("control-plane actions", () => {
  it("dispatches dashboard node actions through backend-owned dialogs", async () => {
    const wrapper = mount(ControlDashboard, {
      global: { stubs: globalStubs },
    });
    await flushPromises();

    mockState.nodesStore.runNodeAction.mockClear();
    mockState.consoleStore.refresh.mockClear();

    const actionButton = wrapper.findAll("button").find((button) => button.text() === "Drain");
    expect(actionButton).toBeTruthy();
    await actionButton!.trigger("click");
    await wrapper.get("[data-test='dialog-submit']").trigger("click");
    await flushPromises();

    expect(mockState.nodesStore.runNodeAction).toHaveBeenCalledWith(
      "node-1",
      expect.objectContaining({ key: "drain" }),
      expect.objectContaining({ reason: "operator-confirmed" })
    );
    expect(mockState.consoleStore.refresh).toHaveBeenCalledTimes(1);
  });

  it("submits node provisioning and filtered node actions through the backend contract", async () => {
    mockState.route = reactive({
      path: "/nodes",
      fullPath: "/nodes?os=windows&attention=1",
      query: { os: "windows", attention: "1" },
    });

    const wrapper = mount(NodesView, {
      global: { stubs: globalStubs },
    });
    await flushPromises();

    mockState.nodesStore.fetchNodes.mockClear();
    mockState.nodesStore.provisionNode.mockClear();
    mockState.nodesStore.runNodeAction.mockClear();

    await wrapper.get("[data-test='backend-form-submit']").trigger("click");
    await flushPromises();

    expect(mockState.nodesStore.provisionNode).toHaveBeenCalledWith(
      expect.objectContaining({
        node_id: "node-new",
        name: "Node New",
        executor: "swift-native",
        capabilities: ["health.ingest"],
      })
    );
    expect(mockState.nodesStore.fetchNodes).toHaveBeenCalledWith({ os: "windows", attention: "1" });

    mockState.nodesStore.fetchNodes.mockClear();
    const drainButton = wrapper.findAll("button").find((button) => button.text() === "Drain");
    expect(drainButton).toBeTruthy();
    await drainButton!.trigger("click");
    await wrapper.get("[data-test='dialog-submit']").trigger("click");
    await flushPromises();

    expect(mockState.nodesStore.runNodeAction).toHaveBeenCalledWith(
      "node-1",
      expect.objectContaining({ key: "drain" }),
      expect.objectContaining({ reason: "operator-confirmed" })
    );
    expect(mockState.nodesStore.fetchNodes).toHaveBeenCalledWith({ os: "windows", attention: "1" });
  });

  it("queues jobs, runs actions, and refetches filtered data on SSE updates", async () => {
    mockState.route = reactive({
      path: "/jobs",
      fullPath: "/jobs?target_executor=swift-native",
      query: { target_executor: "swift-native" },
    });

    const wrapper = mount(JobsView, {
      global: { stubs: globalStubs },
    });
    await flushPromises();

    mockState.jobsStore.fetchJobs.mockClear();
    mockState.jobsStore.createJob.mockClear();
    mockState.jobsStore.runJobAction.mockClear();

    await wrapper.get("[data-test='backend-form-submit']").trigger("click");
    await flushPromises();

    expect(mockState.jobsStore.createJob).toHaveBeenCalledWith(
      expect.objectContaining({
        kind: "health.sync",
        connector_id: "connector-main",
        target_executor: "swift-native",
        required_capabilities: ["health.ingest"],
      }),
      { target_executor: "swift-native" }
    );

    const retryButton = wrapper.findAll("button").find((button) => button.text() === "Retry Now");
    expect(retryButton).toBeTruthy();
    await retryButton!.trigger("click");
    await wrapper.get("[data-test='dialog-submit']").trigger("click");
    await flushPromises();

    expect(mockState.jobsStore.runJobAction).toHaveBeenCalledWith(
      "job-1",
      expect.objectContaining({ key: "retry" }),
      expect.objectContaining({ reason: "operator-confirmed" })
    );
    expect(mockState.jobsStore.fetchJobs).toHaveBeenCalledWith({ target_executor: "swift-native" });

    mockState.jobsStore.fetchJobs.mockClear();
    mockState.eventsStore.items = [{ ev: { type: "job:events", data: { job_id: "job-1" } } }];
    mockState.eventsStore.revision += 1;
    await flushPromises();

    expect(mockState.jobsStore.fetchJobs).toHaveBeenCalledWith({ target_executor: "swift-native" });
  });

  it("saves connectors, runs actions, and refetches filtered connector views on SSE", async () => {
    mockState.route = reactive({
      path: "/connectors",
      fullPath: "/connectors?status=error",
      query: { status: "error" },
    });

    const wrapper = mount(ConnectorsView, {
      global: { stubs: globalStubs },
    });
    await flushPromises();

    mockState.connectorsStore.fetchConnectors.mockClear();
    mockState.connectorsStore.upsertConnector.mockClear();
    mockState.connectorsStore.runConnectorAction.mockClear();

    await wrapper.get("[data-test='backend-form-submit']").trigger("click");
    await flushPromises();

    expect(mockState.connectorsStore.upsertConnector).toHaveBeenCalledWith(
      expect.objectContaining({
        connector_id: "connector-main",
        name: "Node New",
        kind: "health.sync",
      })
    );
    expect(mockState.connectorsStore.fetchConnectors).toHaveBeenCalledWith({ status: "error" });

    mockState.connectorsStore.fetchConnectors.mockClear();
    const invokeButton = wrapper.findAll("button").find((button) => button.text() === "Invoke");
    expect(invokeButton).toBeTruthy();
    await invokeButton!.trigger("click");
    await wrapper.get("[data-test='dialog-submit']").trigger("click");
    await flushPromises();

    expect(mockState.connectorsStore.runConnectorAction).toHaveBeenCalledWith(
      "connector-main",
      expect.objectContaining({ key: "invoke" }),
      expect.objectContaining({ action: "ping", reason: "operator-confirmed" })
    );
    expect(mockState.connectorsStore.fetchConnectors).toHaveBeenCalledWith({ status: "error" });

    mockState.connectorsStore.fetchConnectors.mockClear();
    mockState.eventsStore.items = [{ ev: { type: "connector:events", data: { connector_id: "connector-main" } } }];
    mockState.eventsStore.revision += 1;
    await flushPromises();

    expect(mockState.connectorsStore.fetchConnectors).toHaveBeenCalledWith({ status: "error" });
  });
});
