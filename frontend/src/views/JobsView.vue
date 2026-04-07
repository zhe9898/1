<template>
  <section class="mx-auto max-w-6xl space-y-4 px-6 py-8">
    <header class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <p class="text-sm uppercase tracking-[0.25em] text-base-content/60">
          Gateway Control Plane
        </p>
        <h1 class="mt-2 text-3xl font-semibold">
          {{ jobSchema?.title ?? "Jobs" }}
        </h1>
        <p
          v-if="jobSchema?.description"
          class="mt-2 max-w-3xl text-sm text-base-content/70"
        >
          {{ jobSchema.description }}
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
      v-if="jobCapability"
      class="alert alert-info"
    >
      <span>{{ jobCapability.reason ?? "Dispatch queue is active." }}</span>
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

    <div class="grid gap-4 xl:grid-cols-[20rem_minmax(0,1fr)]">
      <BackendForm
        v-if="jobSchema"
        title="Queue Job"
        :sections="jobSchema.sections"
        :submit-label="jobSchema.submit_action?.label ?? 'Queue Job'"
        :submitting="store.submitting"
        @submit="submitCreate"
        @invalid="handleFormError"
      />
      <div
        v-else
        class="card border border-base-300 bg-base-100 p-4 shadow-sm"
      >
        <h2 class="text-lg font-medium">
          Queue Job
        </h2>
        <p class="mt-3 text-sm text-base-content/65">
          Loading backend job schema...
        </p>
      </div>

      <div class="stats w-full bg-base-100 shadow">
        <div class="stat">
          <div class="stat-title">
            Pending
          </div>
          <div class="stat-value text-2xl">
            {{ statusSummary.pending }}
          </div>
        </div>
        <div class="stat">
          <div class="stat-title">
            Running
          </div>
          <div class="stat-value text-2xl text-warning">
            {{ statusSummary.running }}
          </div>
        </div>
        <div class="stat">
          <div class="stat-title">
            Completed
          </div>
          <div class="stat-value text-2xl text-success">
            {{ statusSummary.completed }}
          </div>
        </div>
        <div class="stat">
          <div class="stat-title">
            Failed
          </div>
          <div class="stat-value text-2xl text-error">
            {{ statusSummary.failed }}
          </div>
        </div>
      </div>
    </div>

    <div
      v-if="!store.loading && filteredJobs.length === 0"
      class="rounded-3xl border border-base-300 bg-base-100 p-6 text-base-content/60"
    >
      {{ jobSchema?.empty_state ?? "No jobs match the current view." }}
    </div>

    <div class="grid gap-4">
      <article
        v-for="job in filteredJobs"
        :key="job.job_id"
        class="card border border-base-300 bg-base-100 shadow-sm"
      >
        <div class="card-body gap-4">
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 class="card-title text-xl">
                {{ job.kind }}
              </h2>
              <p class="font-mono text-xs text-base-content/50">
                {{ job.job_id }}
              </p>
            </div>
            <span
              class="badge"
              :class="badgeClassFromTone(job.status_view.tone)"
            >
              {{ job.status_view.label }}
            </span>
          </div>

          <div class="flex flex-wrap gap-2">
            <button
              v-for="action in job.actions"
              :key="`${job.job_id}:${action.key}`"
              class="btn btn-sm btn-outline"
              type="button"
              :title="action.reason ?? ''"
              :disabled="store.actionLoading[job.job_id] || store.explainLoading[job.job_id] || !action.enabled"
              @click="executeJobAction(job.job_id, action)"
            >
              {{ action.key === "explain" && expandedExplain[job.job_id] ? "Hide Explain" : action.label }}
            </button>
          </div>

          <div class="grid gap-3 lg:grid-cols-3">
            <div class="rounded-2xl border border-base-300 bg-base-200/40 p-4">
              <p class="text-xs uppercase tracking-[0.2em] text-base-content/50">
                Assignment
              </p>
              <p class="mt-2 text-sm">
                Node: {{ job.node_id || "-" }}
              </p>
              <p class="mt-1 text-sm">
                Connector: {{ job.connector_id || "-" }}
              </p>
              <p class="mt-1 text-sm">
                Attempt: {{ job.attempt }}
              </p>
              <p class="mt-1 text-sm">
                Lease: {{ job.lease_seconds }}s
              </p>
              <p class="mt-1 text-sm">
                Idempotency: {{ job.idempotency_key || "-" }}
              </p>
              <p class="mt-1 text-sm">
                Priority: {{ job.priority }}
              </p>
              <p class="mt-1 text-sm">
                Source: {{ job.source }}
              </p>
              <p class="mt-1 text-sm">
                Lease State: {{ job.lease_state_view.label }}
              </p>
              <p class="mt-1 text-sm">
                Attention: {{ job.attention_reason || "-" }}
              </p>
            </div>

            <div class="rounded-2xl border border-base-300 bg-base-200/40 p-4">
              <p class="text-xs uppercase tracking-[0.2em] text-base-content/50">
                Timeline
              </p>
              <p class="mt-2 text-sm">
                Created: {{ formatTs(job.created_at) }}
              </p>
              <p class="mt-1 text-sm">
                Started: {{ formatTs(job.started_at) }}
              </p>
              <p class="mt-1 text-sm">
                Completed: {{ formatTs(job.completed_at) }}
              </p>
            </div>

            <div class="rounded-2xl border border-base-300 bg-base-200/40 p-4">
              <p class="text-xs uppercase tracking-[0.2em] text-base-content/50">
                Constraints
              </p>
              <p class="mt-2 text-sm">
                OS/Arch: {{ job.target_os || "*" }}/{{ job.target_arch || "*" }}
              </p>
              <p class="mt-1 text-sm">
                Executor: {{ job.target_executor || "*" }}
              </p>
              <p class="mt-1 text-sm">
                Zone: {{ job.target_zone || "*" }}
              </p>
              <p class="mt-1 text-sm">
                CPU / Memory: {{ job.required_cpu_cores ?? "*" }} / {{ job.required_memory_mb ?? "*" }}
              </p>
              <p class="mt-1 text-sm">
                GPU / Storage: {{ job.required_gpu_vram_mb ?? "*" }} / {{ job.required_storage_mb ?? "*" }}
              </p>
              <p class="mt-1 text-sm">
                Timeout: {{ job.timeout_seconds }}s
              </p>
              <p class="mt-1 text-sm">
                Retries: {{ job.retry_count }}/{{ job.max_retries }}
              </p>
              <p class="mt-1 text-sm">
                Estimate: {{ estimatedDurationText(job.estimated_duration_s) }}
              </p>
              <div class="mt-2 flex flex-wrap gap-2">
                <span
                  v-for="capability in job.required_capabilities"
                  :key="capability"
                  class="badge badge-outline"
                >
                  {{ capability }}
                </span>
                <span
                  v-if="job.required_capabilities.length === 0"
                  class="text-sm text-base-content/60"
                >
                  No extra capability filter
                </span>
              </div>
            </div>

            <div class="rounded-2xl border border-base-300 bg-base-200/40 p-4 lg:col-span-3">
              <p class="text-xs uppercase tracking-[0.2em] text-base-content/50">
                Result / Error
              </p>
              <pre
                v-if="job.result"
                class="mt-2 whitespace-pre-wrap break-all rounded-xl bg-base-100 p-3 text-xs"
              >{{ formatObject(job.result) }}</pre>
              <div
                v-else-if="job.safe_error_code"
                class="mt-2 rounded-xl border border-error/30 bg-error/5 p-3 text-sm"
              >
                <p class="font-medium text-error">
                  {{ job.safe_error_code }}
                </p>
                <p
                  v-if="job.safe_error_hint"
                  class="mt-1 text-base-content/80"
                >
                  {{ job.safe_error_hint }}
                </p>
              </div>
              <p
                v-else
                class="mt-2 text-sm text-base-content/60"
              >
                No terminal output yet.
              </p>
            </div>

            <div class="rounded-2xl border border-base-300 bg-base-200/40 p-4 lg:col-span-3">
              <div class="flex items-center justify-between gap-3">
                <div>
                  <p class="text-xs uppercase tracking-[0.2em] text-base-content/50">
                    Attempt History
                  </p>
                  <p class="mt-2 text-sm text-base-content/70">
                    Every lease is recorded here so retry, failure, and node placement stay auditable.
                  </p>
                </div>
                <button
                  class="btn btn-sm btn-outline"
                  type="button"
                  :disabled="store.attemptsLoading[job.job_id]"
                  @click="toggleAttempts(job.job_id)"
                >
                  {{ expandedJobs[job.job_id] ? "Hide Attempts" : "Show Attempts" }}
                </button>
              </div>
              <div
                v-if="expandedJobs[job.job_id]"
                class="mt-4 space-y-3"
              >
                <div
                  v-if="store.attemptsLoading[job.job_id]"
                  class="rounded-xl border border-base-300 bg-base-100 p-3 text-sm text-base-content/70"
                >
                  Loading attempts...
                </div>
                <div
                  v-else-if="jobAttempts(job.job_id).length === 0"
                  class="rounded-xl border border-base-300 bg-base-100 p-3 text-sm text-base-content/70"
                >
                  No attempt history recorded yet.
                </div>
                <div
                  v-for="attempt in jobAttempts(job.job_id)"
                  :key="attempt.attempt_id"
                  class="rounded-xl border border-base-300 bg-base-100 p-4"
                >
                  <div class="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <div class="flex flex-wrap items-center gap-2">
                        <span class="badge badge-outline">Attempt {{ attempt.attempt_no }}</span>
                        <span
                          class="badge"
                          :class="badgeClassFromTone(attempt.status_view.tone)"
                        >
                          {{ attempt.status_view.label }}
                        </span>
                      </div>
                      <p class="mt-2 text-sm text-base-content/70">
                        Node {{ attempt.node_id }} scored {{ attempt.score }} for this lease.
                      </p>
                    </div>
                    <div class="text-right text-sm text-base-content/65">
                      <p>Started: {{ formatTs(attempt.started_at) }}</p>
                      <p>Completed: {{ formatTs(attempt.completed_at) }}</p>
                    </div>
                  </div>
                  <div
                    v-if="attempt.safe_error_code"
                    class="mt-3 rounded-xl border border-error/30 bg-error/5 p-3 text-sm"
                  >
                    <p class="font-medium text-error">
                      {{ attempt.safe_error_code }}
                    </p>
                    <p
                      v-if="attempt.safe_error_hint"
                      class="mt-1 text-base-content/80"
                    >
                      {{ attempt.safe_error_hint }}
                    </p>
                  </div>
                  <pre
                    v-else-if="attempt.result_summary"
                    class="mt-3 whitespace-pre-wrap break-all rounded-xl bg-base-200/60 p-3 text-xs"
                  >{{ formatObject(attempt.result_summary) }}</pre>
                </div>
              </div>
            </div>

            <div class="rounded-2xl border border-base-300 bg-base-200/40 p-4 lg:col-span-3">
              <div class="flex items-center justify-between gap-3">
                <div>
                  <p class="text-xs uppercase tracking-[0.2em] text-base-content/50">
                    Scheduler Explain
                  </p>
                  <p class="mt-2 text-sm text-base-content/70">
                    Shows why a node can or cannot pick this job and what score eligible nodes receive.
                  </p>
                </div>
              </div>
              <div
                v-if="expandedExplain[job.job_id]"
                class="mt-4 space-y-3"
              >
                <div
                  v-if="store.explainLoading[job.job_id]"
                  class="rounded-xl border border-base-300 bg-base-100 p-3 text-sm text-base-content/70"
                >
                  Loading scheduler diagnostics...
                </div>
                <div
                  v-else-if="!jobExplain(job.job_id)"
                  class="rounded-xl border border-base-300 bg-base-100 p-3 text-sm text-base-content/70"
                >
                  No scheduler diagnostics available yet.
                </div>
                <div
                  v-else
                  class="space-y-3"
                >
                  <div class="rounded-xl border border-base-300 bg-base-100 p-3 text-sm">
                    Eligible nodes: {{ jobExplain(job.job_id)?.eligible_nodes ?? 0 }} / {{ jobExplain(job.job_id)?.total_nodes ?? 0 }}
                  </div>
                  <div
                    v-for="decision in jobExplain(job.job_id)?.decisions ?? []"
                    :key="decision.node_id"
                    class="rounded-xl border border-base-300 bg-base-100 p-4"
                  >
                    <div class="flex flex-wrap items-start justify-between gap-3">
                      <div>
                        <div class="flex flex-wrap items-center gap-2">
                          <span class="badge badge-outline">{{ decision.node_id }}</span>
                          <span
                            class="badge"
                            :class="badgeClassFromTone(decision.eligibility_view.tone)"
                          >
                            {{ decision.eligibility_view.label }}
                          </span>
                          <span
                            v-if="decision.score != null"
                            class="badge badge-info"
                          >
                            score {{ decision.score }}
                          </span>
                        </div>
                        <p class="mt-2 text-sm text-base-content/70">
                          {{ decision.executor }} on {{ decision.os }}/{{ decision.arch }}{{ decision.zone ? ` @ ${decision.zone}` : "" }} | leases {{ decision.active_lease_count }}/{{ decision.max_concurrency }} | drain {{ decision.drain_status_view.label }} | reliability {{ decision.reliability_score.toFixed(2) }}
                        </p>
                        <p class="mt-1 text-xs text-base-content/55">
                          cpu {{ decision.cpu_cores }} | memory {{ decision.memory_mb }} MB | gpu {{ decision.gpu_vram_mb }} MB | storage {{ decision.storage_mb }} MB
                        </p>
                      </div>
                      <div class="text-right text-sm text-base-content/65">
                        <p>Status: {{ decision.status_view.label }}</p>
                        <p>Seen: {{ formatTs(decision.last_seen_at) }}</p>
                      </div>
                    </div>
                    <div
                      v-if="decision.reasons.length > 0"
                      class="mt-3 flex flex-wrap gap-2"
                    >
                      <span
                        v-for="reason in decision.reasons"
                        :key="reason"
                        class="badge badge-outline"
                      >
                        {{ reason }}
                      </span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </article>
    </div>

    <ControlActionDialog
      :action="pendingAction"
      :submitting="activeActionSubmitting"
      @close="closeActionDialog"
      @invalid="handleFormError"
      @submit="submitJobAction"
    />
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import BackendForm from "@/components/control/BackendForm.vue";
import ControlActionDialog from "@/components/control/ControlActionDialog.vue";
import { useCapabilitiesStore } from "@/stores/capabilities";
import { useEventsStore } from "@/stores/events";
import { useJobsStore } from "@/stores/jobs";
import { JOBS } from "@/utils/api";
import { findCapabilityByEndpoint } from "@/utils/controlPlane";
import { badgeClassFromTone } from "@/utils/statusView";
import type { ControlAction } from "@/types/controlPlane";
import type { JobControlEvent } from "@/types/sse";

const store = useJobsStore();
const capsStore = useCapabilitiesStore();
const eventsStore = useEventsStore();
const route = useRoute();
const router = useRouter();
const expandedJobs = reactive<Record<string, boolean>>({});
const expandedExplain = reactive<Record<string, boolean>>({});
const pendingAction = ref<ControlAction | null>(null);
const pendingJobId = ref<string | null>(null);

const jobCapability = computed(() => findCapabilityByEndpoint(capsStore.caps, JOBS.list));
const jobSchema = computed(() => store.schema);
const schemaPolicyBadges = computed(() => {
  const policies = jobSchema.value?.policies ?? {};
  const badges: string[] = [];
  if (typeof policies.resource_mode === "string" && policies.resource_mode) {
    badges.push(policies.resource_mode);
  }
  if (typeof policies.ui_mode === "string" && policies.ui_mode) {
    badges.push(policies.ui_mode);
  }
  return badges;
});
const activeActionSubmitting = computed(() => {
  const jobId = pendingJobId.value;
  if (!jobId) return false;
  return store.actionLoading[jobId] || store.explainLoading[jobId];
});
const listQueryParams = computed<Record<string, string>>(() => normalizeRouteQuery(route.query));
const hasActiveFilters = computed(() => Object.keys(listQueryParams.value).length > 0);
const filteredJobs = computed(() => store.items);
const filterLabels = computed(() => {
  const labels: string[] = [];
  if (typeof route.query.job_id === "string" && route.query.job_id) labels.push(`job ${route.query.job_id}`);
  if (typeof route.query.status === "string" && route.query.status) labels.push(`status ${route.query.status}`);
  if (typeof route.query.lease_state === "string" && route.query.lease_state) labels.push(`lease ${route.query.lease_state}`);
  if (typeof route.query.priority_bucket === "string" && route.query.priority_bucket) labels.push(`priority ${route.query.priority_bucket}`);
  if (typeof route.query.target_executor === "string" && route.query.target_executor) {
    labels.push(`executor ${route.query.target_executor}`);
  }
  if (typeof route.query.target_zone === "string" && route.query.target_zone) labels.push(`zone ${route.query.target_zone}`);
  if (typeof route.query.required_capability === "string" && route.query.required_capability) {
    labels.push(`capability ${route.query.required_capability}`);
  }
  return labels;
});

const statusSummary = computed(() => {
  return filteredJobs.value.reduce(
    (summary, job) => {
      const status = job.status_view.key.toLowerCase();
      if (status in summary) {
        summary[status as keyof typeof summary] += 1;
      }
      return summary;
    },
    { pending: 0, running: 0, completed: 0, failed: 0 }
  );
});

function formatTs(ts: string | null): string {
  if (!ts) return "-";
  return new Date(ts).toLocaleString();
}

function formatObject(value: Record<string, unknown>): string {
  return JSON.stringify(value, null, 2);
}

function estimatedDurationText(value: number | null): string {
  return value == null ? "-" : `${String(value)}s`;
}

function jobAttempts(jobId: string) {
  return store.attemptsByJob[jobId] ?? [];
}

function jobExplain(jobId: string) {
  return store.explanationsByJob[jobId] ?? null;
}

function refreshNow(): void {
  void store.fetchJobs(listQueryParams.value);
}

function clearFilters(): void {
  void router.push({ path: route.path, query: {} });
}

async function toggleAttempts(jobId: string): Promise<void> {
  expandedJobs[jobId] = !expandedJobs[jobId];
  if (expandedJobs[jobId]) {
    await store.fetchJobAttempts(jobId);
  }
}

async function toggleExplain(jobId: string): Promise<void> {
  expandedExplain[jobId] = !expandedExplain[jobId];
  if (expandedExplain[jobId]) {
    await store.fetchJobExplain(jobId);
  }
}

async function executeJobAction(jobId: string, action: ControlAction): Promise<void> {
  if (!action.enabled) return;
  if (action.key === "explain") {
    await toggleExplain(jobId);
    return;
  }
  pendingJobId.value = jobId;
  pendingAction.value = action;
}

function closeActionDialog(): void {
  pendingAction.value = null;
  pendingJobId.value = null;
}

async function submitJobAction(payload: Record<string, unknown>): Promise<void> {
  if (!pendingAction.value || !pendingJobId.value) {
    return;
  }
  await store.runJobAction(pendingJobId.value, pendingAction.value, payload);
  if (hasActiveFilters.value) {
    await store.fetchJobs(listQueryParams.value);
  }
  closeActionDialog();
}

function handleFormError(message: string): void {
  store.error = message;
}

async function submitCreate(payload: Record<string, unknown>): Promise<void> {
  await store.createJob({
    kind: typeof payload.kind === "string" ? payload.kind : "",
    connector_id: typeof payload.connector_id === "string" && payload.connector_id ? payload.connector_id : undefined,
    idempotency_key:
      typeof payload.idempotency_key === "string" && payload.idempotency_key ? payload.idempotency_key : undefined,
    priority: typeof payload.priority === "number" ? payload.priority : 50,
    lease_seconds: typeof payload.lease_seconds === "number" ? payload.lease_seconds : undefined,
    timeout_seconds: typeof payload.timeout_seconds === "number" ? payload.timeout_seconds : 300,
    max_retries: typeof payload.max_retries === "number" ? payload.max_retries : 0,
    estimated_duration_s: typeof payload.estimated_duration_s === "number" ? payload.estimated_duration_s : undefined,
    target_os: typeof payload.target_os === "string" && payload.target_os ? payload.target_os : undefined,
    target_arch: typeof payload.target_arch === "string" && payload.target_arch ? payload.target_arch : undefined,
    target_executor: typeof payload.target_executor === "string" && payload.target_executor ? payload.target_executor : undefined,
    target_zone: typeof payload.target_zone === "string" && payload.target_zone ? payload.target_zone : undefined,
    required_cpu_cores: typeof payload.required_cpu_cores === "number" ? payload.required_cpu_cores : undefined,
    required_memory_mb: typeof payload.required_memory_mb === "number" ? payload.required_memory_mb : undefined,
    required_gpu_vram_mb: typeof payload.required_gpu_vram_mb === "number" ? payload.required_gpu_vram_mb : undefined,
    required_storage_mb: typeof payload.required_storage_mb === "number" ? payload.required_storage_mb : undefined,
    required_capabilities: Array.isArray(payload.required_capabilities)
      ? payload.required_capabilities.filter((item): item is string => typeof item === "string" && item.length > 0)
      : undefined,
    source: typeof payload.source === "string" && payload.source ? payload.source : undefined,
    payload:
      typeof payload.payload === "object" && payload.payload != null
        ? (payload.payload as Record<string, unknown>)
        : {},
  }, listQueryParams.value);
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
    void store.fetchJobs(listQueryParams.value);
  },
  { immediate: true }
);

watch(
  () => eventsStore.revision,
  () => {
    if (eventsStore.items.length === 0) return;
    const newest = eventsStore.items[0];
    if (newest.ev.type === "job:events") {
      if (hasActiveFilters.value) {
        void store.fetchJobs(listQueryParams.value);
        return;
      }
      store.applyJobEvent(newest.ev.data as JobControlEvent);
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
