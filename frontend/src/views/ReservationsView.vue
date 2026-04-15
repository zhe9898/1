<template>
  <section class="mx-auto max-w-6xl space-y-4 px-6 py-8">
    <header class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <p class="text-sm uppercase tracking-[0.25em] text-base-content/60">
          Gateway Control Plane
        </p>
        <h1 class="mt-2 text-3xl font-semibold">
          Reservations
        </h1>
        <p class="mt-2 max-w-3xl text-sm text-base-content/70">
          Time-dimension reservations, backfill windows, and planning diagnostics.
        </p>
      </div>
      <button
        class="btn btn-sm btn-primary"
        :disabled="store.loading || store.statsLoading"
        @click="refreshNow"
      >
        Refresh
      </button>
    </header>

    <div
      v-if="reservationCapability"
      class="alert alert-info"
    >
      <span>{{ reservationCapability.reason ?? "Reservation runtime is active and streaming control-plane updates." }}</span>
    </div>

    <div
      v-if="store.error"
      class="alert alert-error"
    >
      <span>{{ store.error }}</span>
    </div>

    <div
      v-if="filterLabels.length > 0"
      class="flex flex-wrap items-center gap-2 rounded-2xl border border-base-300 bg-base-100 px-4 py-3"
    >
      <span class="text-xs uppercase tracking-[0.2em] text-base-content/50">Active Filters</span>
      <span
        v-for="label in filterLabels"
        :key="label"
        class="badge badge-outline"
      >
        {{ label }}
      </span>
      <button
        class="btn btn-xs btn-ghost ml-auto"
        type="button"
        @click="clearFilters"
      >
        Clear
      </button>
    </div>

    <div class="stats w-full bg-base-100 shadow">
      <div class="stat">
        <div class="stat-title">
          Active Reservations
        </div>
        <div class="stat-value text-2xl">
          {{ store.stats.active_reservations }}
        </div>
      </div>
      <div class="stat">
        <div class="stat-title">
          Store Backend
        </div>
        <div class="stat-value text-lg">
          {{ store.stats.store_backend }}
        </div>
      </div>
      <div class="stat">
        <div class="stat-title">
          Tenant
        </div>
        <div class="stat-value text-lg">
          {{ store.stats.tenant_id || "-" }}
        </div>
      </div>
      <div class="stat">
        <div class="stat-title">
          Node Buckets
        </div>
        <div class="stat-value text-lg">
          {{ store.nodeCountEntries.length }}
        </div>
      </div>
    </div>

    <div
      v-if="store.nodeCountEntries.length > 0"
      class="flex flex-wrap gap-2 rounded-2xl border border-base-300 bg-base-100 px-4 py-3"
    >
      <span class="text-xs uppercase tracking-[0.2em] text-base-content/50">Node Pressure</span>
      <span
        v-for="[nodeId, count] in store.nodeCountEntries"
        :key="nodeId"
        class="badge badge-outline"
      >
        {{ nodeId }} x{{ count }}
      </span>
    </div>

    <div
      v-if="!store.loading && reservations.length === 0"
      class="rounded-3xl border border-base-300 bg-base-100 p-6 text-base-content/60"
    >
      No active reservations match the current view.
    </div>

    <div class="grid gap-4 lg:grid-cols-2">
      <article
        v-for="reservation in reservations"
        :key="reservation.job_id"
        class="card border border-base-300 bg-base-100 shadow-sm"
      >
        <div class="card-body gap-4">
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 class="card-title text-xl">
                Job {{ reservation.job_id }}
              </h2>
              <p class="font-mono text-xs text-base-content/50">
                Node {{ reservation.node_id }}
              </p>
            </div>
            <span class="badge badge-outline">
              priority {{ reservation.priority }}
            </span>
          </div>

          <div class="grid gap-3 md:grid-cols-2">
            <div class="rounded-2xl border border-base-300 bg-base-200/40 p-4">
              <p class="text-xs uppercase tracking-[0.2em] text-base-content/50">
                Window
              </p>
              <p class="mt-2 text-sm">
                Start: {{ formatTs(reservation.start_at) }}
              </p>
              <p class="mt-1 text-sm">
                End: {{ formatTs(reservation.end_at) }}
              </p>
              <p class="mt-1 text-sm">
                Duration: {{ durationText(reservation.start_at, reservation.end_at) }}
              </p>
            </div>

            <div class="rounded-2xl border border-base-300 bg-base-200/40 p-4">
              <p class="text-xs uppercase tracking-[0.2em] text-base-content/50">
                Resources
              </p>
              <p class="mt-2 text-sm">
                CPU: {{ reservation.cpu_cores }}
              </p>
              <p class="mt-1 text-sm">
                Memory: {{ reservation.memory_mb }} MB
              </p>
              <p class="mt-1 text-sm">
                GPU: {{ reservation.gpu_vram_mb }} MB
              </p>
              <p class="mt-1 text-sm">
                Slots: {{ reservation.slots }}
              </p>
            </div>
          </div>
        </div>
      </article>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import type { ReservationControlEvent } from "@/types/sse";
import { useCapabilitiesStore } from "@/stores/capabilities";
import { useEventsStore } from "@/stores/events";
import { useReservationsStore } from "@/stores/reservations";
import { RESERVATIONS } from "@/utils/api";
import { findCapabilityByEndpoint } from "@/utils/controlPlane";

const store = useReservationsStore();
const capsStore = useCapabilitiesStore();
const eventsStore = useEventsStore();
const route = useRoute();
const router = useRouter();

const reservationCapability = computed(() => findCapabilityByEndpoint(capsStore.caps, RESERVATIONS.list));
const reservations = computed(() => store.items);
const listQueryParams = computed<Record<string, string>>(() => normalizeRouteQuery(route.query));
const hasActiveFilters = computed(() => Object.keys(listQueryParams.value).length > 0);
const filterLabels = computed(() => {
  const labels: string[] = [];
  if (typeof route.query.node_id === "string" && route.query.node_id) labels.push(`node ${route.query.node_id}`);
  if (typeof route.query.after === "string" && route.query.after) labels.push(`after ${route.query.after}`);
  return labels;
});

function formatTs(value: string): string {
  return new Date(value).toLocaleString();
}

function durationText(startAt: string, endAt: string): string {
  const seconds = Math.max(0, Math.round((Date.parse(endAt) - Date.parse(startAt)) / 1000));
  return `${String(seconds)}s`;
}

function refreshNow(): void {
  void store.fetchReservations(listQueryParams.value);
  void store.fetchStats();
}

function clearFilters(): void {
  void router.push({ path: route.path, query: {} });
}

onMounted(() => {
  if (Object.keys(capsStore.caps).length === 0) {
    void capsStore.fetchCapabilities();
  }
});

watch(
  () => route.fullPath,
  () => {
    void store.fetchReservations(listQueryParams.value);
    void store.fetchStats();
  },
  { immediate: true },
);

watch(
  () => eventsStore.revision,
  () => {
    if (eventsStore.items.length === 0) return;
    const newest = eventsStore.items[0];
    if (newest.ev.type !== "reservation:events") return;
    if (hasActiveFilters.value) {
      void store.fetchReservations(listQueryParams.value);
    } else {
      store.applyReservationEvent(newest.ev.data as ReservationControlEvent);
    }
    void store.fetchStats();
  },
);

function normalizeRouteQuery(query: typeof route.query): Record<string, string> {
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
      .filter((entry): entry is [string, string] => entry != null),
  );
}
</script>
