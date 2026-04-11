<template>
  <section class="mx-auto max-w-6xl space-y-4 px-6 py-8">
    <header class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <p class="text-sm uppercase tracking-[0.25em] text-base-content/60">
          Gateway Control Plane
        </p>
        <h1 class="mt-2 text-3xl font-semibold">
          Triggers
        </h1>
        <p class="mt-2 max-w-3xl text-sm text-base-content/70">
          Unified trigger registry, webhook ingress, and delivery history.
        </p>
      </div>
      <button
        class="btn btn-sm btn-primary"
        :disabled="store.loading"
        @click="refreshNow"
      >
        Refresh
      </button>
    </header>

    <div
      v-if="triggerCapability"
      class="alert alert-info"
    >
      <span>{{ triggerCapability.reason ?? "Trigger registry is active and live delivery events are streaming." }}</span>
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

    <div
      v-if="!store.loading && triggers.length === 0"
      class="rounded-3xl border border-base-300 bg-base-100 p-6 text-base-content/60"
    >
      No triggers match the current view.
    </div>

    <div class="grid gap-4">
      <article
        v-for="trigger in triggers"
        :key="trigger.trigger_id"
        class="card border border-base-300 bg-base-100 shadow-sm"
      >
        <div class="card-body gap-4">
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 class="card-title text-xl">
                {{ trigger.name }}
              </h2>
              <p class="font-mono text-xs text-base-content/50">
                {{ trigger.trigger_id }}
              </p>
            </div>
            <div class="flex flex-wrap gap-2">
              <span class="badge badge-outline">
                {{ trigger.kind }}
              </span>
              <span
                class="badge"
                :class="trigger.status === 'active' ? 'badge-success' : 'badge-warning'"
              >
                {{ trigger.status }}
              </span>
            </div>
          </div>

          <p
            v-if="trigger.description"
            class="text-sm text-base-content/70"
          >
            {{ trigger.description }}
          </p>

          <div class="grid gap-3 lg:grid-cols-3">
            <div class="rounded-2xl border border-base-300 bg-base-200/40 p-4">
              <p class="text-xs uppercase tracking-[0.2em] text-base-content/50">
                Target
              </p>
              <p class="mt-2 text-sm">
                Kind: {{ targetLabel(trigger.target, "target_kind") }}
              </p>
              <p class="mt-1 text-sm">
                Target ID: {{ targetIdentity(trigger.target) }}
              </p>
              <p class="mt-1 text-sm">
                Updated: {{ formatTs(trigger.updated_at) }}
              </p>
            </div>

            <div class="rounded-2xl border border-base-300 bg-base-200/40 p-4">
              <p class="text-xs uppercase tracking-[0.2em] text-base-content/50">
                Delivery
              </p>
              <p class="mt-2 text-sm">
                Last Status: {{ trigger.last_delivery_status || "-" }}
              </p>
              <p class="mt-1 text-sm">
                Last Target: {{ trigger.last_delivery_target_kind || "-" }} {{ trigger.last_delivery_target_id || "" }}
              </p>
              <p class="mt-1 text-sm">
                Last Fired: {{ formatTs(trigger.last_fired_at) }}
              </p>
              <p class="mt-1 text-sm">
                Next Run: {{ formatTs(trigger.next_run_at) }}
              </p>
            </div>

            <div class="rounded-2xl border border-base-300 bg-base-200/40 p-4">
              <p class="text-xs uppercase tracking-[0.2em] text-base-content/50">
                Operators
              </p>
              <p class="mt-2 text-sm">
                Created By: {{ trigger.created_by || "-" }}
              </p>
              <p class="mt-1 text-sm">
                Updated By: {{ trigger.updated_by || "-" }}
              </p>
              <div class="mt-3 flex flex-wrap gap-2">
                <button
                  v-if="trigger.status !== 'active'"
                  class="btn btn-sm btn-success"
                  :disabled="store.actionLoading[trigger.trigger_id]"
                  @click="setTriggerStatus(trigger.trigger_id, 'activate')"
                >
                  Activate
                </button>
                <button
                  v-if="trigger.status === 'active'"
                  class="btn btn-sm btn-warning"
                  :disabled="store.actionLoading[trigger.trigger_id]"
                  @click="setTriggerStatus(trigger.trigger_id, 'pause')"
                >
                  Pause
                </button>
                <button
                  class="btn btn-sm btn-outline"
                  :disabled="store.deliveriesLoading[trigger.trigger_id]"
                  @click="toggleDeliveries(trigger.trigger_id)"
                >
                  {{ expandedDeliveries[trigger.trigger_id] ? "Hide Deliveries" : "Show Deliveries" }}
                </button>
              </div>
            </div>
          </div>

          <div class="rounded-2xl border border-base-300 bg-base-200/40 p-4">
            <p class="text-xs uppercase tracking-[0.2em] text-base-content/50">
              Target Contract
            </p>
            <pre class="mt-2 whitespace-pre-wrap break-all rounded-xl bg-base-100 p-3 text-xs">{{ formatObject(trigger.target) }}</pre>
          </div>

          <div
            v-if="expandedDeliveries[trigger.trigger_id]"
            class="rounded-2xl border border-base-300 bg-base-200/40 p-4"
          >
            <div class="flex items-center justify-between gap-3">
              <p class="text-xs uppercase tracking-[0.2em] text-base-content/50">
                Delivery History
              </p>
              <span
                v-if="store.deliveriesLoading[trigger.trigger_id]"
                class="text-xs text-base-content/60"
              >
                Loading...
              </span>
            </div>

            <div
              v-if="triggerDeliveries(trigger.trigger_id).length === 0 && !store.deliveriesLoading[trigger.trigger_id]"
              class="mt-3 rounded-xl border border-base-300 bg-base-100 p-3 text-sm text-base-content/70"
            >
              No deliveries recorded yet.
            </div>

            <div
              v-for="delivery in triggerDeliveries(trigger.trigger_id)"
              :key="delivery.delivery_id"
              class="mt-3 rounded-xl border border-base-300 bg-base-100 p-4"
            >
              <div class="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div class="flex flex-wrap items-center gap-2">
                    <span class="badge badge-outline">{{ delivery.delivery_id }}</span>
                    <span
                      class="badge"
                      :class="delivery.status === 'delivered' ? 'badge-success' : 'badge-warning'"
                    >
                      {{ delivery.status }}
                    </span>
                  </div>
                  <p class="mt-2 text-sm text-base-content/70">
                    {{ delivery.source_kind }} -> {{ delivery.target_kind || "-" }} {{ delivery.target_id || "" }}
                  </p>
                </div>
                <div class="text-right text-sm text-base-content/65">
                  <p>Fired: {{ formatTs(delivery.fired_at) }}</p>
                  <p>Delivered: {{ formatTs(delivery.delivered_at) }}</p>
                </div>
              </div>
              <p
                v-if="delivery.error_message"
                class="mt-3 text-sm text-error"
              >
                {{ delivery.error_message }}
              </p>
            </div>
          </div>
        </div>
      </article>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, reactive, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import type { TriggerControlEvent } from "@/types/sse";
import { useCapabilitiesStore } from "@/stores/capabilities";
import { useEventsStore } from "@/stores/events";
import { useTriggersStore } from "@/stores/triggers";
import { TRIGGERS } from "@/utils/api";
import { findCapabilityByEndpoint } from "@/utils/controlPlane";

const store = useTriggersStore();
const capsStore = useCapabilitiesStore();
const eventsStore = useEventsStore();
const route = useRoute();
const router = useRouter();
const expandedDeliveries = reactive<Record<string, boolean>>({});

const triggerCapability = computed(() => findCapabilityByEndpoint(capsStore.caps, TRIGGERS.list));
const triggers = computed(() => store.items);
const listQueryParams = computed<Record<string, string>>(() => normalizeRouteQuery(route.query));
const hasActiveFilters = computed(() => Object.keys(listQueryParams.value).length > 0);
const filterLabels = computed(() => {
  const labels: string[] = [];
  if (typeof route.query.kind === "string" && route.query.kind) labels.push(`kind ${route.query.kind}`);
  if (typeof route.query.status === "string" && route.query.status) labels.push(`status ${route.query.status}`);
  return labels;
});

function formatTs(value: string | null): string {
  return value ? new Date(value).toLocaleString() : "-";
}

function formatObject(value: Record<string, unknown>): string {
  return JSON.stringify(value, null, 2);
}

function triggerDeliveries(triggerId: string) {
  return store.deliveriesByTrigger[triggerId] ?? [];
}

function targetLabel(target: Record<string, unknown>, key: string): string {
  const value = target[key];
  return typeof value === "string" && value ? value : "-";
}

function targetIdentity(target: Record<string, unknown>): string {
  const keys = ["job_kind", "template_id", "target_id"];
  for (const key of keys) {
    const value = target[key];
    if (typeof value === "string" && value) {
      return value;
    }
  }
  return "-";
}

function refreshNow(): void {
  void store.fetchTriggers(listQueryParams.value);
}

function clearFilters(): void {
  void router.push({ path: route.path, query: {} });
}

async function toggleDeliveries(triggerId: string): Promise<void> {
  expandedDeliveries[triggerId] = !expandedDeliveries[triggerId];
  if (expandedDeliveries[triggerId]) {
    await store.fetchTriggerDeliveries(triggerId, 50, true);
  }
}

async function setTriggerStatus(triggerId: string, action: "activate" | "pause"): Promise<void> {
  await store.runStatusAction(triggerId, action);
}

watch(
  () => route.fullPath,
  () => {
    void store.fetchTriggers(listQueryParams.value);
    if (Object.keys(capsStore.caps).length === 0) {
      void capsStore.fetchCapabilities();
    }
  },
  { immediate: true },
);

watch(
  () => eventsStore.revision,
  () => {
    if (eventsStore.items.length === 0) return;
    const newest = eventsStore.items[0];
    if (newest.ev.type !== "trigger:events") return;
    if (hasActiveFilters.value) {
      void store.fetchTriggers(listQueryParams.value);
      return;
    }
    const payload = newest.ev.data as TriggerControlEvent;
    store.applyTriggerEvent(payload);
    const triggerId = typeof payload.trigger?.trigger_id === "string" ? payload.trigger.trigger_id : null;
    if (triggerId && expandedDeliveries[triggerId]) {
      void store.fetchTriggerDeliveries(triggerId, 50, true);
    }
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
