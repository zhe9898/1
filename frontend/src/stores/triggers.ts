import { defineStore } from "pinia";
import { ref } from "vue";

import type { TriggerControlEvent } from "@/types/sse";
import { TRIGGERS } from "@/utils/api";
import { http } from "@/utils/http";

export interface TriggerItem {
  trigger_id: string;
  name: string;
  description: string | null;
  kind: string;
  status: string;
  config: Record<string, unknown>;
  input_defaults: Record<string, unknown>;
  target: Record<string, unknown>;
  last_fired_at: string | null;
  last_delivery_status: string | null;
  last_delivery_message: string | null;
  last_delivery_id: string | null;
  last_delivery_target_kind: string | null;
  last_delivery_target_id: string | null;
  next_run_at: string | null;
  created_by: string | null;
  updated_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface TriggerDeliveryItem {
  delivery_id: string;
  trigger_id: string;
  trigger_kind: string;
  source_kind: string;
  status: string;
  idempotency_key: string | null;
  actor: string | null;
  reason: string | null;
  input_payload: Record<string, unknown>;
  context: Record<string, unknown>;
  target_kind: string | null;
  target_id: string | null;
  target_snapshot: Record<string, unknown>;
  error_message: string | null;
  fired_at: string;
  delivered_at: string | null;
  created_at: string;
  updated_at: string;
}

function toListParams(query: Record<string, unknown> = {}): Record<string, string> {
  return Object.fromEntries(
    Object.entries(query)
      .map(([key, value]) => {
        if (typeof value === "string" && value) return [key, value];
        if (typeof value === "number" && Number.isFinite(value)) return [key, String(value)];
        if (Array.isArray(value)) {
          const first = value.find((item): item is string => typeof item === "string" && item.length > 0);
          if (first) return [key, first];
        }
        return null;
      })
      .filter((entry): entry is [string, string] => entry != null),
  );
}

function normalizeTrigger(partial: Partial<TriggerItem> & { trigger_id: string }): TriggerItem {
  return {
    trigger_id: partial.trigger_id,
    name: partial.name ?? partial.trigger_id,
    description: partial.description ?? null,
    kind: partial.kind ?? "unknown",
    status: partial.status ?? "unknown",
    config: partial.config ?? {},
    input_defaults: partial.input_defaults ?? {},
    target: partial.target ?? {},
    last_fired_at: partial.last_fired_at ?? null,
    last_delivery_status: partial.last_delivery_status ?? null,
    last_delivery_message: partial.last_delivery_message ?? null,
    last_delivery_id: partial.last_delivery_id ?? null,
    last_delivery_target_kind: partial.last_delivery_target_kind ?? null,
    last_delivery_target_id: partial.last_delivery_target_id ?? null,
    next_run_at: partial.next_run_at ?? null,
    created_by: partial.created_by ?? null,
    updated_by: partial.updated_by ?? null,
    created_at: partial.created_at ?? new Date().toISOString(),
    updated_at: partial.updated_at ?? new Date().toISOString(),
  };
}

function normalizeDelivery(
  partial: Partial<TriggerDeliveryItem> & { delivery_id: string; trigger_id: string },
): TriggerDeliveryItem {
  return {
    delivery_id: partial.delivery_id,
    trigger_id: partial.trigger_id,
    trigger_kind: partial.trigger_kind ?? "unknown",
    source_kind: partial.source_kind ?? "unknown",
    status: partial.status ?? "unknown",
    idempotency_key: partial.idempotency_key ?? null,
    actor: partial.actor ?? null,
    reason: partial.reason ?? null,
    input_payload: partial.input_payload ?? {},
    context: partial.context ?? {},
    target_kind: partial.target_kind ?? null,
    target_id: partial.target_id ?? null,
    target_snapshot: partial.target_snapshot ?? {},
    error_message: partial.error_message ?? null,
    fired_at: partial.fired_at ?? new Date().toISOString(),
    delivered_at: partial.delivered_at ?? null,
    created_at: partial.created_at ?? new Date().toISOString(),
    updated_at: partial.updated_at ?? new Date().toISOString(),
  };
}

export const useTriggersStore = defineStore("triggers", () => {
  const items = ref<TriggerItem[]>([]);
  const deliveriesByTrigger = ref<Record<string, TriggerDeliveryItem[]>>({});
  const loading = ref(false);
  const deliveriesLoading = ref<Record<string, boolean>>({});
  const actionLoading = ref<Record<string, boolean>>({});
  const error = ref<string | null>(null);
  const lastUpdatedAt = ref(0);

  function touchLastUpdatedAt(): void {
    lastUpdatedAt.value = Math.max(Date.now(), lastUpdatedAt.value + 1);
  }

  function upsertTrigger(partial: Partial<TriggerItem> & { trigger_id: string }): void {
    const normalized = normalizeTrigger(partial);
    const index = items.value.findIndex((item) => item.trigger_id === normalized.trigger_id);
    if (index >= 0) {
      items.value[index] = normalizeTrigger({ ...items.value[index], ...partial });
    } else {
      items.value.unshift(normalized);
    }
    touchLastUpdatedAt();
  }

  function upsertDelivery(partial: Partial<TriggerDeliveryItem> & { delivery_id: string; trigger_id: string }): void {
    const normalized = normalizeDelivery(partial);
    const current = deliveriesByTrigger.value[normalized.trigger_id] ?? [];
    const index = current.findIndex((item) => item.delivery_id === normalized.delivery_id);
    const next = [...current];
    if (index >= 0) {
      next[index] = normalizeDelivery({ ...next[index], ...partial });
    } else {
      next.unshift(normalized);
    }
    deliveriesByTrigger.value = { ...deliveriesByTrigger.value, [normalized.trigger_id]: next };
    touchLastUpdatedAt();
  }

  async function fetchTriggers(query: Record<string, unknown> = {}): Promise<void> {
    loading.value = true;
    error.value = null;
    try {
      const { data } = await http.get<TriggerItem[]>(TRIGGERS.list, { params: toListParams(query) });
      items.value = data.map((item) => normalizeTrigger(item));
      touchLastUpdatedAt();
    } catch (err: unknown) {
      error.value = err instanceof Error ? err.message : "Failed to load triggers";
    } finally {
      loading.value = false;
    }
  }

  async function fetchTriggerDeliveries(triggerId: string, limit = 50, force = false): Promise<TriggerDeliveryItem[]> {
    if (!force && Object.prototype.hasOwnProperty.call(deliveriesByTrigger.value, triggerId)) {
      return deliveriesByTrigger.value[triggerId];
    }
    deliveriesLoading.value = { ...deliveriesLoading.value, [triggerId]: true };
    try {
      const { data } = await http.get<TriggerDeliveryItem[]>(TRIGGERS.deliveries(triggerId), {
        params: { limit: String(limit) },
      });
      const normalized = data.map((item) => normalizeDelivery(item));
      deliveriesByTrigger.value = { ...deliveriesByTrigger.value, [triggerId]: normalized };
      touchLastUpdatedAt();
      return normalized;
    } catch (err: unknown) {
      error.value = err instanceof Error ? err.message : "Failed to load trigger deliveries";
      return [];
    } finally {
      deliveriesLoading.value = { ...deliveriesLoading.value, [triggerId]: false };
    }
  }

  async function runStatusAction(triggerId: string, action: "activate" | "pause", reason?: string): Promise<TriggerItem | null> {
    actionLoading.value = { ...actionLoading.value, [triggerId]: true };
    error.value = null;
    try {
      const endpoint = action === "activate" ? TRIGGERS.activate(triggerId) : TRIGGERS.pause(triggerId);
      const { data } = await http.post<TriggerItem>(endpoint, reason ? { reason } : {});
      upsertTrigger(data);
      return normalizeTrigger(data);
    } catch (err: unknown) {
      error.value = err instanceof Error ? err.message : `Failed to ${action} trigger`;
      return null;
    } finally {
      actionLoading.value = { ...actionLoading.value, [triggerId]: false };
    }
  }

  function applyTriggerEvent(event: TriggerControlEvent): void {
    const trigger = event.trigger;
    if (trigger && typeof trigger === "object") {
      const triggerId = (trigger as { trigger_id?: unknown }).trigger_id;
      if (typeof triggerId === "string" && triggerId) {
        upsertTrigger(trigger as Partial<TriggerItem> & { trigger_id: string });
      }
    }

    const delivery = event.delivery;
    if (!delivery || typeof delivery !== "object") {
      return;
    }
    const deliveryId = (delivery as { delivery_id?: unknown }).delivery_id;
    const triggerId =
      typeof (delivery as { trigger_id?: unknown }).trigger_id === "string"
        ? (delivery as { trigger_id: string }).trigger_id
        : typeof (trigger as { trigger_id?: unknown }).trigger_id === "string"
          ? (trigger as { trigger_id: string }).trigger_id
          : "";
    if (typeof deliveryId !== "string" || !deliveryId || !triggerId) {
      return;
    }
    upsertDelivery({
      ...delivery,
      delivery_id: deliveryId,
      trigger_id: triggerId,
    } as Partial<TriggerDeliveryItem> & { delivery_id: string; trigger_id: string });
  }

  return {
    items,
    deliveriesByTrigger,
    loading,
    deliveriesLoading,
    actionLoading,
    error,
    lastUpdatedAt,
    applyTriggerEvent,
    fetchTriggers,
    fetchTriggerDeliveries,
    runStatusAction,
  };
});
