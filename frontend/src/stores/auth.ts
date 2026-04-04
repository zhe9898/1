/**
 */
import { defineStore } from "pinia";
import { computed, ref } from "vue";

import { AUTH } from "@/utils/api";
import { http } from "@/utils/http";
import { decodePayload } from "@/utils/jwt";

const TOKEN_KEY = "zen70-token";

function hasWebStorage(): boolean {
  return typeof window !== "undefined" && typeof window.sessionStorage !== "undefined";
}

function readInitialToken(): string | null {
  if (!hasWebStorage()) {
    return null;
  }
  const sessionToken = sessionStorage.getItem(TOKEN_KEY);
  if (sessionToken) {
    return sessionToken;
  }
  const legacyToken = localStorage.getItem(TOKEN_KEY);
  if (!legacyToken) {
    return null;
  }
  sessionStorage.setItem(TOKEN_KEY, legacyToken);
  localStorage.removeItem(TOKEN_KEY);
  return legacyToken;
}

export type Role = "superadmin" | "admin" | "geek" | "family" | "child" | "elder" | "guest" | "user";

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

export const useAuthStore = defineStore("auth", () => {
  const token = ref(readInitialToken());

  const tokenPayload = computed(() => {
    return token.value ? decodePayload(token.value) : null;
  });

  const role = computed<Role>(() => {
    return normalizeRole(tokenPayload.value?.role);
  });

  const aiRoutePreference = computed<string>(() => {
    return tokenPayload.value?.ai_route_preference ?? "auto";
  });

  const isAdmin = computed(() => {
    return role.value === "superadmin" || role.value === "admin";
  });
  const isFamily = computed(() => role.value === "family");
  const isChild = computed(() => role.value === "child");
  const isElder = computed(() => role.value === "elder");

  function setToken(value: string | null): void {
    token.value = value;
    if (!hasWebStorage()) {
      return;
    }
    if (value) {
      sessionStorage.setItem(TOKEN_KEY, value);
      localStorage.removeItem(TOKEN_KEY);
      return;
    }
    sessionStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(TOKEN_KEY);
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
    setToken,
    updateAiPreference,
  };
});
