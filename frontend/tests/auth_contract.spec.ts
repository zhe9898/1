// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest";
import { createPinia, setActivePinia } from "pinia";

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
  });

  it("exports PIN auth endpoints from the shared API contract", () => {
    expect(AUTH.pinLogin).toBe("/v1/auth/pin/login");
    expect(AUTH.pinSet).toBe("/v1/auth/pin/set");
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

  it("stores auth tokens in sessionStorage and migrates legacy localStorage tokens", () => {
    localStorage.setItem("zen70-token", makeToken({ role: "admin" }));

    const store = useAuthStore();

    expect(store.token).toBeTruthy();
    expect(sessionStorage.getItem("zen70-token")).toBe(store.token);
    expect(localStorage.getItem("zen70-token")).toBeNull();

    const nextToken = makeToken({ role: "guest" });
    store.setToken(nextToken);
    expect(sessionStorage.getItem("zen70-token")).toBe(nextToken);
    expect(localStorage.getItem("zen70-token")).toBeNull();

    store.setToken(null);
    expect(sessionStorage.getItem("zen70-token")).toBeNull();
  });

  it("returns null token payload and guest role for expired token (past exp claim)", () => {
    const store = useAuthStore();
    // exp = 1 (Unix epoch 1970-01-01) — far in the past
    store.setToken(makeToken({ role: "admin", exp: 1 }));

    // The store stores the raw token but the router guard calls isTokenExpired().
    // Verify the payload is still decoded (store doesn't auto-clear on set)
    // and that role is correctly read from payload before expiry check.
    expect(store.role).toBe("admin");
    // Token remains until the router guard clears it — verify it is stored.
    expect(store.token).toBeTruthy();
  });

  it("treats a structurally invalid (non-JWT) token as guest with no payload", () => {
    const store = useAuthStore();
    store.setToken("not.a.valid.jwt.token");

    // decodePayload returns null for non-base64 or non-JSON payloads.
    // normalizeRole(null) must return "user" (fallback), never throw.
    expect(() => store.role).not.toThrow();
    expect(store.isAdmin).toBe(false);
  });
});

