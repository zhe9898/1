/**
 */
import { defineStore } from "pinia";
import { computed, ref } from "vue";

import { AUTH } from "@/utils/api";
import { http } from "@/utils/http";
import { decodePayload, getTokenExpiryMs, isWellFormedJwt, type JwtPayload } from "@/utils/jwt";

export type Role = "superadmin" | "admin" | "geek" | "family" | "child" | "elder" | "guest" | "user";

interface AuthSessionResponse {
  authenticated: boolean;
  sub?: string | null;
  username?: string | null;
  role?: string | null;
  tenant_id?: string | null;
  scopes?: string[];
  ai_route_preference?: string | null;
  exp?: number | null;
}

interface SessionClaims {
  sub: string;
  username: string | null;
  role: string | null;
  tenant_id: string | null;
  scopes: string[];
  ai_route_preference: string;
  exp: number | null;
}

type AuthPayload = JwtPayload & {
  tenant_id?: string;
  scopes?: string[];
};

function normalizeRole(rawRole: string | null | undefined): Role {
  const role = (rawRole ?? "user").toLowerCase();
  if (role === "superadmin") return "superadmin";
  if (role === "admin") return "admin";
  if (role === "geek") return "geek";
  if (role === "elder" || role === "family_elder") return "elder";
  if (role === "family") return "family";
  if (role === "child" || role === "family_child") return "child";
  if (role === "guest") return "guest";
  return "user";
}

function normalizeToken(value: string | null): string | null {
  if (typeof value !== "string") {
    return null;
  }
  const normalized = value.trim();
  if (!normalized || normalized !== value || !isWellFormedJwt(normalized)) {
    return null;
  }
  const payload = decodePayload(normalized);
  if (payload === null) {
    return null;
  }
  const expiresAtMs = getTokenExpiryMs(normalized);
  if (expiresAtMs !== null && Date.now() >= expiresAtMs) {
    return null;
  }
  return normalized;
}

function extractSessionClaims(source: AuthPayload | null): SessionClaims | null {
  if (!source) {
    return null;
  }
  const rawSub = typeof source.sub === "string" ? source.sub.trim() : "";
  if (!rawSub) {
    return null;
  }
  const rawScopes = source.scopes;
  return {
    sub: rawSub,
    username: typeof source.username === "string" ? source.username : null,
    role: typeof source.role === "string" ? source.role : null,
    tenant_id: typeof source.tenant_id === "string" ? source.tenant_id : null,
    scopes: Array.isArray(rawScopes)
      ? rawScopes.filter((scope): scope is string => typeof scope === "string" && scope.length > 0)
      : [],
    ai_route_preference:
      typeof source.ai_route_preference === "string" && source.ai_route_preference.length > 0
        ? source.ai_route_preference
        : "auto",
    exp: typeof source.exp === "number" && Number.isFinite(source.exp) ? source.exp : null,
  };
}

function sessionClaimsToPayload(claims: SessionClaims | null): AuthPayload | null {
  if (!claims) {
    return null;
  }
  return {
    sub: claims.sub,
    username: claims.username ?? undefined,
    role: claims.role ?? undefined,
    tenant_id: claims.tenant_id ?? undefined,
    scopes: claims.scopes,
    ai_route_preference: claims.ai_route_preference,
    exp: claims.exp ?? undefined,
  };
}

export const useAuthStore = defineStore("auth", () => {
  const token = ref<string | null>(null);
  const sessionClaims = ref<SessionClaims | null>(null);
  const hydrated = ref(false);
  let expiryTimer: number | null = null;
  let hydrationPromise: Promise<void> | null = null;

  const tokenPayload = computed<AuthPayload | null>(() => {
    if (token.value) {
      return decodePayload(token.value);
    }
    return sessionClaimsToPayload(sessionClaims.value);
  });

  const role = computed<Role>(() => {
    const payload = tokenPayload.value;
    return normalizeRole(typeof payload?.role === "string" ? payload.role : null);
  });

  const aiRoutePreference = computed<string>(() => {
    const payload = tokenPayload.value;
    return typeof payload?.ai_route_preference === "string" ? payload.ai_route_preference : "auto";
  });

  const isAdmin = computed(() => {
    return role.value === "superadmin" || role.value === "admin";
  });
  const isFamily = computed(() => role.value === "family");
  const isChild = computed(() => role.value === "child");
  const isElder = computed(() => role.value === "elder");
  const isAuthenticated = computed(() => tokenPayload.value !== null);
  const identityKey = computed(() => {
    const payload = tokenPayload.value;
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

  function scheduleExpiryClear(claims: SessionClaims | null, tokenValue: string | null = null): void {
    clearExpiryTimer();
    if (typeof window === "undefined") {
      return;
    }
    const expiresAtMs =
      claims?.exp !== null && claims?.exp !== undefined
        ? claims.exp * 1000
        : tokenValue
          ? getTokenExpiryMs(tokenValue)
          : null;
    if (expiresAtMs === null) {
      return;
    }
    const delayMs = Math.max(expiresAtMs - Date.now() - 1_000, 0);
    expiryTimer = window.setTimeout(() => {
      setToken(null);
    }, delayMs);
  }

  function applyClaims(claims: SessionClaims | null): void {
    sessionClaims.value = claims;
    scheduleExpiryClear(claims, token.value);
  }

  function clearLocalSession(): void {
    token.value = null;
    applyClaims(null);
  }

  function setToken(value: string | null): void {
    hydrated.value = true;
    const normalized = normalizeToken(value);
    token.value = normalized;
    if (!normalized) {
      applyClaims(null);
      return;
    }
    const claims = extractSessionClaims(decodePayload(normalized));
    sessionClaims.value = claims;
    scheduleExpiryClear(claims, normalized);
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
        if (data.authenticated) {
          applyClaims(
            extractSessionClaims({
              sub: data.sub ?? undefined,
              username: data.username ?? undefined,
              role: data.role ?? undefined,
              tenant_id: data.tenant_id ?? undefined,
              scopes: data.scopes ?? [],
              ai_route_preference: data.ai_route_preference ?? undefined,
              exp: data.exp ?? undefined,
            }),
          );
        } else {
          clearLocalSession();
        }
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
    const { data } = await http.patch<{ access_token: string }>(AUTH.updateAiPreference, {
      preference,
    });
    setToken((data.access_token as string | undefined) ?? null);
  }

  return {
    token,
    tokenPayload,
    role,
    aiRoutePreference,
    isAdmin,
    isFamily,
    isChild,
    isElder,
    isAuthenticated,
    identityKey,
    hydrated,
    setToken,
    hydrateSession,
    updateAiPreference,
  };
});
