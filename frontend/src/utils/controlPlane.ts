import type { Capabilities, Capability } from "@/types/capability";

export function normalizeEndpoint(endpoint: string | null | undefined): string {
  const normalized = (endpoint ?? "").split("?", 1)[0].replace(/\/+$/, "");
  return normalized || "/";
}

export function findCapabilityByEndpoint(
  caps: Capabilities,
  endpoint: string
): Capability | null {
  const target = normalizeEndpoint(endpoint);
  for (const capability of Object.values(caps)) {
    if (normalizeEndpoint(capability.endpoint) === target) {
      return capability;
    }
  }
  return null;
}
