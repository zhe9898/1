// @vitest-environment jsdom
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import MockAdapter from "axios-mock-adapter";
import { createPinia, setActivePinia } from "pinia";

import { http } from "../src/utils/http";
import { useJobsStore } from "../src/stores/jobs";
import type { JobItem, JobAttemptItem, JobExplainItem, CreateJobPayload } from "../src/stores/jobs";
import type { ControlAction } from "../src/types/controlPlane";

vi.mock("../src/utils/requestId", () => ({ getRequestId: () => "test-req" }));
vi.mock("../src/utils/logger", () => ({
  logInfo: vi.fn(),
  logWarn: vi.fn(),
  logError: vi.fn(),
}));

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeStatusView(key: string, label: string) {
  return { key, label, tone: "info" };
}

function makeJob(overrides: Partial<JobItem> = {}): JobItem {
  return {
    job_id: "job-001",
    kind: "connector.invoke",
    status: "pending",
    status_view: makeStatusView("pending", "Pending"),
    node_id: null,
    connector_id: null,
    idempotency_key: null,
    priority: 50,
    target_os: null,
    target_arch: null,
    target_executor: null,
    required_capabilities: [],
    target_zone: null,
    required_cpu_cores: null,
    required_memory_mb: null,
    required_gpu_vram_mb: null,
    required_storage_mb: null,
    timeout_seconds: 300,
    max_retries: 0,
    retry_count: 0,
    estimated_duration_s: null,
    source: "console",
    attempt: 0,
    payload: {},
    result: null,
    error_message: null,
    lease_seconds: 30,
    leased_until: null,
    lease_state: "none",
    lease_state_view: makeStatusView("none", "None"),
    attention_reason: null,
    actions: [],
    created_at: "2025-01-01T00:00:00Z",
    started_at: null,
    completed_at: null,
    ...overrides,
  };
}

function makeAttempt(overrides: Partial<JobAttemptItem> = {}): JobAttemptItem {
  return {
    attempt_id: "att-001",
    job_id: "job-001",
    node_id: "node-001",
    lease_token: "tok",
    attempt_no: 1,
    status: "completed",
    status_view: makeStatusView("completed", "Completed"),
    score: 100,
    error_message: null,
    result_summary: null,
    created_at: "2025-01-01T00:00:00Z",
    started_at: "2025-01-01T00:00:01Z",
    completed_at: "2025-01-01T00:00:02Z",
    ...overrides,
  };
}

function makeExplain(): JobExplainItem {
  return {
    job: makeJob(),
    total_nodes: 2,
    eligible_nodes: 1,
    selected_node_id: "node-001",
    decisions: [],
  };
}

function makeAction(key: string, enabled = true): ControlAction {
  return {
    key,
    label: key,
    endpoint: `/v1/jobs/job-001/${key}`,
    method: "POST",
    enabled,
    requires_admin: true,
    reason: enabled ? null : "Action unavailable",
    confirmation: null,
    fields: [],
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("useJobsStore", () => {
  let mock: MockAdapter;

  beforeEach(() => {
    setActivePinia(createPinia());
    mock = new MockAdapter(http);
  });

  afterEach(() => {
    mock.restore();
  });

  // ---- initial state ----
  it("starts with empty state", () => {
    const store = useJobsStore();
    expect(store.items).toEqual([]);
    expect(store.loading).toBe(false);
    expect(store.submitting).toBe(false);
    expect(store.error).toBeNull();
    expect(store.pendingCount).toBe(0);
  });

  // ---- upsertJob ----
  it("upsertJob inserts a new job at the front", () => {
    const store = useJobsStore();
    store.upsertJob({ job_id: "job-001" });
    expect(store.items).toHaveLength(1);
    expect(store.items[0].job_id).toBe("job-001");
  });

  it("upsertJob updates an existing job by id", () => {
    const store = useJobsStore();
    store.upsertJob({ job_id: "job-001", status: "pending" });
    store.upsertJob({ job_id: "job-001", status: "running" });
    expect(store.items).toHaveLength(1);
    expect(store.items[0].status).toBe("running");
  });

  it("upsertJob normalizes missing fields to safe defaults", () => {
    const store = useJobsStore();
    store.upsertJob({ job_id: "job-001" });
    const job = store.items[0];
    expect(job.kind).toBe("unknown");
    expect(job.priority).toBe(50);
    expect(job.required_capabilities).toEqual([]);
    expect(job.timeout_seconds).toBe(300);
    expect(job.lease_state).toBe("none");
  });

  it("upsertJob prepends: newer jobs appear first", () => {
    const store = useJobsStore();
    store.upsertJob({ job_id: "first" });
    store.upsertJob({ job_id: "second" });
    expect(store.items[0].job_id).toBe("second");
  });

  // ---- pendingCount computed ----
  it("pendingCount counts pending and running jobs", () => {
    const store = useJobsStore();
    store.upsertJob({ job_id: "j1", status: "pending", status_view: makeStatusView("pending", "Pending") });
    store.upsertJob({ job_id: "j2", status: "running", status_view: makeStatusView("running", "Running") });
    store.upsertJob({ job_id: "j3", status: "completed", status_view: makeStatusView("completed", "Completed") });
    expect(store.pendingCount).toBe(2);
  });

  // ---- fetchJobs ----
  it("fetchJobs populates items on success", async () => {
    const store = useJobsStore();
    mock.onGet("/v1/jobs").reply(200, [makeJob()]);
    await store.fetchJobs();
    expect(store.items).toHaveLength(1);
    expect(store.loading).toBe(false);
    expect(store.error).toBeNull();
  });

  it("fetchJobs sets error on failure", async () => {
    const store = useJobsStore();
    mock.onGet("/v1/jobs").reply(500, { detail: "Server error" });
    await store.fetchJobs();
    expect(store.items).toEqual([]);
    expect(store.error).toBeTruthy();
  });

  it("fetchJobs passes query params as strings", async () => {
    const store = useJobsStore();
    mock.onGet("/v1/jobs").reply((config) => {
      expect(config.params?.status).toBe("pending");
      return [200, []];
    });
    await store.fetchJobs({ status: "pending" });
  });

  it("fetchJobs serializes numeric limit and offset to string query params", async () => {
    const store = useJobsStore();
    mock.onGet("/v1/jobs").reply((config) => {
      expect(config.params?.limit).toBe("50");
      expect(config.params?.offset).toBe("100");
      return [200, []];
    });
    await store.fetchJobs({ limit: 50, offset: 100 });
  });

  // ---- fetchJob ----
  it("fetchJob returns and upserts job on success", async () => {
    const store = useJobsStore();
    mock.onGet("/v1/jobs/job-001").reply(200, makeJob({ job_id: "job-001", status: "running" }));
    const result = await store.fetchJob("job-001");
    expect(result?.job_id).toBe("job-001");
    expect(store.items).toHaveLength(1);
  });

  it("fetchJob returns null on error", async () => {
    const store = useJobsStore();
    mock.onGet("/v1/jobs/job-001").reply(404);
    const result = await store.fetchJob("job-001");
    expect(result).toBeNull();
  });

  // ---- fetchSchema ----
  it("fetchSchema stores schema on success", async () => {
    const store = useJobsStore();
    const schema = { product: "ZEN70", resource: "jobs", title: "Jobs", profile: "gw", runtime_profile: "gw", columns: [], actions: [], filters: [] };
    mock.onGet("/v1/jobs/schema").reply(200, schema);
    const result = await store.fetchSchema();
    expect(result).toEqual(schema);
    expect(store.schema).toEqual(schema);
  });

  it("fetchSchema returns null on error and sets error field", async () => {
    const store = useJobsStore();
    mock.onGet("/v1/jobs/schema").reply(500);
    const result = await store.fetchSchema();
    expect(result).toBeNull();
    expect(store.error).toBeTruthy();
  });

  // ---- fetchJobAttempts ----
  it("fetchJobAttempts fetches and caches attempts", async () => {
    const store = useJobsStore();
    mock.onGet("/v1/jobs/job-001/attempts").reply(200, [makeAttempt()]);
    const result = await store.fetchJobAttempts("job-001");
    expect(result).toHaveLength(1);
    expect(store.attemptsByJob["job-001"]).toHaveLength(1);
  });

  it("fetchJobAttempts returns cached result without HTTP call", async () => {
    const store = useJobsStore();
    mock.onGet("/v1/jobs/job-001/attempts").reply(200, [makeAttempt()]);
    await store.fetchJobAttempts("job-001");
    const callCount = mock.history.get.length;
    await store.fetchJobAttempts("job-001");
    expect(mock.history.get.length).toBe(callCount); // no extra HTTP call
  });

  it("fetchJobAttempts forces refresh when force=true", async () => {
    const store = useJobsStore();
    mock.onGet("/v1/jobs/job-001/attempts").reply(200, [makeAttempt()]);
    await store.fetchJobAttempts("job-001");
    await store.fetchJobAttempts("job-001", true);
    expect(mock.history.get.length).toBe(2);
  });

  it("fetchJobAttempts returns empty array on error", async () => {
    const store = useJobsStore();
    mock.onGet("/v1/jobs/job-001/attempts").reply(500);
    const result = await store.fetchJobAttempts("job-001");
    expect(result).toEqual([]);
    expect(store.error).toBeTruthy();
  });

  // ---- fetchJobExplain ----
  it("fetchJobExplain fetches and caches explain data", async () => {
    const store = useJobsStore();
    mock.onGet("/v1/jobs/job-001/explain").reply(200, makeExplain());
    const result = await store.fetchJobExplain("job-001");
    expect(result?.job.job_id).toBe("job-001");
    expect(store.explanationsByJob["job-001"]).toBeDefined();
  });

  it("fetchJobExplain returns cached result without HTTP call", async () => {
    const store = useJobsStore();
    mock.onGet("/v1/jobs/job-001/explain").reply(200, makeExplain());
    await store.fetchJobExplain("job-001");
    const callCount = mock.history.get.length;
    await store.fetchJobExplain("job-001");
    expect(mock.history.get.length).toBe(callCount);
  });

  it("fetchJobExplain forces re-fetch when force=true", async () => {
    const store = useJobsStore();
    mock.onGet("/v1/jobs/job-001/explain").reply(200, makeExplain());
    await store.fetchJobExplain("job-001");
    await store.fetchJobExplain("job-001", true);
    expect(mock.history.get.length).toBe(2);
  });

  it("fetchJobExplain returns null on error", async () => {
    const store = useJobsStore();
    mock.onGet("/v1/jobs/job-001/explain").reply(500);
    const result = await store.fetchJobExplain("job-001");
    expect(result).toBeNull();
  });

  it("fetchJobExplain upserts the embedded job into items", async () => {
    const store = useJobsStore();
    mock.onGet("/v1/jobs/job-001/explain").reply(200, makeExplain());
    await store.fetchJobExplain("job-001");
    expect(store.items.some((j) => j.job_id === "job-001")).toBe(true);
  });

  // ---- createJob ----
  it("createJob posts payload, re-fetches list, and returns new job", async () => {
    const store = useJobsStore();
    const newJob = makeJob({ job_id: "job-new" });
    mock.onPost("/v1/jobs").reply(200, newJob);
    mock.onGet("/v1/jobs").reply(200, [newJob]);
    const payload: CreateJobPayload = { kind: "connector.invoke" };
    const result = await store.createJob(payload);
    expect(result?.job_id).toBe("job-new");
    expect(store.submitting).toBe(false);
  });

  it("createJob sets error and returns null on failure", async () => {
    const store = useJobsStore();
    mock.onPost("/v1/jobs").reply(422, { detail: "Validation error" });
    const result = await store.createJob({ kind: "connector.invoke" });
    expect(result).toBeNull();
    expect(store.error).toBeTruthy();
    expect(store.submitting).toBe(false);
  });

  // ---- runJobAction ----
  it("runJobAction with disabled action sets error and returns null", async () => {
    const store = useJobsStore();
    const action = makeAction("cancel", false);
    const result = await store.runJobAction("job-001", action);
    expect(result).toBeNull();
    expect(store.error).toBeTruthy();
  });

  it("runJobAction with explain key calls fetchJobExplain", async () => {
    const store = useJobsStore();
    mock.onGet("/v1/jobs/job-001/explain").reply(200, makeExplain());
    const action = makeAction("explain", true);
    action.method = "GET";
    const result = await store.runJobAction("job-001", action);
    expect(result).toBeDefined();
  });

  it("runJobAction POST upserts returned job", async () => {
    const store = useJobsStore();
    const updated = makeJob({ job_id: "job-001", status: "cancelled" });
    mock.onPost("/v1/jobs/job-001/cancel").reply(200, updated);
    const action = makeAction("cancel");
    const result = await store.runJobAction("job-001", action);
    expect((result as JobItem | null)?.status).toBe("cancelled");
    expect(store.items.some((j) => j.job_id === "job-001" && j.status === "cancelled")).toBe(true);
  });

  it("runJobAction POST clears cached explanation for the job", async () => {
    const store = useJobsStore();
    mock.onGet("/v1/jobs/job-001/explain").reply(200, makeExplain());
    await store.fetchJobExplain("job-001");
    expect(store.explanationsByJob["job-001"]).toBeDefined();

    const updated = makeJob({ job_id: "job-001" });
    mock.onPost("/v1/jobs/job-001/cancel").reply(200, updated);
    await store.runJobAction("job-001", makeAction("cancel"));
    expect(store.explanationsByJob["job-001"]).toBeUndefined();
  });

  it("runJobAction sets error and returns null on HTTP failure", async () => {
    const store = useJobsStore();
    mock.onPost("/v1/jobs/job-001/cancel").reply(500);
    const result = await store.runJobAction("job-001", makeAction("cancel"));
    expect(result).toBeNull();
    expect(store.error).toBeTruthy();
  });

  // ---- cancelJob / retryJobNow ----
  it("cancelJob resolves to the updated job on success", async () => {
    const store = useJobsStore();
    const cancelled = makeJob({ job_id: "job-001", status: "cancelled" });
    mock.onPost("/v1/jobs/job-001/cancel").reply(200, cancelled);
    const result = await store.cancelJob("job-001");
    expect(result?.status).toBe("cancelled");
  });

  it("retryJobNow resolves to the updated job on success", async () => {
    const store = useJobsStore();
    const retried = makeJob({ job_id: "job-001", status: "pending" });
    mock.onPost("/v1/jobs/job-001/retry").reply(200, retried);
    const result = await store.retryJobNow("job-001");
    expect(result?.job_id).toBe("job-001");
  });

  // ---- applyJobEvent ----
  it("applyJobEvent with single job field upserts the job", () => {
    const store = useJobsStore();
    store.applyJobEvent({ job: { job_id: "job-001", status: "running" }, jobs: undefined });
    expect(store.items.some((j) => j.job_id === "job-001" && j.status === "running")).toBe(true);
  });

  it("applyJobEvent with jobs array upserts all jobs", () => {
    const store = useJobsStore();
    store.applyJobEvent({
      job: undefined,
      jobs: [
        { job_id: "j1", status: "pending" },
        { job_id: "j2", status: "running" },
      ],
    });
    expect(store.items).toHaveLength(2);
  });

  it("applyJobEvent ignores events with no job_id", () => {
    const store = useJobsStore();
    store.applyJobEvent({ job: {}, jobs: undefined });
    expect(store.items).toHaveLength(0);
  });

  it("applyJobEvent with null event does not throw", () => {
    const store = useJobsStore();
    expect(() => store.applyJobEvent({ job: null as unknown as Record<string, unknown>, jobs: null as unknown as Record<string, unknown>[] })).not.toThrow();
  });

  // ---- lastUpdatedAt ----
  it("lastUpdatedAt advances after upsertJob", () => {
    const store = useJobsStore();
    const before = store.lastUpdatedAt;
    store.upsertJob({ job_id: "job-001" });
    expect(store.lastUpdatedAt).toBeGreaterThanOrEqual(before);
  });
});
