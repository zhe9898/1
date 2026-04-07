// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

const { httpGetMock, httpPatchMock } = vi.hoisted(() => ({
  httpGetMock: vi.fn(),
  httpPatchMock: vi.fn(),
}));

vi.mock("@/utils/http", () => ({
  http: {
    get: httpGetMock,
    patch: httpPatchMock,
  },
}));

import { useAuthStore } from "../src/stores/auth";
import { AUTH } from "../src/utils/api";

function makeToken(payload: Record<string, unknown>): string {
  const encode = (value: object) => btoa(JSON.stringify(value));
  return `${encode({ alg: "none", typ: "JWT" })}.${encode(payload)}.sig`;
}

describe("auth contract alignment", () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    setActivePinia(createPinia());
    vi.useRealTimers();
    httpGetMock.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("exports PIN auth endpoints from the shared API contract", () => {
    expect(AUTH.pinLogin).toBe("/v1/auth/pin/login");
    expect(AUTH.pinSet).toBe("/v1/auth/pin/set");
    expect(AUTH.session).toBe("/v1/auth/session");
    expect(AUTH.pushVapidKey).toBe("/v1/auth/push/vapid-public-key");
    expect(AUTH.pushSubscribe).toBe("/v1/auth/push/subscribe");
  });

  it("keeps frontend roles aligned with backend-issued roles", () => {
    const store = useAuthStore();

    store.setToken(makeToken({ role: "superadmin" }));
    expect(store.role).toBe("superadmin");
    expect(store.isAdmin).toBe(true);

    store.setToken(makeToken({ role: "geek" }));
    expect(store.role).toBe("geek");
    expect(store.isAdmin).toBe(false);

    store.setToken(makeToken({ role: "guest" }));
    expect(store.role).toBe("guest");
    expect(store.isAdmin).toBe(false);
  });

  it("keeps access tokens in memory only and never persists them to web storage", () => {
    const store = useAuthStore();
    const nextToken = makeToken({ sub: "admin-1", role: "admin" });

    store.setToken(nextToken);
    expect(store.token).toBe(nextToken);
    expect(store.isAuthenticated).toBe(true);
    expect(store.identityKey).toBe("admin-1:admin");
    expect(sessionStorage.getItem("zen70-token")).toBeNull();
    expect(localStorage.getItem("zen70-token")).toBeNull();

    store.setToken(null);
    expect(store.token).toBeNull();
    expect(store.isAuthenticated).toBe(false);
  });

  it("drops expired tokens immediately instead of keeping them in memory", () => {
    const store = useAuthStore();
    // exp = 1 (Unix epoch 1970-01-01) — far in the past
    store.setToken(makeToken({ role: "admin", exp: 1 }));

    // The store stores the raw token but the router guard calls isTokenExpired().
    // Verify the payload is still decoded (store doesn't auto-clear on set)
    // and that role is correctly read from payload before expiry check.
    expect(store.token).toBeNull();
    // Token remains until the router guard clears it — verify it is stored.
    expect(store.role).toBe("user");
  });

  it("treats a structurally invalid (non-JWT) token as guest with no payload", () => {
    const store = useAuthStore();
    store.setToken("not.a.valid.jwt.token");

    // decodePayload returns null for non-base64 or non-JSON payloads.
    // normalizeRole(null) must return "user" (fallback), never throw.
    expect(() => store.role).not.toThrow();
    expect(store.isAdmin).toBe(false);
  });

  it("rejects tokens with leading or trailing whitespace", () => {
    const store = useAuthStore();
    store.setToken(` ${makeToken({ role: "admin" })}`);

    expect(store.token).toBeNull();
  });

  it("clears a valid token once its exp claim is reached", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-01T00:00:00Z"));

    const store = useAuthStore();
    const exp = Math.floor(Date.now() / 1000) + 2;
    store.setToken(makeToken({ role: "admin", exp }));

    expect(store.token).toBeTruthy();
    vi.advanceTimersByTime(2_500);
    expect(store.token).toBeNull();
  });

  it("hydrates authenticated session state from the cookie-backed session endpoint", async () => {
    httpGetMock.mockResolvedValue({
      data: {
        authenticated: true,
        sub: "user-7",
        username: "alice",
        role: "admin",
        tenant_id: "tenant-a",
        scopes: ["write:jobs"],
        ai_route_preference: "cloud",
        exp: Math.floor(Date.now() / 1000) + 60,
      },
    });

    const store = useAuthStore();
    await store.hydrateSession();

    expect(store.isAuthenticated).toBe(true);
    expect(store.role).toBe("admin");
    expect(store.aiRoutePreference).toBe("cloud");
    expect(store.identityKey).toBe("user-7:admin");
    expect(store.token).toBeNull();
  });
});
