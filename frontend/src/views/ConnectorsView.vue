<template>
  <section class="mx-auto max-w-6xl space-y-4 px-6 py-8">
    <header class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <p class="text-sm uppercase tracking-[0.25em] text-base-content/60">
          Gateway Control Plane
        </p>
        <h1 class="mt-2 text-3xl font-semibold">
          {{ connectorSchema?.title ?? "Connectors" }}
        </h1>
        <p
          v-if="connectorSchema?.description"
          class="mt-2 max-w-3xl text-sm text-base-content/70"
        >
          {{ connectorSchema.description }}
        </p>
        <div
          v-if="schemaPolicyBadges.length > 0"
          class="mt-3 flex flex-wrap gap-2"
        >
          <span
            v-for="badge in schemaPolicyBadges"
            :key="badge"
            class="badge badge-outline"
          >
            {{ badge }}
          </span>
        </div>
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
      v-if="connectorCapability"
      class="alert alert-info"
    >
      <span>{{ connectorCapability.reason ?? "Connector registry and invoke/test entrypoints are active." }}</span>
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

    <BackendForm
      v-if="connectorSchema"
      title="Save Connector"
      :sections="connectorSchema.sections"
      :submit-label="connectorSchema.submit_action?.label ?? 'Save Connector'"
      :submitting="store.submitting"
      @submit="submitUpsert"
      @invalid="handleFormError"
    />
    <div
      v-else
      class="card border border-base-300 bg-base-100 p-4 shadow-sm"
    >
      <h2 class="text-lg font-medium">
        Save Connector
      </h2>
      <p class="mt-3 text-sm text-base-content/65">
        Loading backend connector schema...
      </p>
    </div>

    <div
      v-if="!store.loading && filteredConnectors.length === 0"
      class="rounded-3xl border border-base-300 bg-base-100 p-6 text-base-content/60"
    >
      {{ connectorSchema?.empty_state ?? "No connectors match the current view." }}
    </div>

    <div class="grid gap-4 lg:grid-cols-2">
      <article
        v-for="item in filteredConnectors"
        :key="item.connector_id"
        class="card border border-base-300 bg-base-100 shadow-sm"
      >
        <div class="card-body gap-4">
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 class="card-title text-xl">
                {{ item.name }}
              </h2>
              <p class="font-mono text-xs text-base-content/50">
                {{ item.connector_id }}
              </p>
            </div>
            <span
              class="badge"
              :class="badgeClassFromTone(item.status_view.tone)"
            >
              {{ item.status_view.label }}
            </span>
          </div>

          <div class="grid gap-3 md:grid-cols-2">
            <div class="rounded-2xl border border-base-300 bg-base-200/40 p-4">
              <p class="text-xs uppercase tracking-[0.2em] text-base-content/50">
                Runtime
              </p>
              <p class="mt-2 text-sm">
                Kind: {{ item.kind }}
              </p>
              <p class="mt-1 text-sm">
                Profile: {{ item.profile }}
              </p>
              <p class="mt-1 text-sm break-all">
                Endpoint: {{ item.endpoint || "-" }}
              </p>
              <p class="mt-1 text-sm">
                Updated: {{ formatTs(item.updated_at) }}
              </p>
            </div>

            <div class="rounded-2xl border border-base-300 bg-base-200/40 p-4">
              <p class="text-xs uppercase tracking-[0.2em] text-base-content/50">
                Health
              </p>
              <p class="mt-2 text-sm">
                Test: {{ item.last_test_message || "-" }}
              </p>
              <p class="mt-1 text-sm">
                Invoke: {{ item.last_invoke_message || "-" }}
              </p>
              <p class="mt-1 text-sm">
                Attention: {{ item.attention_reason || "-" }}
              </p>
            </div>
          </div>

          <div class="rounded-2xl border border-base-300 bg-base-200/40 p-4">
            <p class="text-xs uppercase tracking-[0.2em] text-base-content/50">
              Config
            </p>
            <pre class="mt-2 whitespace-pre-wrap break-all rounded-xl bg-base-100 p-3 text-xs">{{ formatObject(item.config) }}</pre>
          </div>

          <div class="flex flex-wrap gap-2">
            <button
              v-for="action in item.actions"
              :key="`${item.connector_id}:${action.key}`"
              class="btn btn-sm"
              :class="action.key === 'invoke' ? 'btn-primary' : ''"
              :disabled="store.submitting || store.actionLoading[item.connector_id] || !action.enabled"
              :title="action.reason ?? ''"
              @click="executeConnectorAction(item.connector_id, action)"
            >
              {{ action.label }}
            </button>
          </div>
        </div>
      </article>
    </div>

    <ControlActionDialog
      :action="pendingAction"
      :submitting="activeActionSubmitting"
      @close="closeActionDialog"
      @invalid="handleFormError"
      @submit="submitConnectorAction"
    />
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import BackendForm from "@/components/control/BackendForm.vue";
import ControlActionDialog from "@/components/control/ControlActionDialog.vue";
import { useCapabilitiesStore } from "@/stores/capabilities";
import { useConnectorsStore } from "@/stores/connectors";
import { useEventsStore } from "@/stores/events";
import { CONNECTORS } from "@/utils/api";
import { findCapabilityByEndpoint } from "@/utils/controlPlane";
import { badgeClassFromTone } from "@/utils/statusView";
import type { ControlAction } from "@/types/controlPlane";
import type { ConnectorControlEvent } from "@/types/sse";

const store = useConnectorsStore();
const capsStore = useCapabilitiesStore();
const eventsStore = useEventsStore();
const route = useRoute();
const router = useRouter();
const pendingAction = ref<ControlAction | null>(null);
const pendingConnectorId = ref<string | null>(null);
const listQueryParams = computed<Record<string, string>>(() => normalizeRouteQuery(route.query));
const hasActiveFilters = computed(() => Object.keys(listQueryParams.value).length > 0);

const connectorCapability = computed(() => findCapabilityByEndpoint(capsStore.caps, CONNECTORS.list));
const connectorSchema = computed(() => store.schema);
const schemaPolicyBadges = computed(() => {
  const policies = connectorSchema.value?.policies ?? {};
  const badges: string[] = [];
  if (typeof policies.resource_mode === "string" && policies.resource_mode) {
    badges.push(policies.resource_mode);
  }
  if (typeof policies.ui_mode === "string" && policies.ui_mode) {
    badges.push(policies.ui_mode);
  }
  return badges;
});
const filteredConnectors = computed(() => store.items);
const filterLabels = computed(() => {
  const labels: string[] = [];
  if (typeof route.query.connector_id === "string" && route.query.connector_id) {
    labels.push(`connector ${route.query.connector_id}`);
  }
  if (typeof route.query.status === "string" && route.query.status) {
    labels.push(`status ${route.query.status}`);
  }
  if (typeof route.query.attention === "string" && route.query.attention) {
    labels.push("attention only");
  }
  return labels;
});
const activeActionSubmitting = computed(() => {
  const connectorId = pendingConnectorId.value;
  return connectorId ? store.actionLoading[connectorId] || store.submitting : false;
});

function formatTs(ts: string): string {
  if (!ts) return "-";
  return new Date(ts).toLocaleString();
}

function formatObject(value: Record<string, unknown>): string {
  if (Object.keys(value).length === 0) return "{}";
  return JSON.stringify(value, null, 2);
}

function refreshNow(): void {
  void store.fetchConnectors(listQueryParams.value);
}

function clearFilters(): void {
  void router.push({ path: route.path, query: {} });
}

function handleFormError(message: string): void {
  store.error = message;
}

async function submitUpsert(payload: Record<string, unknown>): Promise<void> {
  await store.upsertConnector({
    connector_id: typeof payload.connector_id === "string" ? payload.connector_id : "",
    name: typeof payload.name === "string" ? payload.name : "",
    kind: typeof payload.kind === "string" ? payload.kind : "",
    status: typeof payload.status === "string" && payload.status ? payload.status : undefined,
    endpoint: typeof payload.endpoint === "string" && payload.endpoint ? payload.endpoint : undefined,
    profile: typeof payload.profile === "string" && payload.profile ? payload.profile : undefined,
    config:
      typeof payload.config === "object" && payload.config != null
        ? (payload.config as Record<string, unknown>)
        : undefined,
  });
  if (hasActiveFilters.value) {
    await store.fetchConnectors(listQueryParams.value);
  }
}

function executeConnectorAction(connectorId: string, action: ControlAction): void {
  if (!action.enabled) return;
  pendingConnectorId.value = connectorId;
  pendingAction.value = action;
}

function closeActionDialog(): void {
  pendingAction.value = null;
  pendingConnectorId.value = null;
}

async function submitConnectorAction(payload: Record<string, unknown>): Promise<void> {
  if (!pendingAction.value || !pendingConnectorId.value) {
    return;
  }
  await store.runConnectorAction(pendingConnectorId.value, pendingAction.value, payload);
  if (hasActiveFilters.value) {
    await store.fetchConnectors(listQueryParams.value);
  }
  closeActionDialog();
}

onMounted(() => {
  if (Object.keys(capsStore.caps).length === 0) {
    void capsStore.fetchCapabilities();
  }
  void store.fetchSchema();
});

watch(
  () => route.fullPath,
  () => {
    void store.fetchConnectors(listQueryParams.value);
  },
  { immediate: true }
);

watch(
  () => eventsStore.revision,
  () => {
    if (eventsStore.items.length === 0) return;
    const newest = eventsStore.items[0];
    if (newest.ev.type === "connector:events") {
      if (hasActiveFilters.value) {
        void store.fetchConnectors(listQueryParams.value);
        return;
      }
      store.applyConnectorEvent(newest.ev.data as ConnectorControlEvent);
    }
  }
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
      .filter((entry): entry is [string, string] => entry != null)
  );
}
</script>
