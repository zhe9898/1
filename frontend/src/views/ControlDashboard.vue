<template>
  <section class="relative overflow-hidden px-6 py-8">
    <div class="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_left,rgba(14,165,233,0.12),transparent_32%),radial-gradient(circle_at_bottom_right,rgba(16,185,129,0.14),transparent_28%)]" />
    <div class="relative mx-auto max-w-6xl space-y-6">
      <header class="rounded-[2rem] border border-base-300 bg-base-100/90 p-6 shadow-sm backdrop-blur">
        <p class="text-xs uppercase tracking-[0.32em] text-base-content/50">
          Gateway Control Plane
        </p>
        <div class="mt-3 flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 class="text-4xl font-semibold tracking-tight">
              Operational Overview
            </h1>
            <p class="mt-2 max-w-3xl text-sm text-base-content/70">
              Dashboard now reflects cluster pressure, unhealthy runners, blocked work, and connector readiness from backend-driven overview and diagnostics data.
            </p>
          </div>
          <div class="text-right text-sm text-base-content/60">
            <p>Profile: {{ consoleStore.profile?.profile ?? "-" }}</p>
            <p>Updated: {{ generatedAtText }}</p>
          </div>
        </div>
      </header>

      <div class="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <component
          :is="card.route ? RouterLink : 'article'"
          v-for="card in summaryCards"
          :key="card.key"
          :to="card.route ? toRoute(card.route) : undefined"
          class="rounded-[1.75rem] border p-5 shadow-sm transition hover:-translate-y-0.5"
          :class="surfaceClassFromTone(card.tone_view.tone)"
        >
          <p class="text-xs uppercase tracking-[0.22em] text-base-content/55">
            {{ card.kicker }}
          </p>
          <div class="mt-4 flex items-end justify-between gap-3">
            <div>
              <p class="text-4xl font-semibold">
                {{ card.value }}
              </p>
              <p class="mt-2 text-sm text-base-content/70">
                {{ card.title }}
              </p>
            </div>
            <span
              class="badge badge-lg border-0"
              :class="solidBadgeClassFromTone(card.tone_view.tone)"
            >
              {{ card.badge }}
            </span>
          </div>
          <p class="mt-4 text-sm text-base-content/65">
            {{ card.detail }}
          </p>
        </component>
      </div>

      <section class="rounded-[2rem] border border-base-300 bg-base-100 p-5 shadow-sm">
        <div class="flex items-center justify-between gap-3">
          <div>
            <p class="text-xs uppercase tracking-[0.22em] text-base-content/55">
              Optional Packs
            </p>
            <h2 class="mt-2 text-2xl font-semibold">
              Business Boundaries
            </h2>
            <p class="mt-2 max-w-3xl text-sm text-base-content/70">
              Packs stay outside the default kernel ingress path. Dispatch uses capability, zone, and selector contracts instead of pushing business execution into the gateway process.
            </p>
          </div>
          <div class="text-right text-xs uppercase tracking-[0.18em] text-base-content/50">
            selected {{ selectedPackCount }}
          </div>
        </div>
        <div class="mt-5 grid gap-3 lg:grid-cols-2 xl:grid-cols-3">
          <article
            v-for="pack in packCards"
            :key="pack.pack_key"
            class="rounded-[1.5rem] border border-base-300 bg-base-200/30 p-4"
          >
            <div class="flex items-start justify-between gap-3">
              <div>
                <p class="font-medium">
                  {{ pack.label }}
                </p>
                <p class="mt-1 text-xs uppercase tracking-[0.18em] text-base-content/50">
                  {{ pack.category }} · {{ pack.runtime_owner }} · {{ pack.delivery_stage }}
                </p>
              </div>
              <span
                class="badge border-0"
                :class="solidBadgeClassFromTone(pack.status_view.tone)"
              >
                {{ pack.status_view.label }}
              </span>
            </div>
            <p class="mt-3 text-sm text-base-content/70">
              {{ pack.description }}
            </p>
            <p class="mt-2 text-xs text-base-content/55">
              {{ pack.deployment_boundary }}
            </p>
            <div class="mt-3 flex flex-wrap gap-2">
              <span
                v-for="service in pack.services"
                :key="`${pack.pack_key}:service:${service}`"
                class="badge badge-outline"
              >
                svc {{ service }}
              </span>
              <span
                v-for="routerName in pack.router_names"
                :key="`${pack.pack_key}:router:${routerName}`"
                class="badge badge-outline"
              >
                route {{ routerName }}
              </span>
            </div>
            <div class="mt-3 flex flex-wrap gap-2">
              <span
                v-for="hint in pack.selector_hints"
                :key="`${pack.pack_key}:hint:${hint}`"
                class="badge"
                :class="badgeClassFromTone(pack.status_view.tone)"
              >
                {{ hint }}
              </span>
            </div>
          </article>
        </div>
      </section>

      <div class="grid gap-4 xl:grid-cols-[minmax(0,1.1fr)_minmax(0,0.9fr)]">
        <section class="rounded-[2rem] border border-base-300 bg-base-100 p-5 shadow-sm">
          <div class="flex items-center justify-between gap-3">
            <div>
              <p class="text-xs uppercase tracking-[0.22em] text-base-content/55">
                Attention Queue
              </p>
              <h2 class="mt-2 text-2xl font-semibold">
                What Needs Action
              </h2>
            </div>
            <button
              class="btn btn-sm btn-primary"
              :disabled="consoleStore.loading"
              @click="refreshNow"
            >
              Refresh
            </button>
          </div>

          <div
            v-if="attentionItems.length === 0"
            class="mt-6 rounded-2xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-900"
          >
            No operational alerts right now.
          </div>

          <div
            v-else
            class="mt-6 space-y-3"
          >
            <RouterLink
              v-for="item in attentionItems"
              :key="`${item.title}:${item.route.route_path}`"
              :to="toRoute(item.route)"
              class="block rounded-2xl border border-base-300 bg-base-200/40 p-4 transition hover:border-base-content/20 hover:bg-base-200/70"
            >
              <div class="flex items-start justify-between gap-3">
                <div>
                  <div class="flex flex-wrap items-center gap-2">
                    <span
                      class="badge badge-sm border-0"
                      :class="solidBadgeClassFromTone(item.severity_view.tone)"
                    >
                      {{ item.severity_view.label }}
                    </span>
                    <span class="text-lg font-medium">{{ item.title }}</span>
                  </div>
                  <p class="mt-2 text-sm text-base-content/70">
                    {{ item.reason }}
                  </p>
                </div>
                <div class="text-right">
                  <p class="text-2xl font-semibold">
                    {{ item.count }}
                  </p>
                  <p class="text-xs uppercase tracking-[0.18em] text-base-content/50">
                    items
                  </p>
                </div>
              </div>
            </RouterLink>
          </div>
        </section>

        <section class="rounded-[2rem] border border-base-300 bg-base-100 p-5 shadow-sm">
          <p class="text-xs uppercase tracking-[0.22em] text-base-content/55">
            Console Surfaces
          </p>
          <h2 class="mt-2 text-2xl font-semibold">
            Control Areas
          </h2>
          <div class="mt-5 grid gap-3">
            <RouterLink
              v-for="item in cards"
              :key="item.routeName"
              :to="item.routePath"
              class="rounded-2xl border border-base-300 bg-base-200/30 p-4 transition hover:-translate-y-0.5 hover:shadow-sm"
              :class="{ 'pointer-events-none opacity-60': !item.enabled }"
            >
              <div class="flex items-center justify-between gap-3">
                <div>
                  <p class="font-medium">
                    {{ item.title }}
                  </p>
                  <p class="mt-1 text-sm text-base-content/65">
                    {{ item.description }}
                  </p>
                </div>
                <span
                  class="badge border-0"
                  :class="item.enabled ? 'bg-emerald-100 text-emerald-900' : 'bg-base-300 text-base-content/70'"
                >
                  {{ item.status }}
                </span>
              </div>
            </RouterLink>
          </div>
        </section>
      </div>

      <div class="grid gap-4 md:grid-cols-2 2xl:grid-cols-4">
        <section class="rounded-[2rem] border border-base-300 bg-base-100 p-5 shadow-sm">
          <div class="flex items-center justify-between gap-3">
            <div>
              <p class="text-xs uppercase tracking-[0.22em] text-base-content/55">
                Node Reliability
              </p>
              <h2 class="mt-2 text-2xl font-semibold">
                Fleet Pressure
              </h2>
            </div>
            <RouterLink
              class="btn btn-sm btn-outline"
              to="/nodes"
            >
              Open Nodes
            </RouterLink>
          </div>
          <div
            v-if="nodeHealth.length === 0"
            class="mt-5 rounded-2xl border border-base-300 bg-base-200/30 p-4 text-sm text-base-content/65"
          >
            No node diagnostics reported yet.
          </div>
          <div
            v-else
            class="mt-5 space-y-3"
          >
            <article
              v-for="node in nodeHealth"
              :key="node.node_id"
              class="rounded-2xl border border-base-300 bg-base-200/30 p-4"
            >
              <div class="flex items-start justify-between gap-3">
                <div>
                  <p class="font-medium">
                    {{ node.name }}
                  </p>
                  <p class="mt-1 font-mono text-xs text-base-content/55">
                    {{ node.node_id }}
                  </p>
                </div>
                <div class="flex flex-wrap justify-end gap-2">
                  <span
                    class="badge"
                    :class="badgeClassFromTone(node.heartbeat_state_view.tone)"
                  >{{ node.heartbeat_state_view.label }}</span>
                  <span
                    class="badge"
                    :class="badgeClassFromTone(node.capacity_state_view.tone)"
                  >{{ node.capacity_state_view.label }}</span>
                  <span
                    class="badge"
                    :class="badgeClassFromTone(node.drain_status_view.tone)"
                  >{{ node.drain_status_view.label }}</span>
                </div>
              </div>
              <p class="mt-3 text-sm text-base-content/70">
                {{ node.attention_reason || "No immediate operator action required." }}
              </p>
              <p class="mt-2 text-xs text-base-content/55">
                {{ node.node_type }} | {{ node.executor }} | {{ node.os }}/{{ node.arch }}{{ node.zone ? ` @ ${node.zone}` : "" }}
              </p>
              <p class="mt-1 text-xs text-base-content/55">
                reliability {{ node.reliability_score.toFixed(2) }} | leases {{ node.active_lease_count }}/{{ node.max_concurrency }} | cpu {{ node.cpu_cores }} | mem {{ node.memory_mb }} MB | gpu {{ node.gpu_vram_mb }} MB | seen {{ formatTs(node.last_seen_at) }}
              </p>
              <div class="mt-3 flex flex-wrap gap-2">
                <button
                  v-for="action in node.actions"
                  :key="`${node.node_id}:${action.key}`"
                  class="btn btn-xs btn-outline"
                  type="button"
                  :disabled="!action.enabled"
                  :title="action.reason ?? ''"
                  @click="openActionDialog('node', node.node_id, action)"
                >
                  {{ action.label }}
                </button>
                <RouterLink
                  class="btn btn-xs btn-outline"
                  :to="toRoute(node.route)"
                >
                  Open Fleet
                </RouterLink>
              </div>
            </article>
          </div>
        </section>

        <section class="rounded-[2rem] border border-base-300 bg-base-100 p-5 shadow-sm">
          <div class="flex items-center justify-between gap-3">
            <div>
              <p class="text-xs uppercase tracking-[0.22em] text-base-content/55">
                Connector Readiness
              </p>
              <h2 class="mt-2 text-2xl font-semibold">
                Integration Health
              </h2>
            </div>
            <RouterLink
              class="btn btn-sm btn-outline"
              to="/connectors"
            >
              Open Connectors
            </RouterLink>
          </div>
          <div
            v-if="connectorHealth.length === 0"
            class="mt-5 rounded-2xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-900"
          >
            No connector diagnostics need operator attention.
          </div>
          <div
            v-else
            class="mt-5 space-y-3"
          >
            <article
              v-for="connector in connectorHealth"
              :key="connector.connector_id"
              class="rounded-2xl border border-base-300 bg-base-200/30 p-4"
            >
              <div class="flex items-start justify-between gap-3">
                <div>
                  <p class="font-medium">
                    {{ connector.name }}
                  </p>
                  <p class="mt-1 font-mono text-xs text-base-content/55">
                    {{ connector.connector_id }}
                  </p>
                </div>
                <span
                  class="badge"
                  :class="badgeClassFromTone(connector.status_view.tone)"
                >
                  {{ connector.status_view.label }}
                </span>
              </div>
              <p class="mt-3 text-sm text-base-content/70">
                {{ connector.attention_reason || connector.last_test_message || connector.last_invoke_message || "No immediate operator action required." }}
              </p>
              <p class="mt-2 text-xs text-base-content/55">
                test {{ connector.last_test_status || "-" }} | invoke {{ connector.last_invoke_status || "-" }} | updated {{ formatTs(connector.updated_at) }}
              </p>
              <div class="mt-3 flex flex-wrap gap-2">
                <button
                  v-for="action in connector.actions"
                  :key="`${connector.connector_id}:${action.key}`"
                  class="btn btn-xs"
                  :class="action.key === 'invoke' ? 'btn-primary' : 'btn-outline'"
                  type="button"
                  :disabled="!action.enabled"
                  :title="action.reason ?? ''"
                  @click="openActionDialog('connector', connector.connector_id, action)"
                >
                  {{ action.label }}
                </button>
                <RouterLink
                  class="btn btn-xs btn-outline"
                  :to="toRoute(connector.route)"
                >
                  Open Connector
                </RouterLink>
              </div>
            </article>
          </div>
        </section>

        <section class="rounded-[2rem] border border-base-300 bg-base-100 p-5 shadow-sm">
          <div class="flex items-center justify-between gap-3">
            <div>
              <p class="text-xs uppercase tracking-[0.22em] text-base-content/55">
                Placement Diagnostics
              </p>
              <h2 class="mt-2 text-2xl font-semibold">
                Blocked Work
              </h2>
            </div>
            <RouterLink
              class="btn btn-sm btn-outline"
              to="/jobs"
            >
              Open Jobs
            </RouterLink>
          </div>
          <div
            v-if="unschedulableJobs.length === 0"
            class="mt-5 rounded-2xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-900"
          >
            No unschedulable jobs in the current backlog.
          </div>
          <div
            v-else
            class="mt-5 space-y-3"
          >
            <article
              v-for="job in unschedulableJobs"
              :key="job.job_id"
              class="rounded-2xl border border-base-300 bg-base-200/30 p-4"
            >
              <div class="flex items-start justify-between gap-3">
                <div>
                  <p class="font-medium">
                    {{ job.kind }}
                  </p>
                  <p class="mt-1 font-mono text-xs text-base-content/55">
                    {{ job.job_id }}
                  </p>
                </div>
                <span
                  class="badge border-0"
                  :class="solidBadgeClassFromTone(job.priority_view.tone)"
                >
                  {{ job.priority_view.label }} | p{{ job.priority }}
                </span>
              </div>
              <div class="mt-3 flex flex-wrap gap-2">
                <span
                  v-for="selector in job.selectors"
                  :key="selector"
                  class="badge badge-outline"
                >
                  {{ selector }}
                </span>
              </div>
              <div class="mt-3 flex flex-wrap gap-2">
                <span
                  v-for="reason in job.blocker_summary"
                  :key="reason"
                  class="badge badge-outline"
                >
                  {{ reason }}
                </span>
              </div>
              <div class="mt-3 flex flex-wrap gap-2">
                <button
                  v-for="action in job.actions"
                  :key="`${job.job_id}:${action.key}`"
                  class="btn btn-xs btn-outline"
                  type="button"
                  :disabled="!action.enabled"
                  :title="action.reason ?? ''"
                  @click="openActionDialog('job', job.job_id, action)"
                >
                  {{ action.label }}
                </button>
                <RouterLink
                  class="btn btn-xs btn-outline"
                  :to="toRoute(job.route)"
                >
                  Open Job
                </RouterLink>
              </div>
            </article>
          </div>
        </section>

        <section class="rounded-[2rem] border border-base-300 bg-base-100 p-5 shadow-sm">
          <div class="flex items-center justify-between gap-3">
            <div>
              <p class="text-xs uppercase tracking-[0.22em] text-base-content/55">
                Lease Health
              </p>
              <h2 class="mt-2 text-2xl font-semibold">
                Stale And Backlog
              </h2>
            </div>
            <RouterLink
              class="btn btn-sm btn-outline"
              to="/jobs"
            >
              Open Jobs
            </RouterLink>
          </div>
          <div class="mt-5 space-y-4">
            <div>
              <p class="text-xs uppercase tracking-[0.2em] text-base-content/50">
                Backlog By Zone
              </p>
              <div class="mt-2 flex flex-wrap gap-2">
                <RouterLink
                  v-for="segment in backlogByZone"
                  :key="segment.key"
                  :to="toRoute(segment.route)"
                  class="badge badge-outline"
                >
                  {{ segment.label }} x {{ segment.count }}
                </RouterLink>
                <span
                  v-if="backlogByZone.length === 0"
                  class="text-sm text-base-content/60"
                >
                  No pending backlog
                </span>
              </div>
            </div>
            <div>
              <p class="text-xs uppercase tracking-[0.2em] text-base-content/50">
                Backlog By Capability
              </p>
              <div class="mt-2 flex flex-wrap gap-2">
                <RouterLink
                  v-for="segment in backlogByCapability"
                  :key="segment.key"
                  :to="toRoute(segment.route)"
                  class="badge badge-outline"
                >
                  {{ segment.label }} x {{ segment.count }}
                </RouterLink>
                <span
                  v-if="backlogByCapability.length === 0"
                  class="text-sm text-base-content/60"
                >
                  No pending backlog
                </span>
              </div>
            </div>
            <div>
              <p class="text-xs uppercase tracking-[0.2em] text-base-content/50">
                Backlog By Executor
              </p>
              <div class="mt-2 flex flex-wrap gap-2">
                <RouterLink
                  v-for="segment in backlogByExecutor"
                  :key="segment.key"
                  :to="toRoute(segment.route)"
                  class="badge badge-outline"
                >
                  {{ segment.label }} x {{ segment.count }}
                </RouterLink>
                <span
                  v-if="backlogByExecutor.length === 0"
                  class="text-sm text-base-content/60"
                >
                  No executor-specific backlog
                </span>
              </div>
            </div>
            <div>
              <p class="text-xs uppercase tracking-[0.2em] text-base-content/50">
                Stale Leases
              </p>
              <div
                v-if="staleJobs.length === 0"
                class="mt-2 rounded-2xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-900"
              >
                No stale leases detected.
              </div>
              <div
                v-else
                class="mt-2 space-y-2"
              >
                <article
                  v-for="job in staleJobs"
                  :key="job.job_id"
                  class="rounded-2xl border border-base-300 bg-base-200/30 p-3"
                >
                  <div class="flex items-start justify-between gap-3">
                    <div>
                      <p class="font-medium">
                        {{ job.kind }}
                      </p>
                      <p class="mt-1 font-mono text-xs text-base-content/55">
                        {{ job.job_id }}
                      </p>
                    </div>
                    <span
                      class="badge"
                      :class="badgeClassFromTone(job.lease_state_view.tone)"
                    >
                      {{ job.lease_state_view.label }}
                    </span>
                  </div>
                  <p class="mt-2 text-sm text-base-content/70">
                    {{ job.attention_reason || "Lease requires review." }}
                  </p>
                  <p class="mt-1 text-xs text-base-content/55">
                    node {{ job.node_id || "-" }} | attempt {{ job.attempt }} | expired {{ formatTs(job.leased_until) }}
                  </p>
                  <div class="mt-3 flex flex-wrap gap-2">
                    <button
                      v-for="action in job.actions"
                      :key="`${job.job_id}:${action.key}`"
                      class="btn btn-xs btn-outline"
                      type="button"
                      :disabled="!action.enabled"
                      :title="action.reason ?? ''"
                      @click="openActionDialog('job', job.job_id, action)"
                    >
                      {{ action.label }}
                    </button>
                    <RouterLink
                      class="btn btn-xs btn-outline"
                      :to="toRoute(job.route)"
                    >
                      Open Job
                    </RouterLink>
                  </div>
                </article>
              </div>
            </div>
          </div>
        </section>
      </div>
    </div>

    <ControlActionDialog
      :action="pendingAction"
      :submitting="actionSubmitting"
      @close="closeActionDialog"
      @invalid="handleInvalidAction"
      @submit="submitAction"
    />
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { RouterLink } from "vue-router";
import ControlActionDialog from "@/components/control/ControlActionDialog.vue";
import { useCapabilitiesStore } from "@/stores/capabilities";
import { useConnectorsStore } from "@/stores/connectors";
import { useConsoleStore } from "@/stores/console";
import { useJobsStore } from "@/stores/jobs";
import { useNodesStore } from "@/stores/nodes";
import type { ConsoleRouteTarget } from "@/types/console";
import type { ControlAction } from "@/types/controlPlane";
import { findCapabilityByEndpoint } from "@/utils/controlPlane";
import { badgeClassFromTone, solidBadgeClassFromTone, surfaceClassFromTone } from "@/utils/statusView";

interface DashboardCard {
  routePath: string;
  routeName: string;
  title: string;
  description: string;
  enabled: boolean;
  status: string;
}

type ActionTargetKind = "node" | "job" | "connector";

const capsStore = useCapabilitiesStore();
const consoleStore = useConsoleStore();
const nodesStore = useNodesStore();
const jobsStore = useJobsStore();
const connectorsStore = useConnectorsStore();

const pendingAction = ref<ControlAction | null>(null);
const pendingTargetKind = ref<ActionTargetKind | null>(null);
const pendingTargetId = ref<string | null>(null);

const cards = computed<DashboardCard[]>(() => {
  if (!consoleStore.hasMenu) return [];
  return consoleStore.menu
    .filter((item) => item.route_name !== "dashboard")
    .map((item) => {
      const cap = findCapabilityByEndpoint(capsStore.caps, item.endpoint);
      return {
        routePath: item.route_path,
        routeName: item.route_name,
        title: item.label,
        description: item.reason ?? "backend-driven console entry",
        enabled: cap == null ? item.enabled : item.enabled && (cap.enabled ?? true) && cap.status.toLowerCase() !== "offline",
        status: cap?.status ?? (item.enabled ? "online" : "offline"),
      };
    });
});

const overview = computed(() => consoleStore.overview);
const diagnostics = computed(() => consoleStore.diagnostics);

const generatedAtText = computed(() => {
  const ts = overview.value?.generated_at;
  return ts ? new Date(ts).toLocaleString() : "-";
});

const packCards = computed(() => consoleStore.profile?.packs ?? []);
const selectedPackCount = computed(
  () => packCards.value.filter((pack) => pack.selected || pack.inherited).length
);
const summaryCards = computed(() => overview.value?.summary_cards ?? []);
const attentionItems = computed(() => overview.value?.attention ?? []);
const nodeHealth = computed(() => diagnostics.value?.node_health ?? []);
const connectorHealth = computed(() => diagnostics.value?.connector_health ?? []);
const unschedulableJobs = computed(() => diagnostics.value?.unschedulable_jobs ?? []);
const staleJobs = computed(() => diagnostics.value?.stale_jobs ?? []);
const backlogByZone = computed(() => diagnostics.value?.backlog_by_zone ?? []);
const backlogByCapability = computed(() => diagnostics.value?.backlog_by_capability ?? []);
const backlogByExecutor = computed(() => diagnostics.value?.backlog_by_executor ?? []);

const actionSubmitting = computed(() => {
  const targetId = pendingTargetId.value;
  const targetKind = pendingTargetKind.value;
  if (!targetId || !targetKind) return false;
  if (targetKind === "node") return nodesStore.actionLoading[targetId];
  if (targetKind === "job") return jobsStore.actionLoading[targetId] || jobsStore.explainLoading[targetId];
  return connectorsStore.actionLoading[targetId] || connectorsStore.submitting;
});

function formatTs(ts: string | null): string {
  if (!ts) return "-";
  return new Date(ts).toLocaleString();
}

function toRoute(route: ConsoleRouteTarget): { path: string; query: Record<string, string> } {
  return {
    path: route.route_path,
    query: route.query,
  };
}

function refreshNow(): void {
  void consoleStore.refresh();
}

function openActionDialog(kind: ActionTargetKind, id: string, action: ControlAction): void {
  if (!action.enabled) return;
  pendingTargetKind.value = kind;
  pendingTargetId.value = id;
  pendingAction.value = action;
}

function closeActionDialog(): void {
  pendingAction.value = null;
  pendingTargetKind.value = null;
  pendingTargetId.value = null;
}

function handleInvalidAction(message: string): void {
  consoleStore.error = message;
}

async function submitAction(payload: Record<string, unknown>): Promise<void> {
  if (!pendingAction.value || !pendingTargetKind.value || !pendingTargetId.value) {
    return;
  }
  if (pendingTargetKind.value === "node") {
    await nodesStore.runNodeAction(pendingTargetId.value, pendingAction.value, payload);
  } else if (pendingTargetKind.value === "job") {
    await jobsStore.runJobAction(pendingTargetId.value, pendingAction.value, payload);
  } else {
    await connectorsStore.runConnectorAction(pendingTargetId.value, pendingAction.value, payload);
  }
  await consoleStore.refresh();
  closeActionDialog();
}

onMounted(() => {
  if (Object.keys(capsStore.caps).length === 0) {
    void capsStore.fetchCapabilities();
  }
  void consoleStore.refresh();
});
</script>
