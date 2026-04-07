/**
 */
import { defineStore } from "pinia";
import { computed, ref } from "vue";

import { AUTH } from "@/utils/api";
import { http } from "@/utils/http";
import {
  claimsFromSessionResponse,
  normalizeRole,
  sessionClaimsToPayload,
  type AuthPayload,
  type AuthSessionResponse,
  type Role,
  type SessionClaims,
} from "@/stores/auth/sessionClaims";

export const useAuthStore = defineStore("auth", () => {
  const sessionClaims = ref<SessionClaims | null>(null);
  const hydrated = ref(false);
  let expiryTimer: number | null = null;
  let hydrationPromise: Promise<void> | null = null;

  const sessionPayload = computed<AuthPayload | null>(() => {
    return sessionClaimsToPayload(sessionClaims.value);
  });

  const role = computed<Role>(() => {
    const payload = sessionPayload.value;
    return normalizeRole(typeof payload?.role === "string" ? payload.role : null);
  });

  const aiRoutePreference = computed<string>(() => {
    const payload = sessionPayload.value;
    return typeof payload?.ai_route_preference === "string" ? payload.ai_route_preference : "auto";
  });

  const isAdmin = computed(() => {
    return role.value === "superadmin" || role.value === "admin";
  });
  const isFamily = computed(() => role.value === "family");
  const isChild = computed(() => role.value === "child");
  const isElder = computed(() => role.value === "elder");
  const isAuthenticated = computed(() => sessionPayload.value !== null);
  const identityKey = computed(() => {
    const payload = sessionPayload.value;
    if (!payload || typeof payload.sub !== "string" || !payload.sub) {
      return null;
    }
    const roleValue = typeof payload.role === "string" && payload.role ? payload.role : "user";
    return `${payload.sub}:${roleValue}`;
  });

  function clearExpiryTimer(): void {
    if (expiryTimer !== null) {
      window.clearTimeout(expiryTimer);
      expiryTimer = null;
    }
  }

  function scheduleExpiryClear(claims: SessionClaims | null): void {
    clearExpiryTimer();
    if (typeof window === "undefined") {
      return;
    }
    const expiresAtMs =
      claims?.exp !== null && claims?.exp !== undefined
        ? claims.exp * 1000
        : null;
    if (expiresAtMs === null) {
      return;
    }
    const delayMs = Math.max(expiresAtMs - Date.now() - 1_000, 0);
    expiryTimer = window.setTimeout(() => {
      clearLocalSession();
    }, delayMs);
  }

  function applyClaims(claims: SessionClaims | null): void {
    sessionClaims.value = claims;
    scheduleExpiryClear(claims);
  }

  function clearLocalSession(): void {
    applyClaims(null);
  }

  function setSessionClaims(claims: SessionClaims | null): void {
    hydrated.value = true;
    applyClaims(claims);
  }

  async function acceptAuthenticatedSession(session: AuthSessionResponse | null | undefined): Promise<void> {
    if (session) {
      const claims = claimsFromSessionResponse(session);
      hydrated.value = true;
      if (claims !== null) {
        applyClaims(claims);
        return;
      }
    }
    await hydrateSession(true);
  }

  async function hydrateSession(force = false): Promise<void> {
    if (hydrated.value && !force) {
      return;
    }
    if (hydrationPromise) {
      return hydrationPromise;
    }

    hydrationPromise = (async () => {
      try {
        const { data } = await http.get<AuthSessionResponse>(AUTH.session);
        applyClaims(claimsFromSessionResponse(data));
      } catch {
        clearLocalSession();
      } finally {
        hydrated.value = true;
        hydrationPromise = null;
      }
    })();

    return hydrationPromise;
  }

  async function updateAiPreference(preference: "local" | "cloud" | "auto"): Promise<void> {
    const { data } = await http.patch<AuthSessionResponse>(AUTH.updateAiPreference, {
      preference,
    });
    await acceptAuthenticatedSession(data);
  }

  return {
    sessionPayload,
    role,
    aiRoutePreference,
    isAdmin,
    isFamily,
    isChild,
    isElder,
    isAuthenticated,
    identityKey,
    hydrated,
    setSessionClaims,
    acceptAuthenticatedSession,
    hydrateSession,
    updateAiPreference,
  };
});
