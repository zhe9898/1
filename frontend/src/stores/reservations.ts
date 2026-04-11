import { defineStore } from "pinia";
import { computed, ref } from "vue";

import type { ReservationControlEvent } from "@/types/sse";
import { RESERVATIONS } from "@/utils/api";
import { http } from "@/utils/http";

export interface ReservationItem {
  job_id: string;
  node_id: string;
  start_at: string;
  end_at: string;
  priority: number;
  cpu_cores: number;
  memory_mb: number;
  gpu_vram_mb: number;
  slots: number;
}

export interface ReservationStats {
  tenant_id: string;
  active_reservations: number;
  store_backend: string;
  node_counts: Record<string, number>;
  config: Record<string, unknown>;
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

function normalizeReservation(partial: Partial<ReservationItem> & { job_id: string; node_id: string }): ReservationItem {
  return {
    job_id: partial.job_id,
    node_id: partial.node_id,
    start_at: partial.start_at ?? new Date().toISOString(),
    end_at: partial.end_at ?? new Date().toISOString(),
    priority: partial.priority ?? 0,
    cpu_cores: partial.cpu_cores ?? 0,
    memory_mb: partial.memory_mb ?? 0,
    gpu_vram_mb: partial.gpu_vram_mb ?? 0,
    slots: partial.slots ?? 1,
  };
}

function normalizeStats(stats: ReservationStats | null): ReservationStats {
  return {
    tenant_id: stats?.tenant_id ?? "",
    active_reservations: stats?.active_reservations ?? 0,
    store_backend: stats?.store_backend ?? "unknown",
    node_counts: stats?.node_counts ?? {},
    config: stats?.config ?? {},
  };
}

export const useReservationsStore = defineStore("reservations", () => {
  const items = ref<ReservationItem[]>([]);
  const stats = ref(normalizeStats(null));
  const loading = ref(false);
  const statsLoading = ref(false);
  const error = ref<string | null>(null);
  const lastUpdatedAt = ref(0);

  const nodeCountEntries = computed(() =>
    Object.entries(stats.value.node_counts).sort(([left], [right]) => left.localeCompare(right)),
  );

  function touchLastUpdatedAt(): void {
    lastUpdatedAt.value = Math.max(Date.now(), lastUpdatedAt.value + 1);
  }

  function upsertReservation(partial: Partial<ReservationItem> & { job_id: string; node_id: string }): void {
    const next = normalizeReservation(partial);
    const index = items.value.findIndex((item) => item.job_id === next.job_id);
    if (index >= 0) {
      items.value[index] = normalizeReservation({ ...items.value[index], ...partial });
    } else {
      items.value.unshift(next);
    }
    touchLastUpdatedAt();
  }

  function removeReservation(jobId: string): void {
    const nextItems = items.value.filter((item) => item.job_id !== jobId);
    if (nextItems.length !== items.value.length) {
      items.value = nextItems;
      touchLastUpdatedAt();
    }
  }

  async function fetchReservations(query: Record<string, unknown> = {}): Promise<void> {
    loading.value = true;
    error.value = null;
    try {
      const { data } = await http.get<ReservationItem[]>(RESERVATIONS.list, { params: toListParams(query) });
      items.value = data.map((item) => normalizeReservation(item));
      touchLastUpdatedAt();
    } catch (err: unknown) {
      error.value = err instanceof Error ? err.message : "Failed to load reservations";
    } finally {
      loading.value = false;
    }
  }

  async function fetchStats(): Promise<void> {
    statsLoading.value = true;
    error.value = null;
    try {
      const { data } = await http.get<ReservationStats>(RESERVATIONS.stats);
      stats.value = normalizeStats(data);
      touchLastUpdatedAt();
    } catch (err: unknown) {
      error.value = err instanceof Error ? err.message : "Failed to load reservation stats";
    } finally {
      statsLoading.value = false;
    }
  }

  function applyReservationEvent(event: ReservationControlEvent): void {
    const reservation = event.reservation;
    if (!reservation || typeof reservation !== "object") {
      return;
    }
    const jobId = (reservation as { job_id?: unknown }).job_id;
    const nodeId = (reservation as { node_id?: unknown }).node_id;
    if (typeof jobId !== "string" || !jobId || typeof nodeId !== "string" || !nodeId) {
      return;
    }
    if (event.action === "canceled" || event.action === "expired") {
      removeReservation(jobId);
      return;
    }
    upsertReservation(reservation as Partial<ReservationItem> & { job_id: string; node_id: string });
  }

  return {
    items,
    stats,
    loading,
    statsLoading,
    error,
    lastUpdatedAt,
    nodeCountEntries,
    applyReservationEvent,
    fetchReservations,
    fetchStats,
  };
});
