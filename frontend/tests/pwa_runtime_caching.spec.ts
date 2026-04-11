import { describe, expect, it } from "vitest";

import {
  IDENTITY_SCOPED_API_PATHS,
  REPLAY_SAFE_BACKGROUND_SYNC_PATHS,
  isIdentityScopedApiPath,
  workboxRuntimeCaching,
} from "../src/pwa/runtimeCaching";

describe("pwa runtime caching contracts", () => {
  it("marks identity-scoped control-plane endpoints as non-cacheable API surfaces", () => {
    expect(IDENTITY_SCOPED_API_PATHS).toEqual([
      "/api/v1/auth/session",
      "/api/v1/capabilities",
      "/api/v1/console/menu",
      "/api/v1/console/surfaces",
      "/api/v1/profile",
    ]);
    expect(isIdentityScopedApiPath("/api/v1/auth/session")).toBe(true);
    expect(isIdentityScopedApiPath("/api/v1/console/surfaces")).toBe(true);
    expect(isIdentityScopedApiPath("/api/v1/jobs")).toBe(false);
  });

  it("does not install a generic /api runtime cache", () => {
    const serializedPatterns = workboxRuntimeCaching.map((rule) => String(rule.urlPattern));
    expect(serializedPatterns.some((pattern) => pattern.includes("/api/"))).toBe(false);
  });

  it("disables background sync until a replay-safe endpoint allowlist exists", () => {
    expect(REPLAY_SAFE_BACKGROUND_SYNC_PATHS).toEqual([]);
  });
});
