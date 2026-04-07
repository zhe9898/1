import type { StatusView } from "@/types/controlPlane";
import { normalizeStatusView } from "@/utils/statusView";

function normalizeStatusValue(value: string | null | undefined, fallback: string): string {
  const normalized = (value ?? "").trim().toLowerCase();
  if (!normalized) {
    return fallback;
  }
  return normalized;
}

function buildFallbackStatusView(status: string): StatusView {
  if (status === "leased" || status === "running") {
    return { key: "running", label: "Running", tone: "warning" };
  }
  if (status === "completed") {
    return { key: "completed", label: "Completed", tone: "success" };
  }
  if (status === "failed") {
    return { key: "failed", label: "Failed", tone: "danger" };
  }
  if (status === "timeout") {
    return { key: "timeout", label: "Timeout", tone: "danger" };
  }
  if (status === "cancelled") {
    return { key: "cancelled", label: "Cancelled", tone: "neutral" };
  }
  if (status === "pending") {
    return { key: "pending", label: "Pending", tone: "neutral" };
  }
  return {
    key: status,
    label: status ? `${status.charAt(0).toUpperCase()}${status.slice(1)}` : "Unknown",
    tone: "neutral",
  };
}

export function normalizeJobStatusValue(value: string | null | undefined, fallback = "unknown"): string {
  return normalizeStatusValue(value, fallback);
}

export function normalizeJobAttemptStatusValue(value: string | null | undefined, fallback = "unknown"): string {
  return normalizeStatusValue(value, fallback);
}

export function normalizeJobStatusView(raw: unknown, status: string): StatusView {
  const fallback = buildFallbackStatusView(status);
  return normalizeStatusView(raw, fallback.key, fallback.label, fallback.tone);
}
