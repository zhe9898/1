import { createRouter, createWebHistory } from "vue-router";
import { useAuthStore } from "@/stores/auth";
import { ADMIN_ONLY_ROUTE_NAMES, addControlPlaneRoutesFromSurfaces, getControlPlaneRoutes } from "@/config/controlPlane";
import type { ControlPlaneSurfaceSpec } from "@/config/controlPlane";
import { http } from "@/utils/http";
import { CONSOLE } from "@/utils/api";

// Kernel routes are always present. Control-plane routes are backend-driven and
// refreshed whenever the authenticated identity changes.

/**
 * Opaque key identifying the currently loaded control-plane surface set.
 * Format: "<sub>:<role>" and changes on login, logout, or role change.
 * null = surfaces have never been loaded for the current identity.
 */
let _surfacesLoadedForIdentity: string | null = null;
let _surfacesLoadingForIdentity: string | null = null;
let _surfacesLoadingPromise: Promise<boolean> | null = null;

/** Derive a stable identity key from the auth store for surface cache keying. */
function identityKey(auth: ReturnType<typeof useAuthStore>): string | null {
  return auth.identityKey;
}

function redirectAfterLogin(auth: ReturnType<typeof useAuthStore>): { name: string } {
  return auth.isAuthenticated ? { name: "dashboard" } : { name: "login" };
}

async function fetchControlPlaneSurfaces(): Promise<ControlPlaneSurfaceSpec[]> {
  const response = await http.get<{ surfaces: ControlPlaneSurfaceSpec[] }>(CONSOLE.surfaces);
  return response.data.surfaces;
}

export function resetControlPlaneSurfaceSyncState(): void {
  _surfacesLoadedForIdentity = null;
  _surfacesLoadingForIdentity = null;
  _surfacesLoadingPromise = null;
}

export async function syncControlPlaneSurfacesForIdentity(
  targetRouter: ReturnType<typeof createRouter>,
  currentIdentity: string | null,
  fetchSurfaces: () => Promise<ControlPlaneSurfaceSpec[]> = fetchControlPlaneSurfaces,
): Promise<boolean> {
  if (currentIdentity === null) {
    resetControlPlaneSurfaceSyncState();
    return false;
  }
  if (currentIdentity === _surfacesLoadedForIdentity) {
    return false;
  }
  if (_surfacesLoadingPromise && _surfacesLoadingForIdentity === currentIdentity) {
    return await _surfacesLoadingPromise;
  }

  _surfacesLoadingForIdentity = currentIdentity;
  _surfacesLoadingPromise = (async () => {
    try {
      const surfaces = await fetchSurfaces();
      addControlPlaneRoutesFromSurfaces(targetRouter, surfaces);
      _surfacesLoadedForIdentity = currentIdentity;
      return true;
    } catch {
      return false;
    } finally {
      _surfacesLoadingForIdentity = null;
      _surfacesLoadingPromise = null;
    }
  })();

  return await _surfacesLoadingPromise;
}

export const router = createRouter({
  history: createWebHistory(),
  routes: [
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
    ...getControlPlaneRoutes(),
  ],
});

router.beforeEach(async (to, _from, next) => {
  const auth = useAuthStore();
  if (!auth.hydrated) {
    await auth.hydrateSession();
  }

  const currentIdentity = identityKey(auth);
  const refreshed = await syncControlPlaneSurfacesForIdentity(router, currentIdentity);
  if (refreshed) {
    // Re-resolve after new backend-authoritative routes have been mounted.
    next(to.fullPath);
    return;
  }

  if (to.meta.requiresAuth && !auth.isAuthenticated) {
    next({ name: "login" });
  } else if (
    auth.isAuthenticated &&
    typeof to.name === "string" &&
    ADMIN_ONLY_ROUTE_NAMES.has(to.name) &&
    !auth.isAdmin
  ) {
    next({ name: "dashboard" });
  } else if (to.name === "login" && auth.isAuthenticated) {
    next(redirectAfterLogin(auth));
  } else {
    next();
  }
});
