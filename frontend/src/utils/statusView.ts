import type { StatusView } from "@/types/controlPlane";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function toStringValue(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

export function normalizeStatusView(
  raw: unknown,
  fallbackKey = "unknown",
  fallbackLabel = "Unknown",
  fallbackTone = "neutral"
): StatusView {
  if (!isRecord(raw)) {
    return {
      key: fallbackKey,
      label: fallbackLabel,
      tone: fallbackTone,
    };
  }

  const key = toStringValue(raw.key, fallbackKey);
  const label = toStringValue(raw.label, fallbackLabel);
  const tone = toStringValue(raw.tone, fallbackTone);
  return { key, label, tone };
}

export function badgeClassFromTone(tone: string): string {
  if (tone === "success") return "badge-success";
  if (tone === "warning") return "badge-warning";
  if (tone === "danger") return "badge-error";
  if (tone === "info") return "badge-info";
  return "badge-ghost";
}

export function solidBadgeClassFromTone(tone: string): string {
  if (tone === "success") return "bg-emerald-600 text-white";
  if (tone === "warning") return "bg-amber-600 text-white";
  if (tone === "danger") return "bg-rose-600 text-white";
  if (tone === "info") return "bg-sky-600 text-white";
  return "bg-base-300 text-base-content/70";
}

export function surfaceClassFromTone(tone: string): string {
  if (tone === "success") return "border-emerald-200 bg-emerald-50/80";
  if (tone === "warning") return "border-amber-200 bg-amber-50/80";
  if (tone === "danger") return "border-rose-200 bg-rose-50/80";
  if (tone === "info") return "border-sky-200 bg-sky-50/80";
  return "border-base-300 bg-base-100";
}
