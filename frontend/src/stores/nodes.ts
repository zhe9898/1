import { defineStore } from "pinia";
import { computed, ref } from "vue";
import { NODES } from "@/utils/api";
import { http } from "@/utils/http";
import type { ResourceSchema } from "@/types/backendUi";
import type { ControlAction, StatusView } from "@/types/controlPlane";
import type { NodeControlEvent } from "@/types/sse";
import { normalizeStatusView } from "@/utils/statusView";

export interface NodeItem {
  node_id: string;
  name: string;
  node_type: string;
  address: string | null;
  profile: string;
  executor: string;
  os: string;
  arch: string;
  zone: string | null;
  protocol_version: string;
  lease_version: string;
  agent_version: string | null;
  max_concurrency: number;
  active_lease_count: number;
  cpu_cores: number;
  memory_mb: number;
  gpu_vram_mb: number;
  storage_mb: number;
  drain_status: string;
  drain_status_view: StatusView;
  health_reason: string | null;
  heartbeat_state: string;
  heartbeat_state_view: StatusView;
  capacity_state: string;
  capacity_state_view: StatusView;
  attention_reason: string | null;
  enrollment_status: string;
  enrollment_status_view: StatusView;
  status: string;
  status_view: StatusView;
  capabilities: string[];
  metadata: Record<string, unknown>;
  actions: ControlAction[];
  registered_at: string;
  last_seen_at: string;
}

export interface ProvisionNodePayload {
  node_id: string;
  name: string;
  node_type?: string;
  address?: string;
  profile?: string;
  executor?: string;
  os?: string;
  arch?: string;
  zone?: string;
  protocol_version?: string;
  lease_version?: string;
  agent_version?: string;
  max_concurrency?: number;
  cpu_cores?: number;
  memory_mb?: number;
  gpu_vram_mb?: number;
  storage_mb?: number;
  capabilities?: string[];
  metadata?: Record<string, unknown>;
}

export interface BootstrapReceipt {
  key: string;
  label: string;
  platform: string;
  kind: string;
  content: string;
  notes: string[];
}

export interface NodeProvisionReceipt {
  node: NodeItem;
  node_token: string;
  auth_token_version: number;
  bootstrap_commands: Record<string, string>;
  bootstrap_notes: string[];
  bootstrap_receipts: BootstrapReceipt[];
}

function toListParams(query: Record<string, unknown> = {}): Record<string, string> {
  return Object.fromEntries(
    Object.entries(query)
      .map(([key, value]) => {
        if (typeof value === "string" && value) return [key, value];
        if (Array.isArray(value)) {
          const first = value.find((item): item is string => typeof item === "string" && item.length > 0);
          if (first) return [key, first];
        }
        return null;
      })
      .filter((entry): entry is [string, string] => entry != null)
  );
}

function normalizeNode(partial: Partial<NodeItem> & { node_id: string }): NodeItem {
  return {
    node_id: partial.node_id,
    name: partial.name ?? partial.node_id,
    node_type: partial.node_type ?? "runner",
    address: partial.address ?? null,
    profile: partial.profile ?? "unknown",
    executor: partial.executor ?? "unknown",
    os: partial.os ?? "unknown",
    arch: partial.arch ?? "unknown",
    zone: partial.zone ?? null,
    protocol_version: partial.protocol_version ?? "unknown",
    lease_version: partial.lease_version ?? "unknown",
    agent_version: partial.agent_version ?? null,
    max_concurrency: partial.max_concurrency ?? 1,
    active_lease_count: partial.active_lease_count ?? 0,
    cpu_cores: partial.cpu_cores ?? 0,
    memory_mb: partial.memory_mb ?? 0,
    gpu_vram_mb: partial.gpu_vram_mb ?? 0,
    storage_mb: partial.storage_mb ?? 0,
    drain_status: partial.drain_status ?? "active",
    drain_status_view:
      partial.drain_status_view ?? normalizeStatusView(null, partial.drain_status ?? "active", partial.drain_status ?? "Active", "info"),
    health_reason: partial.health_reason ?? null,
    heartbeat_state: partial.heartbeat_state ?? "fresh",
    heartbeat_state_view:
      partial.heartbeat_state_view ?? normalizeStatusView(null, partial.heartbeat_state ?? "fresh", partial.heartbeat_state ?? "Fresh"),
    capacity_state: partial.capacity_state ?? "available",
    capacity_state_view:
      partial.capacity_state_view ??
      normalizeStatusView(null, partial.capacity_state ?? "available", partial.capacity_state ?? "Available"),
    attention_reason: partial.attention_reason ?? null,
    enrollment_status: partial.enrollment_status ?? "pending",
    enrollment_status_view:
      partial.enrollment_status_view ??
      normalizeStatusView(null, partial.enrollment_status ?? "pending", partial.enrollment_status ?? "Pending"),
    status: partial.status ?? "unknown",
    status_view: partial.status_view ?? normalizeStatusView(null, partial.status ?? "unknown", partial.status ?? "Unknown"),
    capabilities: partial.capabilities ?? [],
    metadata: partial.metadata ?? {},
    actions: partial.actions ?? [],
    registered_at: partial.registered_at ?? new Date().toISOString(),
    last_seen_at: partial.last_seen_at ?? new Date().toISOString(),
  };
}

function isProvisionReceipt(value: unknown): value is NodeProvisionReceipt {
  if (typeof value !== "object" || value == null) return false;
  const receipt = value as Record<string, unknown>;
  const bootstrapCommands = receipt.bootstrap_commands;
  const bootstrapNotes = receipt.bootstrap_notes;
  const bootstrapReceipts = receipt.bootstrap_receipts;
  return (
    typeof receipt.node_token === "string" &&
    typeof receipt.auth_token_version === "number" &&
    typeof receipt.node === "object" &&
    receipt.node !== null &&
    typeof (receipt.node as { node_id?: unknown }).node_id === "string" &&
    typeof bootstrapCommands === "object" &&
    bootstrapCommands !== null &&
    Object.values(bootstrapCommands as Record<string, unknown>).every((item) => typeof item === "string") &&
    Array.isArray(bootstrapNotes) &&
    bootstrapNotes.every((item) => typeof item === "string") &&
    Array.isArray(bootstrapReceipts)
  );
}

export const useNodesStore = defineStore("nodes", () => {
  const items = ref<NodeItem[]>([]);
  const schema = ref<ResourceSchema | null>(null);
  const loading = ref(false);
  const submitting = ref(false);
  const actionLoading = ref<Record<string, boolean>>({});
  const error = ref<string | null>(null);
  const lastProvisioned = ref<NodeProvisionReceipt | null>(null);
  const lastUpdatedAt = ref(0);

  const onlineCount = computed(() => items.value.filter((item) => item.status_view.key === "online").length);

  function upsertNode(partial: Partial<NodeItem> & { node_id: string }): void {
    const index = items.value.findIndex((item) => item.node_id === partial.node_id);
    if (index >= 0) {
      items.value[index] = { ...items.value[index], ...partial };
    } else {
      items.value.unshift(normalizeNode(partial));
    }
    lastUpdatedAt.value = Date.now();
  }

  function clearProvisionedSecret(): void {
    lastProvisioned.value = null;
  }

  async function fetchSchema(): Promise<ResourceSchema | null> {
    try {
      const { data } = await http.get<ResourceSchema>(NODES.schema);
      schema.value = data;
      return data;
    } catch (err: unknown) {
      error.value = err instanceof Error ? err.message : "Failed to load node schema";
      return null;
    }
  }

  async function fetchNodes(query: Record<string, unknown> = {}): Promise<void> {
    loading.value = true;
    error.value = null;
    try {
      const { data } = await http.get<NodeItem[]>(NODES.list, { params: toListParams(query) });
      items.value = data.map((item) => normalizeNode(item));
      lastUpdatedAt.value = Date.now();
    } catch (err: unknown) {
      error.value = err instanceof Error ? err.message : "Failed to load nodes";
    } finally {
      loading.value = false;
    }
  }

  async function fetchNode(id: string): Promise<NodeItem | null> {
    try {
      const { data } = await http.get<NodeItem>(NODES.detail(id));
      upsertNode(data);
      return data;
    } catch {
      return null;
    }
  }

  async function provisionNode(payload: ProvisionNodePayload): Promise<NodeProvisionReceipt | null> {
    submitting.value = true;
    error.value = null;
    try {
      const { data } = await http.post<NodeProvisionReceipt>(NODES.provision, payload);
      if (!isProvisionReceipt(data)) {
        throw new Error("Node provisioning response is invalid");
      }
      upsertNode(data.node);
      lastProvisioned.value = {
        ...data,
        node: normalizeNode(data.node),
        bootstrap_commands: { ...data.bootstrap_commands },
        bootstrap_notes: Array.isArray(data.bootstrap_notes)
          ? data.bootstrap_notes.filter((item): item is string => typeof item === "string")
          : [],
        bootstrap_receipts: Array.isArray(data.bootstrap_receipts)
          ? data.bootstrap_receipts.filter(
              (item): item is BootstrapReceipt =>
                typeof item === "object" &&
                typeof (item as { key?: unknown }).key === "string" &&
                typeof (item as { label?: unknown }).label === "string" &&
                typeof (item as { platform?: unknown }).platform === "string" &&
                typeof (item as { kind?: unknown }).kind === "string" &&
                typeof (item as { content?: unknown }).content === "string" &&
                Array.isArray((item as { notes?: unknown[] }).notes)
            )
          : [],
      };
      return lastProvisioned.value;
    } catch (err: unknown) {
      error.value = err instanceof Error ? err.message : "Failed to provision node";
      return null;
    } finally {
      submitting.value = false;
    }
  }

  async function runNodeAction(
    nodeId: string,
    action: ControlAction,
    payload: Record<string, unknown> = {}
  ): Promise<NodeItem | NodeProvisionReceipt | null> {
    actionLoading.value = { ...actionLoading.value, [nodeId]: true };
    error.value = null;
    try {
      if (!action.enabled) {
        throw new Error(action.reason ?? "This action is currently unavailable");
      }
      const { data } = await http.request<NodeItem | NodeProvisionReceipt>({
        url: action.endpoint,
        method: action.method,
        data: action.method.toUpperCase() === "POST" ? payload : undefined,
      });
      if (isProvisionReceipt(data)) {
        upsertNode(data.node);
        lastProvisioned.value = {
          ...data,
          node: normalizeNode(data.node),
          bootstrap_commands: { ...data.bootstrap_commands },
          bootstrap_notes: Array.isArray(data.bootstrap_notes)
            ? data.bootstrap_notes.filter((item): item is string => typeof item === "string")
            : [],
          bootstrap_receipts: Array.isArray(data.bootstrap_receipts)
            ? data.bootstrap_receipts.filter(
                (item): item is BootstrapReceipt =>
                  typeof item === "object" &&
                  typeof (item as { key?: unknown }).key === "string" &&
                  typeof (item as { label?: unknown }).label === "string" &&
                  typeof (item as { platform?: unknown }).platform === "string" &&
                  typeof (item as { kind?: unknown }).kind === "string" &&
                  typeof (item as { content?: unknown }).content === "string" &&
                  Array.isArray((item as { notes?: unknown[] }).notes)
              )
            : [],
        };
        return lastProvisioned.value;
      }
      upsertNode(data);
      return data;
    } catch (err: unknown) {
      error.value = err instanceof Error ? err.message : "Failed to update node";
      return null;
    } finally {
      actionLoading.value = { ...actionLoading.value, [nodeId]: false };
    }
  }

  async function drainNode(nodeId: string, reason?: string): Promise<NodeItem | null> {
    const response = await runNodeAction(
      nodeId,
      {
        key: "drain",
        label: "Drain",
        endpoint: NODES.drain(nodeId),
        method: "POST",
        enabled: true,
        requires_admin: true,
        reason: null,
        confirmation: null,
        fields: [],
      },
      { reason }
    );
    return response && "node_id" in response ? response : null;
  }

  async function undrainNode(nodeId: string, reason?: string): Promise<NodeItem | null> {
    const response = await runNodeAction(
      nodeId,
      {
        key: "undrain",
        label: "Undrain",
        endpoint: NODES.undrain(nodeId),
        method: "POST",
        enabled: true,
        requires_admin: true,
        reason: null,
        confirmation: null,
        fields: [],
      },
      { reason }
    );
    return response && "node_id" in response ? response : null;
  }

  function applyNodeEvent(event: NodeControlEvent): void {
    const node = event.node;
    if (!node || typeof node !== "object") return;
    const nodeId = (node as { node_id?: unknown }).node_id;
    if (typeof nodeId !== "string" || !nodeId) return;
    upsertNode(node as Partial<NodeItem> & { node_id: string });
  }

  return {
    items,
    schema,
    loading,
    submitting,
    actionLoading,
    error,
    lastProvisioned,
    lastUpdatedAt,
    onlineCount,
    applyNodeEvent,
    clearProvisionedSecret,
    fetchSchema,
    fetchNodes,
    fetchNode,
    provisionNode,
    drainNode,
    undrainNode,
    runNodeAction,
  };
});
