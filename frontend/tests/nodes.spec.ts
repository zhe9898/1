// @vitest-environment jsdom
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import MockAdapter from "axios-mock-adapter";
import { createPinia, setActivePinia } from "pinia";

import { http } from "../src/utils/http";
import { useNodesStore } from "../src/stores/nodes";
import type { NodeItem, NodeProvisionReceipt, BootstrapReceipt } from "../src/stores/nodes";
import type { ControlAction } from "../src/types/controlPlane";

vi.mock("../src/utils/requestId", () => ({ getRequestId: () => "test-req" }));
vi.mock("../src/utils/logger", () => ({
  logInfo: vi.fn(),
  logWarn: vi.fn(),
  logError: vi.fn(),
}));

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeStatusView(key: string, label: string) {
  return { key, label, tone: "info" };
}

function makeNode(overrides: Partial<NodeItem> = {}): NodeItem {
  return {
    node_id: "node-001",
    name: "test-node",
    node_type: "runner",
    address: null,
    profile: "default",
    executor: "docker",
    os: "linux",
    arch: "amd64",
    zone: null,
    protocol_version: "1.0",
    lease_version: "1.0",
    agent_version: null,
    max_concurrency: 4,
    active_lease_count: 0,
    cpu_cores: 8,
    memory_mb: 16384,
    gpu_vram_mb: 0,
    storage_mb: 102400,
    drain_status: "active",
    drain_status_view: makeStatusView("active", "Active"),
    health_reason: null,
    heartbeat_state: "fresh",
    heartbeat_state_view: makeStatusView("fresh", "Fresh"),
    capacity_state: "available",
    capacity_state_view: makeStatusView("available", "Available"),
    attention_reason: null,
    enrollment_status: "enrolled",
    enrollment_status_view: makeStatusView("enrolled", "Enrolled"),
    status: "online",
    status_view: makeStatusView("online", "Online"),
    capabilities: [],
    metadata: {},
    actions: [],
    registered_at: "2025-01-01T00:00:00Z",
    last_seen_at: "2025-01-01T00:01:00Z",
    ...overrides,
  };
}

function makeProvisionReceipt(node: NodeItem = makeNode()): NodeProvisionReceipt {
  const receipt: BootstrapReceipt = {
    key: "linux-amd64",
    label: "Linux x86-64",
    platform: "linux",
    kind: "shell",
    content: "#!/bin/bash\necho 'bootstrap'",
    notes: ["Run as root"],
  };
  return {
    node,
    node_token: "secret-token",
    auth_token_version: 1,
    bootstrap_commands: { linux: "curl -s | bash" },
    bootstrap_notes: ["Configure firewall"],
    bootstrap_receipts: [receipt],
  };
}

function makeAction(key: string, endpoint: string, enabled = true): ControlAction {
  return {
    key,
    label: key,
    endpoint,
    method: "POST",
    enabled,
    requires_admin: true,
    reason: enabled ? null : "Action unavailable",
    confirmation: null,
    fields: [],
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("useNodesStore", () => {
  let mock: MockAdapter;

  beforeEach(() => {
    setActivePinia(createPinia());
    mock = new MockAdapter(http);
  });

  afterEach(() => {
    mock.restore();
  });

  // ---- initial state ----
  it("starts with empty state", () => {
    const store = useNodesStore();
    expect(store.items).toEqual([]);
    expect(store.loading).toBe(false);
    expect(store.submitting).toBe(false);
    expect(store.error).toBeNull();
    expect(store.lastProvisioned).toBeNull();
    expect(store.onlineCount).toBe(0);
  });

  // ---- upsertNode ----
  it("upsertNode inserts a new node at the front", () => {
    const store = useNodesStore();
    store.upsertNode({ node_id: "node-001" });
    expect(store.items).toHaveLength(1);
    expect(store.items[0].node_id).toBe("node-001");
  });

  it("upsertNode updates an existing node by id", () => {
    const store = useNodesStore();
    store.upsertNode({ node_id: "node-001", status: "online" });
    store.upsertNode({ node_id: "node-001", status: "offline" });
    expect(store.items).toHaveLength(1);
    expect(store.items[0].status).toBe("offline");
  });

  it("upsertNode normalizes missing fields to safe defaults", () => {
    const store = useNodesStore();
    store.upsertNode({ node_id: "node-001" });
    const node = store.items[0];
    expect(node.name).toBe("node-001"); // defaults to node_id
    expect(node.executor).toBe("unknown");
    expect(node.max_concurrency).toBe(1);
    expect(node.capabilities).toEqual([]);
    expect(node.drain_status).toBe("active");
    expect(node.enrollment_status).toBe("pending");
  });

  it("upsertNode prepends: newer nodes appear first", () => {
    const store = useNodesStore();
    store.upsertNode({ node_id: "first" });
    store.upsertNode({ node_id: "second" });
    expect(store.items[0].node_id).toBe("second");
  });

  // ---- onlineCount computed ----
  it("onlineCount counts only nodes with status_view.key === 'online'", () => {
    const store = useNodesStore();
    store.upsertNode({ node_id: "n1", status_view: makeStatusView("online", "Online") });
    store.upsertNode({ node_id: "n2", status_view: makeStatusView("online", "Online") });
    store.upsertNode({ node_id: "n3", status_view: makeStatusView("offline", "Offline") });
    expect(store.onlineCount).toBe(2);
  });

  // ---- clearProvisionedSecret ----
  it("clearProvisionedSecret sets lastProvisioned to null", () => {
    const store = useNodesStore();
    // Directly set the ref via a provisionNode call simulation is complex; set via store's public interface
    // We can test clearProvisionedSecret by ensuring it clears after provisionNode
    mock.onPost("/v1/nodes").reply(200, makeProvisionReceipt());
    store.provisionNode({ node_id: "node-001", name: "test" }).then(() => {
      store.clearProvisionedSecret();
      expect(store.lastProvisioned).toBeNull();
    });
  });

  // ---- fetchSchema ----
  it("fetchSchema stores schema on success", async () => {
    const store = useNodesStore();
    const schema = { product: "ZEN70", resource: "nodes", title: "Nodes", profile: "gw", runtime_profile: "gw", columns: [], actions: [], filters: [] };
    mock.onGet("/v1/nodes/schema").reply(200, schema);
    const result = await store.fetchSchema();
    expect(result).toEqual(schema);
    expect(store.schema).toEqual(schema);
  });

  it("fetchSchema returns null on error", async () => {
    const store = useNodesStore();
    mock.onGet("/v1/nodes/schema").reply(500);
    const result = await store.fetchSchema();
    expect(result).toBeNull();
    expect(store.error).toBeTruthy();
  });

  // ---- fetchNodes ----
  it("fetchNodes populates items on success", async () => {
    const store = useNodesStore();
    mock.onGet("/v1/nodes").reply(200, [makeNode()]);
    await store.fetchNodes();
    expect(store.items).toHaveLength(1);
    expect(store.loading).toBe(false);
    expect(store.error).toBeNull();
  });

  it("fetchNodes sets error on failure", async () => {
    const store = useNodesStore();
    mock.onGet("/v1/nodes").reply(503);
    await store.fetchNodes();
    expect(store.items).toEqual([]);
    expect(store.error).toBeTruthy();
  });

  it("fetchNodes passes query params as strings", async () => {
    const store = useNodesStore();
    mock.onGet("/v1/nodes").reply((config) => {
      expect(config.params?.status).toBe("online");
      return [200, []];
    });
    await store.fetchNodes({ status: "online" });
  });

  it("fetchNodes serializes numeric limit and offset to string query params", async () => {
    const store = useNodesStore();
    mock.onGet("/v1/nodes").reply((config) => {
      expect(config.params?.limit).toBe("20");
      expect(config.params?.offset).toBe("40");
      return [200, []];
    });
    await store.fetchNodes({ limit: 20, offset: 40 });
  });

  // ---- fetchNode ----
  it("fetchNode returns and upserts node on success", async () => {
    const store = useNodesStore();
    mock.onGet("/v1/nodes/node-001").reply(200, makeNode({ node_id: "node-001" }));
    const result = await store.fetchNode("node-001");
    expect(result?.node_id).toBe("node-001");
    expect(store.items).toHaveLength(1);
  });

  it("fetchNode returns null on error", async () => {
    const store = useNodesStore();
    mock.onGet("/v1/nodes/node-001").reply(404);
    const result = await store.fetchNode("node-001");
    expect(result).toBeNull();
  });

  // ---- provisionNode ----
  it("provisionNode creates node, stores receipt, and returns it", async () => {
    const store = useNodesStore();
    const receipt = makeProvisionReceipt();
    mock.onPost("/v1/nodes").reply(200, receipt);
    const result = await store.provisionNode({ node_id: "node-001", name: "test" });
    expect(result?.node_token).toBe("secret-token");
    expect(store.lastProvisioned?.node_token).toBe("secret-token");
    expect(store.items.some((n) => n.node_id === "node-001")).toBe(true);
    expect(store.submitting).toBe(false);
  });

  it("provisionNode normalizes bootstrap arrays defensively", async () => {
    const store = useNodesStore();
    // Receipt with extra garbage entries in bootstrap_notes/receipts
    const receipt = {
      ...makeProvisionReceipt(),
      bootstrap_notes: ["valid", null, 42],
      bootstrap_receipts: [
        { key: "k", label: "l", platform: "p", kind: "shell", content: "c", notes: [] },
        { key: 123 }, // invalid — should be filtered out
      ],
    };
    mock.onPost("/v1/nodes").reply(200, receipt);
    const result = await store.provisionNode({ node_id: "node-001", name: "test" });
    expect(result?.bootstrap_notes).toEqual(["valid"]);
    expect(result?.bootstrap_receipts).toHaveLength(1);
  });

  it("provisionNode returns null and sets error when response is invalid", async () => {
    const store = useNodesStore();
    // Missing required node_token field — isProvisionReceipt will return false
    mock.onPost("/v1/nodes").reply(200, { node: makeNode() });
    const result = await store.provisionNode({ node_id: "node-001", name: "test" });
    expect(result).toBeNull();
    expect(store.error).toBeTruthy();
  });

  it("provisionNode returns null and sets error on HTTP failure", async () => {
    const store = useNodesStore();
    mock.onPost("/v1/nodes").reply(422, { detail: "Invalid payload" });
    const result = await store.provisionNode({ node_id: "node-001", name: "test" });
    expect(result).toBeNull();
    expect(store.error).toBeTruthy();
    expect(store.submitting).toBe(false);
  });

  // ---- runNodeAction ----
  it("runNodeAction with disabled action sets error and returns null", async () => {
    const store = useNodesStore();
    const action = makeAction("drain", "/v1/nodes/node-001/drain", false);
    const result = await store.runNodeAction("node-001", action);
    expect(result).toBeNull();
    expect(store.error).toBeTruthy();
  });

  it("runNodeAction returns updated NodeItem on success", async () => {
    const store = useNodesStore();
    const updated = makeNode({ node_id: "node-001", drain_status: "draining" });
    mock.onPost("/v1/nodes/node-001/drain").reply(200, updated);
    const action = makeAction("drain", "/v1/nodes/node-001/drain");
    const result = await store.runNodeAction("node-001", action);
    expect((result as NodeItem | null)?.drain_status).toBe("draining");
    expect(store.items.some((n) => n.node_id === "node-001")).toBe(true);
  });

  it("runNodeAction recognizes a provision receipt response and stores it", async () => {
    const store = useNodesStore();
    const receipt = makeProvisionReceipt();
    mock.onPost("/v1/nodes/node-001/token").reply(200, receipt);
    const action = makeAction("rotate-token", "/v1/nodes/node-001/token");
    const result = await store.runNodeAction("node-001", action);
    expect((result as NodeProvisionReceipt | null)?.node_token).toBe("secret-token");
    expect(store.lastProvisioned?.node_token).toBe("secret-token");
  });

  it("runNodeAction sets error and returns null on HTTP failure", async () => {
    const store = useNodesStore();
    mock.onPost("/v1/nodes/node-001/drain").reply(500);
    const action = makeAction("drain", "/v1/nodes/node-001/drain");
    const result = await store.runNodeAction("node-001", action);
    expect(result).toBeNull();
    expect(store.error).toBeTruthy();
  });

  // ---- drainNode / undrainNode ----
  it("drainNode resolves to updated node on success", async () => {
    const store = useNodesStore();
    const updated = makeNode({ node_id: "node-001", drain_status: "draining" });
    mock.onPost("/v1/nodes/node-001/drain").reply(200, updated);
    const result = await store.drainNode("node-001");
    expect(result?.drain_status).toBe("draining");
  });

  it("undrainNode resolves to updated node on success", async () => {
    const store = useNodesStore();
    const updated = makeNode({ node_id: "node-001", drain_status: "active" });
    mock.onPost("/v1/nodes/node-001/undrain").reply(200, updated);
    const result = await store.undrainNode("node-001");
    expect(result?.drain_status).toBe("active");
  });

  // ---- applyNodeEvent ----
  it("applyNodeEvent upserts node from event", () => {
    const store = useNodesStore();
    store.applyNodeEvent({ node: { node_id: "node-001", status: "offline" } });
    expect(store.items.some((n) => n.node_id === "node-001" && n.status === "offline")).toBe(true);
  });

  it("applyNodeEvent ignores events with no node field", () => {
    const store = useNodesStore();
    store.applyNodeEvent({ node: null as unknown as Record<string, unknown> });
    expect(store.items).toHaveLength(0);
  });

  it("applyNodeEvent ignores events with no node_id", () => {
    const store = useNodesStore();
    store.applyNodeEvent({ node: {} });
    expect(store.items).toHaveLength(0);
  });

  // ---- lastUpdatedAt ----
  it("lastUpdatedAt advances after upsertNode", () => {
    const store = useNodesStore();
    const before = store.lastUpdatedAt;
    store.upsertNode({ node_id: "node-001" });
    expect(store.lastUpdatedAt).toBeGreaterThanOrEqual(before);
  });

  it("lastUpdatedAt advances after successful fetchNodes", async () => {
    const store = useNodesStore();
    const before = store.lastUpdatedAt;
    mock.onGet("/v1/nodes").reply(200, [makeNode()]);
    await store.fetchNodes();
    expect(store.lastUpdatedAt).toBeGreaterThan(before);
  });
});
