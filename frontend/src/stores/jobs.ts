import { defineStore } from "pinia";
import { computed, ref } from "vue";
import { JOBS } from "@/utils/api";
import { http } from "@/utils/http";
import type { ResourceSchema } from "@/types/backendUi";
import type { ControlAction, StatusView } from "@/types/controlPlane";
import type { JobControlEvent } from "@/types/sse";
import { normalizeStatusView } from "@/utils/statusView";

export interface JobItem {
  job_id: string;
  kind: string;
  status: string;
  status_view: StatusView;
  node_id: string | null;
  connector_id: string | null;
  idempotency_key: string | null;
  priority: number;
  target_os: string | null;
  target_arch: string | null;
  target_executor: string | null;
  required_capabilities: string[];
  target_zone: string | null;
  required_cpu_cores: number | null;
  required_memory_mb: number | null;
  required_gpu_vram_mb: number | null;
  required_storage_mb: number | null;
  timeout_seconds: number;
  max_retries: number;
  retry_count: number;
  estimated_duration_s: number | null;
  source: string;
  attempt: number;
  payload: Record<string, unknown>;
  result: Record<string, unknown> | null;
  error_message: string | null;
  lease_seconds: number;
  leased_until: string | null;
  lease_state: string;
  lease_state_view: StatusView;
  attention_reason: string | null;
  actions: ControlAction[];
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface JobAttemptItem {
  attempt_id: string;
  job_id: string;
  node_id: string;
  lease_token: string;
  attempt_no: number;
  status: string;
  status_view: StatusView;
  score: number;
  error_message: string | null;
  result_summary: Record<string, unknown> | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface JobExplainDecision {
  node_id: string;
  eligible: boolean;
  eligibility_view: StatusView;
  score: number | null;
  reasons: string[];
  active_lease_count: number;
  max_concurrency: number;
  executor: string;
  os: string;
  arch: string;
  zone: string | null;
  cpu_cores: number;
  memory_mb: number;
  gpu_vram_mb: number;
  storage_mb: number;
  drain_status: string;
  drain_status_view: StatusView;
  reliability_score: number;
  status: string;
  status_view: StatusView;
  last_seen_at: string;
}

export interface JobExplainItem {
  job: JobItem;
  total_nodes: number;
  eligible_nodes: number;
  selected_node_id: string | null;
  decisions: JobExplainDecision[];
}

export interface CreateJobPayload {
  kind: string;
  payload?: Record<string, unknown>;
  connector_id?: string;
  lease_seconds?: number;
  idempotency_key?: string;
  priority?: number;
  target_os?: string;
  target_arch?: string;
  target_executor?: string;
  required_capabilities?: string[];
  target_zone?: string;
  required_cpu_cores?: number;
  required_memory_mb?: number;
  required_gpu_vram_mb?: number;
  required_storage_mb?: number;
  timeout_seconds?: number;
  max_retries?: number;
  estimated_duration_s?: number;
  source?: string;
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
      .filter((entry): entry is [string, string] => entry != null)
  );
}

function normalizeJob(partial: Partial<JobItem> & { job_id: string }): JobItem {
  return {
    job_id: partial.job_id,
    kind: partial.kind ?? "unknown",
    status: partial.status ?? "unknown",
    status_view:
      partial.status_view ?? normalizeStatusView(null, partial.status ?? "unknown", partial.status ?? "Unknown"),
    node_id: partial.node_id ?? null,
    connector_id: partial.connector_id ?? null,
    idempotency_key: partial.idempotency_key ?? null,
    priority: partial.priority ?? 50,
    target_os: partial.target_os ?? null,
    target_arch: partial.target_arch ?? null,
    target_executor: partial.target_executor ?? null,
    required_capabilities: partial.required_capabilities ?? [],
    target_zone: partial.target_zone ?? null,
    required_cpu_cores: partial.required_cpu_cores ?? null,
    required_memory_mb: partial.required_memory_mb ?? null,
    required_gpu_vram_mb: partial.required_gpu_vram_mb ?? null,
    required_storage_mb: partial.required_storage_mb ?? null,
    timeout_seconds: partial.timeout_seconds ?? 300,
    max_retries: partial.max_retries ?? 0,
    retry_count: partial.retry_count ?? 0,
    estimated_duration_s: partial.estimated_duration_s ?? null,
    source: partial.source ?? "console",
    attempt: partial.attempt ?? 0,
    payload: partial.payload ?? {},
    result: partial.result ?? null,
    error_message: partial.error_message ?? null,
    lease_seconds: partial.lease_seconds ?? 30,
    leased_until: partial.leased_until ?? null,
    lease_state: partial.lease_state ?? "none",
    lease_state_view:
      partial.lease_state_view ?? normalizeStatusView(null, partial.lease_state ?? "none", partial.lease_state ?? "None"),
    attention_reason: partial.attention_reason ?? null,
    actions: partial.actions ?? [],
    created_at: partial.created_at ?? new Date().toISOString(),
    started_at: partial.started_at ?? null,
    completed_at: partial.completed_at ?? null,
  };
}

function normalizeJobAttempt(partial: Partial<JobAttemptItem> & { attempt_id: string; job_id: string; node_id: string }): JobAttemptItem {
  return {
    attempt_id: partial.attempt_id,
    job_id: partial.job_id,
    node_id: partial.node_id,
    lease_token: partial.lease_token ?? "",
    attempt_no: partial.attempt_no ?? 0,
    status: partial.status ?? "unknown",
    status_view:
      partial.status_view ?? normalizeStatusView(null, partial.status ?? "unknown", partial.status ?? "Unknown"),
    score: partial.score ?? 0,
    error_message: partial.error_message ?? null,
    result_summary: partial.result_summary ?? null,
    created_at: partial.created_at ?? new Date().toISOString(),
    started_at: partial.started_at ?? null,
    completed_at: partial.completed_at ?? null,
  };
}

function normalizeExplainDecision(partial: Partial<JobExplainDecision> & { node_id: string }): JobExplainDecision {
  return {
    node_id: partial.node_id,
    eligible: partial.eligible ?? false,
    eligibility_view:
      partial.eligibility_view ??
      normalizeStatusView(null, partial.eligible ? "eligible" : "blocked", partial.eligible ? "Eligible" : "Blocked"),
    score: partial.score ?? null,
    reasons: partial.reasons ?? [],
    active_lease_count: partial.active_lease_count ?? 0,
    max_concurrency: partial.max_concurrency ?? 1,
    executor: partial.executor ?? "unknown",
    os: partial.os ?? "unknown",
    arch: partial.arch ?? "unknown",
    zone: partial.zone ?? null,
    cpu_cores: partial.cpu_cores ?? 0,
    memory_mb: partial.memory_mb ?? 0,
    gpu_vram_mb: partial.gpu_vram_mb ?? 0,
    storage_mb: partial.storage_mb ?? 0,
    drain_status: partial.drain_status ?? "active",
    drain_status_view:
      partial.drain_status_view ??
      normalizeStatusView(null, partial.drain_status ?? "active", partial.drain_status ?? "Active", "info"),
    reliability_score: partial.reliability_score ?? 0,
    status: partial.status ?? "unknown",
    status_view:
      partial.status_view ?? normalizeStatusView(null, partial.status ?? "unknown", partial.status ?? "Unknown"),
    last_seen_at: partial.last_seen_at ?? new Date().toISOString(),
  };
}

function normalizeExplainItem(item: JobExplainItem): JobExplainItem {
  return {
    job: normalizeJob(item.job),
    total_nodes: item.total_nodes,
    eligible_nodes: item.eligible_nodes,
    selected_node_id: item.selected_node_id,
    decisions: item.decisions.map((decision) => normalizeExplainDecision(decision)),
  };
}

export const useJobsStore = defineStore("jobs", () => {
  const items = ref<JobItem[]>([]);
  const schema = ref<ResourceSchema | null>(null);
  const attemptsByJob = ref<Record<string, JobAttemptItem[]>>({});
  const explanationsByJob = ref<Record<string, JobExplainItem>>({});
  const attemptsLoading = ref<Record<string, boolean>>({});
  const explainLoading = ref<Record<string, boolean>>({});
  const actionLoading = ref<Record<string, boolean>>({});
  const loading = ref(false);
  const submitting = ref(false);
  const error = ref<string | null>(null);
  const lastUpdatedAt = ref(0);

  const pendingCount = computed(() =>
    items.value.filter((item) => item.status_view.key === "pending" || item.status_view.key === "running").length
  );

  function upsertJob(partial: Partial<JobItem> & { job_id: string }): void {
    const index = items.value.findIndex((item) => item.job_id === partial.job_id);
    if (index >= 0) {
      items.value[index] = normalizeJob({ ...items.value[index], ...partial });
    } else {
      items.value.unshift(normalizeJob(partial));
    }
    lastUpdatedAt.value = Date.now();
  }

  async function fetchJobs(query: Record<string, unknown> = {}): Promise<void> {
    loading.value = true;
    error.value = null;
    try {
      const { data } = await http.get<JobItem[]>(JOBS.list, { params: toListParams(query) });
      items.value = data.map((item) => normalizeJob(item));
      lastUpdatedAt.value = Date.now();
    } catch (err: unknown) {
      error.value = err instanceof Error ? err.message : "Failed to load jobs";
    } finally {
      loading.value = false;
    }
  }

  async function fetchSchema(): Promise<ResourceSchema | null> {
    try {
      const { data } = await http.get<ResourceSchema>(JOBS.schema);
      schema.value = data;
      return data;
    } catch (err: unknown) {
      error.value = err instanceof Error ? err.message : "Failed to load job schema";
      return null;
    }
  }

  async function fetchJob(id: string): Promise<JobItem | null> {
    try {
      const { data } = await http.get<JobItem>(JOBS.detail(id));
      upsertJob(data);
      return data;
    } catch {
      return null;
    }
  }

  async function fetchJobAttempts(jobId: string, force = false): Promise<JobAttemptItem[]> {
    if (!force && Object.prototype.hasOwnProperty.call(attemptsByJob.value, jobId)) {
      return attemptsByJob.value[jobId];
    }
    attemptsLoading.value = { ...attemptsLoading.value, [jobId]: true };
    try {
      const { data } = await http.get<JobAttemptItem[]>(JOBS.attempts(jobId));
      const normalized = data.map((attempt) => normalizeJobAttempt(attempt));
      attemptsByJob.value = { ...attemptsByJob.value, [jobId]: normalized };
      return normalized;
    } catch (err: unknown) {
      error.value = err instanceof Error ? err.message : "Failed to load job attempts";
      return [];
    } finally {
      attemptsLoading.value = { ...attemptsLoading.value, [jobId]: false };
    }
  }

  async function fetchJobExplain(jobId: string, force = false): Promise<JobExplainItem | null> {
    if (!force && Object.prototype.hasOwnProperty.call(explanationsByJob.value, jobId)) {
      return explanationsByJob.value[jobId];
    }
    explainLoading.value = { ...explainLoading.value, [jobId]: true };
    try {
      const { data } = await http.get<JobExplainItem>(JOBS.explain(jobId));
      const normalized = normalizeExplainItem(data);
      explanationsByJob.value = { ...explanationsByJob.value, [jobId]: normalized };
      upsertJob(normalized.job);
      return normalized;
    } catch (err: unknown) {
      error.value = err instanceof Error ? err.message : "Failed to explain job placement";
      return null;
    } finally {
      explainLoading.value = { ...explainLoading.value, [jobId]: false };
    }
  }

  async function createJob(payload: CreateJobPayload, query: Record<string, unknown> = {}): Promise<JobItem | null> {
    submitting.value = true;
    error.value = null;
    try {
      const { data } = await http.post<JobItem>(JOBS.create, payload);
      await fetchJobs(query);
      return data;
    } catch (err: unknown) {
      error.value = err instanceof Error ? err.message : "Failed to create job";
      return null;
    } finally {
      submitting.value = false;
    }
  }

  async function runJobAction(
    jobId: string,
    action: ControlAction,
    payload: Record<string, unknown> = {}
  ): Promise<JobItem | JobExplainItem | null> {
    actionLoading.value = { ...actionLoading.value, [jobId]: true };
    error.value = null;
    try {
      if (!action.enabled) {
        throw new Error(action.reason ?? "This action is currently unavailable");
      }
      if (action.key === "explain" || action.method.toUpperCase() === "GET") {
        return await fetchJobExplain(jobId, true);
      }
      const { data } = await http.request<JobItem>({
        url: action.endpoint,
        method: action.method,
        data: action.method.toUpperCase() === "POST" ? payload : undefined,
      });
      upsertJob(data);
      explanationsByJob.value = Object.fromEntries(Object.entries(explanationsByJob.value).filter(([id]) => id !== jobId));
      return data;
    } catch (err: unknown) {
      error.value = err instanceof Error ? err.message : "Failed to update job";
      return null;
    } finally {
      actionLoading.value = { ...actionLoading.value, [jobId]: false };
    }
  }

  async function cancelJob(jobId: string, reason?: string): Promise<JobItem | null> {
    const response = await runJobAction(
      jobId,
      {
        key: "cancel",
        label: "Cancel",
        endpoint: JOBS.cancel(jobId),
        method: "POST",
        enabled: true,
        requires_admin: true,
        reason: null,
        confirmation: null,
        fields: [],
      },
      { reason }
    );
    return response && "job_id" in response ? response : null;
  }

  async function retryJobNow(jobId: string, reason?: string): Promise<JobItem | null> {
    const response = await runJobAction(
      jobId,
      {
        key: "retry",
        label: "Retry Now",
        endpoint: JOBS.retry(jobId),
        method: "POST",
        enabled: true,
        requires_admin: true,
        reason: null,
        confirmation: null,
        fields: [],
      },
      { reason }
    );
    return response && "job_id" in response ? response : null;
  }

  function applyJobEvent(event: JobControlEvent): void {
    const one = event.job;
    if (one && typeof one === "object") {
      const id = (one as { job_id?: unknown }).job_id;
      if (typeof id === "string" && id) {
        upsertJob(one as Partial<JobItem> & { job_id: string });
      }
    }

    const many = event.jobs;
    if (Array.isArray(many)) {
      for (const job of many) {
        const id = (job as { job_id?: unknown }).job_id;
        if (typeof id !== "string" || !id) continue;
        upsertJob(job as Partial<JobItem> & { job_id: string });
      }
    }
  }

  return {
    items,
    schema,
    attemptsByJob,
    explanationsByJob,
    attemptsLoading,
    explainLoading,
    actionLoading,
    loading,
    submitting,
    error,
    lastUpdatedAt,
    pendingCount,
    applyJobEvent,
    fetchJobs,
    fetchSchema,
    fetchJob,
    fetchJobAttempts,
    fetchJobExplain,
    createJob,
    cancelJob,
    retryJobNow,
    runJobAction,
  };
});
