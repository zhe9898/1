import { createRouter, createWebHistory } from "vue-router";
import { useAuthStore } from "@/stores/auth";
import { ADMIN_ONLY_ROUTE_NAMES, addControlPlaneRoutesFromSurfaces, getControlPlaneRoutes } from "@/config/controlPlane";
import type { ControlPlaneSurfaceSpec } from "@/config/controlPlane";
import { decodePayload, isTokenExpired } from "@/utils/jwt";
import { http } from "@/utils/http";
import { CONSOLE } from "@/utils/api";

// ── Kernel + Control Plane Architecture ──────────────────────────────────────
//
// KERNEL routes  → always registered, never removed, pre-auth accessible.
//                  (login, invite)
//
// CONTROL PLANE  → fetched from backend /api/v1/console/surfaces on every
// routes           distinct identity (sub:role). Backend is the single source
//                  of truth (ADR 0011). Static JSON is the offline fallback.
//
// When the authenticated identity changes (login, logout, role change), the
// control-plane surfaces are re-fetched so that admin vs non-admin surfaces
// are always in sync with the backend's authoritative view.
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Opaque key identifying the currently loaded control-plane surface set.
 * Format: "<sub>:<role>" — changes on login, logout, or role change.
 * null = surfaces have never been loaded for the current identity.
 */
let _surfacesLoadedForIdentity: string | null = null;

/** Derive a stable identity key from the auth store for surface cache keying. */
function identityKey(auth: ReturnType<typeof useAuthStore>): string | null {
  if (!auth.token) return null;
  const payload = decodePayload(auth.token);
  if (!payload) return null;
  // sub uniquely identifies the user; role determines which surfaces are visible.
  return `${payload.sub ?? "anon"}:${payload.role ?? "user"}`;
}

function redirectAfterLogin(auth: ReturnType<typeof useAuthStore>): { name: string } {
  return auth.token ? { name: "dashboard" } : { name: "login" };
}

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    // ── Kernel routes (permanent, pre-auth) ────────────────────────────────
    {
      path: "/login",
      name: "login",
      component: () => import("@/views/Login.vue"),
      meta: { title: "Login" },
    },
    {
      path: "/invite",
      name: "invite",
      component: () => import("@/views/InviteView.vue"),
      meta: { title: "Invite" },
    },

    // Old paths (/family /elderly /kids /gallery /media /iot /board etc.) now return 404.

    // ── Control-plane fallback routes ──────────────────────────────────────
    // Built from the bundled controlPlaneSurfaces.json so the app works
    // offline or before the first backend round-trip completes.
    // addControlPlaneRoutesFromSurfaces() updates these with the authoritative
    // backend definition on every authenticated session.
    ...getControlPlaneRoutes(),
  ],
});

router.beforeEach(async (to, _from, next) => {
  const auth = useAuthStore();

  // Clear stale tokens before any guard logic.
  if (auth.token && isTokenExpired(auth.token)) {
    auth.setToken(null);
  }

  // ── Control Plane Surface Sync ────────────────────────────────────────────
  // Fetch authoritative surfaces from the backend whenever the authenticated
  // identity changes (fresh login, role change, or returning after logout).
  // This ensures admin surfaces (e.g. settings) appear only for admin roles
  // and disappear when the same browser session switches to a guest identity.
  const currentIdentity = identityKey(auth);
  if (currentIdentity !== null && currentIdentity !== _surfacesLoadedForIdentity) {
    _surfacesLoadedForIdentity = currentIdentity; // guard against concurrent navigations
    try {
      const response = await http.get<{ surfaces: ControlPlaneSurfaceSpec[] }>(CONSOLE.surfaces);
      const surfaces = response.data?.surfaces ?? [];
      addControlPlaneRoutesFromSurfaces(router, surfaces);
      // Force Vue Router to re-resolve the target path with the newly registered
      // routes in place; without this the navigation would 404 on fresh routes.
      next(to.fullPath);
      return;
    } catch {
      // Backend unreachable — static fallback routes remain active.
      // Identity is already recorded so we don't retry on every navigation.
    }
  }

  // ── Standard Auth Guards ──────────────────────────────────────────────────
  if (to.meta.requiresAuth && !auth.token) {
    next({ name: "login" });
  } else if (
    auth.token &&
    typeof to.name === "string" &&
    ADMIN_ONLY_ROUTE_NAMES.has(to.name) &&
    !auth.isAdmin
  ) {
    next({ name: "dashboard" });
  } else if (to.name === "login" && auth.token) {
    next(redirectAfterLogin(auth));
  } else {
    next();
  }
});


