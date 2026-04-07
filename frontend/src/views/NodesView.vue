<template>
  <section class="mx-auto max-w-6xl space-y-4 px-6 py-8">
    <header class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <p class="text-sm uppercase tracking-[0.25em] text-base-content/60">
          Gateway Control Plane
        </p>
        <h1 class="mt-2 text-3xl font-semibold">
          {{ nodeSchema?.title ?? "Nodes" }}
        </h1>
        <p
          v-if="nodeSchema?.description"
          class="mt-2 max-w-3xl text-sm text-base-content/70"
        >
          {{ nodeSchema.description }}
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
      v-if="nodeCapability"
      class="alert alert-info"
    >
      <span>{{ nodeCapability.reason ?? "Runner registry and heartbeat are active." }}</span>
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

    <div class="grid gap-4 xl:grid-cols-[24rem_minmax(0,1fr)]">
      <BackendForm
        v-if="nodeSchema"
        title="Provision Node"
        :sections="nodeSchema.sections"
        :submit-label="nodeSchema.submit_action?.label ?? 'Provision Node'"
        :submitting="store.submitting"
        @submit="submitProvision"
        @invalid="handleFormError"
      />
      <div
        v-else
        class="card border border-base-300 bg-base-100 p-4 shadow-sm"
      >
        <h2 class="text-lg font-medium">
          Provision Node
        </h2>
        <p class="mt-3 text-sm text-base-content/65">
          Loading backend node schema...
        </p>
      </div>

      <div class="space-y-4">
        <div class="stats w-full bg-base-100 shadow">
          <div class="stat">
            <div class="stat-title">
              Total
            </div>
            <div class="stat-value text-2xl">
              {{ filteredNodes.length }}
            </div>
          </div>
          <div class="stat">
            <div class="stat-title">
              Online
            </div>
            <div class="stat-value text-2xl text-success">
              {{ onlineCount }}
            </div>
          </div>
          <div class="stat">
            <div class="stat-title">
              Draining
            </div>
            <div class="stat-value text-2xl text-warning">
              {{ drainingCount }}
            </div>
          </div>
          <div class="stat">
            <div class="stat-title">
              Last Refresh
            </div>
            <div class="stat-value text-lg">
              {{ lastUpdatedText }}
            </div>
          </div>
        </div>

        <div
          v-if="provisioned"
          class="rounded-[1.75rem] border border-emerald-200 bg-emerald-50 p-5 text-emerald-950 shadow-sm"
        >
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p class="text-xs uppercase tracking-[0.22em] text-emerald-800/75">
                One-Time Credential
              </p>
              <h2 class="mt-2 text-2xl font-semibold">
                {{ provisioned.node.name }}
              </h2>
              <p class="mt-1 font-mono text-xs text-emerald-900/75">
                {{ provisioned.node.node_id }}
              </p>
            </div>
            <button
              class="btn btn-sm"
              type="button"
              @click="store.clearProvisionedSecret()"
            >
              Dismiss
            </button>
          </div>

          <p class="mt-4 text-sm">
            Copy the token now. It will not be shown again after this page state is cleared.
          </p>
          <pre class="mt-3 whitespace-pre-wrap break-all rounded-2xl bg-white/80 p-4 text-xs">{{ provisioned.node_token }}</pre>
          <ul
            v-if="provisioned.bootstrap_notes.length > 0"
            class="mt-4 space-y-2 text-sm"
          >
            <li
              v-for="note in provisioned.bootstrap_notes"
              :key="note"
            >
              {{ note }}
            </li>
          </ul>

          <div
            v-if="provisioned.bootstrap_receipts.length > 0"
            class="mt-4 grid gap-4 xl:grid-cols-2"
          >
            <div
              v-for="receipt in provisioned.bootstrap_receipts"
              :key="receipt.key"
              class="rounded-2xl border border-emerald-200 bg-white/80 p-4"
            >
              <p class="text-xs uppercase tracking-[0.2em] text-emerald-800/75">
                {{ receipt.label }}
              </p>
              <pre class="mt-2 whitespace-pre-wrap break-all text-xs">{{ receipt.content }}</pre>
              <ul
                v-if="receipt.notes.length > 0"
                class="mt-3 space-y-1 text-xs text-emerald-900/80"
              >
                <li
                  v-for="note in receipt.notes"
                  :key="`${receipt.key}:${note}`"
                >
                  {{ note }}
                </li>
              </ul>
            </div>
          </div>
          <div
            v-else
            class="mt-4 grid gap-4 xl:grid-cols-2"
          >
            <div class="rounded-2xl border border-emerald-200 bg-white/80 p-4">
              <p class="text-xs uppercase tracking-[0.2em] text-emerald-800/75">
                PowerShell
              </p>
              <pre class="mt-2 whitespace-pre-wrap break-all text-xs">{{ provisioned.bootstrap_commands.powershell || "" }}</pre>
            </div>
            <div class="rounded-2xl border border-emerald-200 bg-white/80 p-4">
              <p class="text-xs uppercase tracking-[0.2em] text-emerald-800/75">
                macOS / Linux
              </p>
              <pre class="mt-2 whitespace-pre-wrap break-all text-xs">{{ provisioned.bootstrap_commands.unix || "" }}</pre>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div
      v-if="!store.loading && filteredNodes.length === 0"
      class="rounded-3xl border border-base-300 bg-base-100 p-6 text-base-content/60"
    >
      {{ nodeSchema?.empty_state ?? "No nodes match the current view." }}
    </div>

    <div class="grid gap-4 lg:grid-cols-2">
      <article
        v-for="node in filteredNodes"
        :key="node.node_id"
        class="card border border-base-300 bg-base-100 shadow-sm"
      >
        <div class="card-body gap-4">
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 class="card-title text-xl">
                {{ node.name }}
              </h2>
              <p class="font-mono text-xs text-base-content/50">
                {{ node.node_id }}
              </p>
            </div>
            <div class="flex flex-wrap gap-2">
              <span
                class="badge"
                :class="badgeClassFromTone(node.status_view.tone)"
              >
                {{ node.status_view.label }}
              </span>
              <span
                class="badge"
                :class="badgeClassFromTone(node.drain_status_view.tone)"
              >
                {{ node.drain_status_view.label }}
              </span>
            </div>
          </div>

          <div class="flex flex-wrap gap-2">
            <button
              v-for="action in node.actions"
              :key="`${node.node_id}:${action.key}`"
              class="btn btn-sm"
              :class="actionButtonClass(action.key)"
              type="button"
              :title="action.reason ?? ''"
              :disabled="store.actionLoading[node.node_id] || !action.enabled"
              @click="requestNodeAction(node.node_id, action)"
            >
              {{ action.label }}
            </button>
          </div>

          <div class="grid gap-3 md:grid-cols-2">
            <div class="rounded-2xl border border-base-300 bg-base-200/40 p-4">
              <p class="text-xs uppercase tracking-[0.2em] text-base-content/50">
                Runtime
              </p>
              <p class="mt-2 text-sm">
                Type: {{ node.node_type }}
              </p>
              <p class="mt-1 text-sm">
                Profile: {{ node.profile }}
              </p>
              <p class="mt-1 text-sm">
                Executor: {{ node.executor }}
              </p>
              <p class="mt-1 text-sm">
                Platform: {{ node.os }}/{{ node.arch }}
              </p>
              <p class="mt-1 text-sm">
                Zone: {{ node.zone || "-" }}
              </p>
              <p class="mt-1 text-sm">
                Address: {{ node.address || "-" }}
              </p>
              <p class="mt-1 text-sm">
                Agent: {{ node.agent_version || "-" }}
              </p>
              <p class="mt-1 text-sm">
                Last Seen: {{ formatTs(node.last_seen_at) }}
              </p>
              <p class="mt-1 text-sm">
                Heartbeat: {{ node.heartbeat_state_view.label }}
              </p>
              <p class="mt-1 text-sm">
                Enrollment: {{ node.enrollment_status_view.label }}
              </p>
            </div>

            <div class="rounded-2xl border border-base-300 bg-base-200/40 p-4">
              <p class="text-xs uppercase tracking-[0.2em] text-base-content/50">
                Capacity
              </p>
              <p class="mt-2 text-sm">
                Active Leases: {{ node.active_lease_count }}
              </p>
              <p class="mt-1 text-sm">
                Max Concurrency: {{ node.max_concurrency }}
              </p>
              <p class="mt-1 text-sm">
                CPU / Memory: {{ node.cpu_cores || 0 }} cores / {{ node.memory_mb || 0 }} MB
              </p>
              <p class="mt-1 text-sm">
                GPU / Storage: {{ node.gpu_vram_mb || 0 }} MB / {{ node.storage_mb || 0 }} MB
              </p>
              <p class="mt-1 text-sm">
                Drain Status: {{ node.drain_status_view.label }}
              </p>
              <p class="mt-1 text-sm">
                Health Reason: {{ node.health_reason || "-" }}
              </p>
              <p class="mt-1 text-sm">
                Capacity State: {{ node.capacity_state_view.label }}
              </p>
              <p class="mt-1 text-sm">
                Attention: {{ node.attention_reason || "-" }}
              </p>
              <p class="mt-1 text-sm">
                Lease Contract: {{ node.lease_version }}
              </p>
              <p class="mt-1 text-sm">
                Runner Contract: {{ node.protocol_version }}
              </p>
            </div>
          </div>

          <div class="rounded-2xl border border-base-300 bg-base-200/40 p-4">
            <p class="text-xs uppercase tracking-[0.2em] text-base-content/50">
              Capabilities
            </p>
            <div class="mt-2 flex flex-wrap gap-2">
              <span
                v-for="capability in node.capabilities"
                :key="capability"
                class="badge badge-outline"
              >
                {{ capability }}
              </span>
              <span
                v-if="node.capabilities.length === 0"
                class="text-sm text-base-content/60"
              >
                -
              </span>
            </div>
          </div>

          <div class="rounded-2xl border border-base-300 bg-base-200/40 p-4">
            <p class="text-xs uppercase tracking-[0.2em] text-base-content/50">
              Metadata
            </p>
            <div
              v-if="metadataEntries(node.metadata).length > 0"
              class="mt-2 grid gap-2 md:grid-cols-2"
            >
              <div
                v-for="[key, value] in metadataEntries(node.metadata)"
                :key="key"
                class="rounded-xl bg-base-100 px-3 py-2"
              >
                <p class="text-xs text-base-content/50">
                  {{ key }}
                </p>
                <p class="font-mono text-xs">
                  {{ value }}
                </p>
              </div>
            </div>
            <p
              v-else
              class="mt-2 text-sm text-base-content/60"
            >
              No metadata reported.
            </p>
          </div>
        </div>
      </article>
    </div>

    <ControlActionDialog
      :action="pendingAction"
      :submitting="activeActionSubmitting"
      @close="closeActionDialog"
      @invalid="handleFormError"
      @submit="submitNodeAction"
    />
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import BackendForm from "@/components/control/BackendForm.vue";
import ControlActionDialog from "@/components/control/ControlActionDialog.vue";
import { useCapabilitiesStore } from "@/stores/capabilities";
import { useEventsStore } from "@/stores/events";
import { useNodesStore } from "@/stores/nodes";
import { NODES } from "@/utils/api";
import { findCapabilityByEndpoint } from "@/utils/controlPlane";
import { badgeClassFromTone } from "@/utils/statusView";
import type { ControlAction } from "@/types/controlPlane";
import type { NodeControlEvent } from "@/types/sse";

const store = useNodesStore();
const capsStore = useCapabilitiesStore();
const eventsStore = useEventsStore();
const route = useRoute();
const router = useRouter();

const nodeCapability = computed(() => findCapabilityByEndpoint(capsStore.caps, NODES.list));
const nodeSchema = computed(() => store.schema);
const provisioned = computed(() => store.lastProvisioned);
const schemaPolicyBadges = computed(() => {
  const policies = nodeSchema.value?.policies ?? {};
  const badges: string[] = [];
  if (typeof policies.resource_mode === "string" && policies.resource_mode) {
    badges.push(policies.resource_mode);
  }
  if (typeof policies.ui_mode === "string" && policies.ui_mode) {
    badges.push(policies.ui_mode);
  }
  const secretDelivery = policies.secret_delivery;
  if (
    typeof secretDelivery === "object" &&
    secretDelivery != null &&
    typeof (secretDelivery as { visibility?: unknown }).visibility === "string"
  ) {
    badges.push(`secret ${(secretDelivery as { visibility: string }).visibility}`);
  }
  return badges;
});
const pendingAction = ref<ControlAction | null>(null);
const pendingNodeId = ref<string | null>(null);
const listQueryParams = computed<Record<string, string>>(() => normalizeRouteQuery(route.query));
const hasActiveFilters = computed(() => Object.keys(listQueryParams.value).length > 0);

const lastUpdatedText = computed(() => {
  if (!store.lastUpdatedAt) return "-";
  return new Date(store.lastUpdatedAt).toLocaleTimeString();
});

const filteredNodes = computed(() => store.items);
const filterLabels = computed(() => {
  const labels: string[] = [];
  if (typeof route.query.node_id === "string" && route.query.node_id) labels.push(`node ${route.query.node_id}`);
  if (typeof route.query.node_type === "string" && route.query.node_type) labels.push(`type ${route.query.node_type}`);
  if (typeof route.query.executor === "string" && route.query.executor) labels.push(`executor ${route.query.executor}`);
  if (typeof route.query.os === "string" && route.query.os) labels.push(`os ${route.query.os}`);
  if (typeof route.query.zone === "string" && route.query.zone) labels.push(`zone ${route.query.zone}`);
  if (typeof route.query.enrollment_status === "string" && route.query.enrollment_status) {
    labels.push(`enrollment ${route.query.enrollment_status}`);
  }
  if (typeof route.query.drain_status === "string" && route.query.drain_status) {
    labels.push(`drain ${route.query.drain_status}`);
  }
  if (typeof route.query.heartbeat_state === "string" && route.query.heartbeat_state) {
    labels.push(`heartbeat ${route.query.heartbeat_state}`);
  }
  if (typeof route.query.capacity_state === "string" && route.query.capacity_state) {
    labels.push(`capacity ${route.query.capacity_state}`);
  }
  if (typeof route.query.attention === "string" && route.query.attention) labels.push("attention only");
  return labels;
});
const drainingCount = computed(() => filteredNodes.value.filter((item) => item.drain_status_view.key !== "active").length);
const onlineCount = computed(() => filteredNodes.value.filter((item) => item.status_view.key === "online").length);
const activeActionSubmitting = computed(() => {
  const nodeId = pendingNodeId.value;
  return nodeId ? store.actionLoading[nodeId] : false;
});

function formatTs(ts: string): string {
  if (!ts) return "-";
  return new Date(ts).toLocaleString();
}

function metadataEntries(metadata: Record<string, unknown>): [string, string][] {
  return Object.entries(metadata).map(([key, value]) => [key, typeof value === "string" ? value : JSON.stringify(value)]);
}

function actionButtonClass(actionKey: string): string {
  if (actionKey === "revoke") return "btn-error";
  if (actionKey === "rotate_token") return "btn-primary";
  return "btn-outline";
}

function refreshNow(): void {
  void store.fetchNodes(listQueryParams.value);
}

function clearFilters(): void {
  void router.push({ path: route.path, query: {} });
}

function handleFormError(message: string): void {
  store.error = message;
}

async function submitProvision(payload: Record<string, unknown>): Promise<void> {
  await store.provisionNode({
    node_id: typeof payload.node_id === "string" ? payload.node_id : "",
    name: typeof payload.name === "string" ? payload.name : "",
    node_type: typeof payload.node_type === "string" && payload.node_type ? payload.node_type : undefined,
    address: typeof payload.address === "string" && payload.address ? payload.address : undefined,
    profile: typeof payload.profile === "string" && payload.profile ? payload.profile : undefined,
    executor: typeof payload.executor === "string" && payload.executor ? payload.executor : undefined,
    os: typeof payload.os === "string" && payload.os ? payload.os : undefined,
    arch: typeof payload.arch === "string" && payload.arch ? payload.arch : undefined,
    zone: typeof payload.zone === "string" && payload.zone ? payload.zone : undefined,
    protocol_version:
      typeof payload.protocol_version === "string" && payload.protocol_version ? payload.protocol_version : undefined,
    lease_version: typeof payload.lease_version === "string" && payload.lease_version ? payload.lease_version : undefined,
    agent_version: typeof payload.agent_version === "string" && payload.agent_version ? payload.agent_version : undefined,
    max_concurrency: typeof payload.max_concurrency === "number" ? payload.max_concurrency : undefined,
    cpu_cores: typeof payload.cpu_cores === "number" ? payload.cpu_cores : undefined,
    memory_mb: typeof payload.memory_mb === "number" ? payload.memory_mb : undefined,
    gpu_vram_mb: typeof payload.gpu_vram_mb === "number" ? payload.gpu_vram_mb : undefined,
    storage_mb: typeof payload.storage_mb === "number" ? payload.storage_mb : undefined,
    capabilities: Array.isArray(payload.capabilities)
      ? payload.capabilities.filter((item): item is string => typeof item === "string" && item.length > 0)
      : undefined,
    metadata:
      typeof payload.metadata === "object" && payload.metadata != null
        ? (payload.metadata as Record<string, unknown>)
        : undefined,
  });
  if (hasActiveFilters.value) {
    await store.fetchNodes(listQueryParams.value);
  }
}

function requestNodeAction(nodeId: string, action: ControlAction): void {
  if (!action.enabled) return;
  pendingNodeId.value = nodeId;
  pendingAction.value = action;
}

function closeActionDialog(): void {
  pendingAction.value = null;
  pendingNodeId.value = null;
}

async function submitNodeAction(payload: Record<string, unknown>): Promise<void> {
  if (!pendingAction.value || !pendingNodeId.value) {
    return;
  }
  await store.runNodeAction(pendingNodeId.value, pendingAction.value, payload);
  if (hasActiveFilters.value) {
    await store.fetchNodes(listQueryParams.value);
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
    void store.fetchNodes(listQueryParams.value);
  },
  { immediate: true }
);

watch(
  () => eventsStore.revision,
  () => {
    if (eventsStore.items.length === 0) return;
    const newest = eventsStore.items[0];
    if (newest.ev.type === "node:events") {
      if (hasActiveFilters.value) {
        void store.fetchNodes(listQueryParams.value);
        return;
      }
      store.applyNodeEvent(newest.ev.data as NodeControlEvent);
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
