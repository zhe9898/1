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
import {
  normalizeAiRoutePreference,
  normalizeRole,
  type AuthSessionResponse,
  type SessionClaims,
} from "../src/stores/auth/sessionClaims";
import { AUTH } from "../src/utils/api";

function makeClaims(overrides: Partial<SessionClaims> = {}): SessionClaims {
  return {
    sub: "user-1",
    username: "alice",
    role: "admin",
    tenant_id: "tenant-a",
    scopes: [],
    ai_route_preference: "auto",
    exp: Math.floor(Date.now() / 1000) + 60,
    ...overrides,
  };
}

function makeSession(overrides: Partial<AuthSessionResponse> = {}): AuthSessionResponse {
  return {
    authenticated: true,
    sub: "user-1",
    username: "alice",
    role: "admin",
    tenant_id: "tenant-a",
    scopes: [],
    ai_route_preference: "auto",
    exp: Math.floor(Date.now() / 1000) + 60,
    ...overrides,
  };
}

describe("auth contract alignment", () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    setActivePinia(createPinia());
    vi.useRealTimers();
    httpGetMock.mockReset();
    httpPatchMock.mockReset();
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

  it("keeps frontend roles aligned with backend-issued session claims", () => {
    const store = useAuthStore();

    store.setSessionClaims(makeClaims({ role: "superadmin" }));
    expect(store.role).toBe("superadmin");
    expect(store.isAdmin).toBe(true);

    store.setSessionClaims(makeClaims({ role: "geek" }));
    expect(store.role).toBe("geek");
    expect(store.isAdmin).toBe(false);

    store.setSessionClaims(makeClaims({ role: "guest" }));
    expect(store.role).toBe("guest");
    expect(store.isAdmin).toBe(false);
  });

  it("normalizes legacy role aliases and invalid AI route preferences", () => {
    expect(normalizeRole("family_child")).toBe("child");
    expect(normalizeRole("长辈")).toBe("elder");
    expect(normalizeAiRoutePreference("edge")).toBe("auto");
    expect(normalizeAiRoutePreference("cloud")).toBe("cloud");
  });

  it("keeps cookie-primary auth state in memory only and never persists tokens to web storage", () => {
    const store = useAuthStore();

    store.setSessionClaims(makeClaims({ sub: "admin-1", role: "admin" }));
    expect("token" in store).toBe(false);
    expect(store.isAuthenticated).toBe(true);
    expect(store.identityKey).toBe("admin-1:admin");
    expect(sessionStorage.getItem("zen70-token")).toBeNull();
    expect(localStorage.getItem("zen70-token")).toBeNull();

    store.setSessionClaims(null);
    expect(store.isAuthenticated).toBe(false);
  });

  it("clears the in-memory session once its exp claim is reached", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-01T00:00:00Z"));

    const store = useAuthStore();
    const exp = Math.floor(Date.now() / 1000) + 2;
    store.setSessionClaims(makeClaims({ exp }));

    expect(store.isAuthenticated).toBe(true);
    vi.advanceTimersByTime(2_500);
    expect(store.isAuthenticated).toBe(false);
  });

  it("falls back to the cookie-backed session endpoint when a response body is unauthenticated", async () => {
    httpGetMock.mockResolvedValue({ data: { authenticated: false } });
    const store = useAuthStore();
    store.setSessionClaims(makeClaims());

    await store.acceptAuthenticatedSession({ authenticated: false });

    expect(store.isAuthenticated).toBe(false);
    expect(httpGetMock).toHaveBeenCalledWith(AUTH.session);
  });

  it("hydrates authenticated session state from the cookie-backed session endpoint", async () => {
    httpGetMock.mockResolvedValue({
      data: makeSession({
        sub: "user-7",
        username: "alice",
        role: "admin",
        tenant_id: "tenant-a",
        scopes: ["write:jobs"],
        ai_route_preference: "cloud",
      }),
    });

    const store = useAuthStore();
    await store.hydrateSession();

    expect(store.isAuthenticated).toBe(true);
    expect(store.role).toBe("admin");
    expect(store.aiRoutePreference).toBe("cloud");
    expect(store.identityKey).toBe("user-7:admin");
    expect(store.sessionPayload?.sub).toBe("user-7");
  });

  it("accepts authenticated session bodies directly without parsing bearer tokens", async () => {
    const store = useAuthStore();

    await store.acceptAuthenticatedSession(
      makeSession({
        sub: "user-9",
        role: "family",
        tenant_id: "tenant-home",
        scopes: ["read:jobs"],
      }),
    );

    expect(store.isAuthenticated).toBe(true);
    expect(store.identityKey).toBe("user-9:family");
    expect(store.role).toBe("family");
  });
});
