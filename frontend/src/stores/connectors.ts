import { defineStore } from "pinia";
import { ref } from "vue";
import { CONNECTORS } from "@/utils/api";
import { http } from "@/utils/http";
import type { ResourceSchema } from "@/types/backendUi";
import type { ControlAction, StatusView } from "@/types/controlPlane";
import type { ConnectorControlEvent } from "@/types/sse";
import { normalizeStatusView } from "@/utils/statusView";

export interface ConnectorItem {
  connector_id: string;
  name: string;
  kind: string;
  status: string;
  status_view: StatusView;
  endpoint: string | null;
  profile: string;
  config: Record<string, unknown>;
  last_test_ok: boolean | null;
  last_test_status: string | null;
  last_test_message: string | null;
  last_test_at: string | null;
  last_invoke_status: string | null;
  last_invoke_message: string | null;
  last_invoke_job_id: string | null;
  last_invoke_at: string | null;
  attention_reason: string | null;
  actions: ControlAction[];
  created_at: string;
  updated_at: string;
}

export interface UpsertConnectorPayload {
  connector_id: string;
  name: string;
  kind: string;
  status?: string;
  endpoint?: string;
  profile?: string;
  config?: Record<string, unknown>;
}

export interface InvokeConnectorPayload {
  action: string;
  payload?: Record<string, unknown>;
  lease_seconds?: number;
}

export interface ConnectorInvokeResult {
  connector_id: string;
  accepted: boolean;
  job_id: string;
  status: string;
  message: string;
}

export interface ConnectorTestResult {
  connector_id: string;
  ok: boolean;
  endpoint: string | null;
  status: string;
  message: string;
  checked_at: string;
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

function normalizeConnector(partial: Partial<ConnectorItem> & { connector_id: string }): ConnectorItem {
  return {
    connector_id: partial.connector_id,
    name: partial.name ?? partial.connector_id,
    kind: partial.kind ?? "unknown",
    status: partial.status ?? "unknown",
    status_view: partial.status_view ?? normalizeStatusView(null, partial.status ?? "unknown", partial.status ?? "Unknown"),
    endpoint: partial.endpoint ?? null,
    profile: partial.profile ?? "manual",
    config: partial.config ?? {},
    last_test_ok: partial.last_test_ok ?? null,
    last_test_status: partial.last_test_status ?? null,
    last_test_message: partial.last_test_message ?? null,
    last_test_at: partial.last_test_at ?? null,
    last_invoke_status: partial.last_invoke_status ?? null,
    last_invoke_message: partial.last_invoke_message ?? null,
    last_invoke_job_id: partial.last_invoke_job_id ?? null,
    last_invoke_at: partial.last_invoke_at ?? null,
    attention_reason: partial.attention_reason ?? null,
    actions: partial.actions ?? [],
    created_at: partial.created_at ?? new Date().toISOString(),
    updated_at: partial.updated_at ?? new Date().toISOString(),
  };
}

export const useConnectorsStore = defineStore("connectors", () => {
  const items = ref<ConnectorItem[]>([]);
  const schema = ref<ResourceSchema | null>(null);
  const loading = ref(false);
  const submitting = ref(false);
  const actionLoading = ref<Record<string, boolean>>({});
  const error = ref<string | null>(null);
  const lastUpdatedAt = ref(0);

  function upsertConnectorLocal(partial: Partial<ConnectorItem> & { connector_id: string }): void {
    const index = items.value.findIndex((item) => item.connector_id === partial.connector_id);
    if (index >= 0) {
      items.value[index] = { ...items.value[index], ...partial };
    } else {
      items.value.unshift(normalizeConnector(partial));
    }
    lastUpdatedAt.value = Date.now();
  }

  async function fetchSchema(): Promise<ResourceSchema | null> {
    try {
      const { data } = await http.get<ResourceSchema>(CONNECTORS.schema);
      schema.value = data;
      return data;
    } catch (err: unknown) {
      error.value = err instanceof Error ? err.message : "Failed to load connector schema";
      return null;
    }
  }

  async function fetchConnectors(query: Record<string, unknown> = {}): Promise<void> {
    loading.value = true;
    error.value = null;
    try {
      const { data } = await http.get<ConnectorItem[]>(CONNECTORS.list, { params: toListParams(query) });
      items.value = data.map((item) => normalizeConnector(item));
      lastUpdatedAt.value = Date.now();
    } catch (err: unknown) {
      error.value = err instanceof Error ? err.message : "Failed to load connectors";
    } finally {
      loading.value = false;
    }
  }

  async function upsertConnector(payload: UpsertConnectorPayload): Promise<ConnectorItem | null> {
    submitting.value = true;
    error.value = null;
    try {
      const { data } = await http.post<ConnectorItem>(CONNECTORS.upsert, payload);
      upsertConnectorLocal(data);
      return normalizeConnector(data);
    } catch (err: unknown) {
      error.value = err instanceof Error ? err.message : "Failed to save connector";
      return null;
    } finally {
      submitting.value = false;
    }
  }

  async function invokeConnector(id: string, payload: InvokeConnectorPayload): Promise<ConnectorInvokeResult | null> {
    submitting.value = true;
    error.value = null;
    try {
      const { data } = await http.post<ConnectorInvokeResult>(CONNECTORS.invoke(id), payload);
      await fetchConnector(id);
      return data;
    } catch (err: unknown) {
      error.value = err instanceof Error ? err.message : "Failed to invoke connector";
      return null;
    } finally {
      submitting.value = false;
    }
  }

  async function testConnector(id: string, timeout_ms?: number): Promise<ConnectorTestResult | null> {
    try {
      const { data } = await http.post<ConnectorTestResult>(CONNECTORS.test(id), timeout_ms ? { timeout_ms } : {});
      await fetchConnector(id);
      return data;
    } catch (err: unknown) {
      error.value = err instanceof Error ? err.message : "Failed to test connector";
      return null;
    }
  }

  async function fetchConnector(id: string): Promise<ConnectorItem | null> {
    try {
      const { data } = await http.get<ConnectorItem[]>(CONNECTORS.list, { params: { connector_id: id } });
      const match = data.find((item) => item.connector_id === id) ?? null;
      if (match) {
        upsertConnectorLocal(match);
        return normalizeConnector(match);
      }
      return null;
    } catch {
      return null;
    }
  }

  async function runConnectorAction(
    connectorId: string,
    action: ControlAction,
    payload: Record<string, unknown> = {}
  ): Promise<ConnectorItem | ConnectorInvokeResult | ConnectorTestResult | null> {
    actionLoading.value = { ...actionLoading.value, [connectorId]: true };
    error.value = null;
    try {
      if (!action.enabled) {
        throw new Error(action.reason ?? "This action is currently unavailable");
      }
      if (action.key === "test") {
        return await testConnector(connectorId, typeof payload.timeout_ms === "number" ? payload.timeout_ms : undefined);
      }
      if (action.key === "invoke") {
        return await invokeConnector(connectorId, {
          action: typeof payload.action === "string" && payload.action ? payload.action : "ping",
          payload: typeof payload.payload === "object" && payload.payload != null ? (payload.payload as Record<string, unknown>) : {},
          lease_seconds: typeof payload.lease_seconds === "number" ? payload.lease_seconds : undefined,
        });
      }
      return null;
    } catch (err: unknown) {
      error.value = err instanceof Error ? err.message : "Failed to update connector";
      return null;
    } finally {
      actionLoading.value = { ...actionLoading.value, [connectorId]: false };
    }
  }

  function applyConnectorEvent(event: ConnectorControlEvent): void {
    const connector = event.connector;
    if (connector && typeof connector === "object") {
      const id = (connector as { connector_id?: unknown }).connector_id;
      if (typeof id === "string" && id) {
        upsertConnectorLocal(connector as Partial<ConnectorItem> & { connector_id: string });
        return;
      }
    }

    if (typeof event.connector_id === "string" && event.connector_id) {
      upsertConnectorLocal({
        connector_id: event.connector_id,
        status: event.status ?? "unknown",
      });
    }
  }

  return {
    items,
    schema,
    loading,
    submitting,
    actionLoading,
    error,
    lastUpdatedAt,
    applyConnectorEvent,
    fetchSchema,
    fetchConnectors,
    fetchConnector,
    upsertConnector,
    invokeConnector,
    testConnector,
    runConnectorAction,
  };
});
