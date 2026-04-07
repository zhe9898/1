import type { RouteRecordRaw, Router } from "vue-router";
import rawSurfaces from "./controlPlaneSurfaces.json";

export interface ControlPlaneSurfaceSpec {
  capability_key: string;
  route_name: string;
  route_path: string;
  label: string;
  description: string;
  endpoint: string;
  backend_router: string;
  frontend_view: string;
  profiles: string[];
  requires_admin: boolean;
}

export const CONTROL_PLANE_SURFACES = rawSurfaces as ControlPlaneSurfaceSpec[];

type ControlPlaneViewLoader = () => Promise<unknown>;

/**
 * All possible view loaders keyed by route_name.
 * Must be exhaustive so that dynamic routes registered at runtime
 * (via addControlPlaneRoutesFromSurfaces) always have a component available.
 */
const VIEW_LOADERS = {
  dashboard: () => import("@/views/ControlDashboard.vue"),
  nodes: () => import("@/views/NodesView.vue"),
  jobs: () => import("@/views/JobsView.vue"),
  connectors: () => import("@/views/ConnectorsView.vue"),
  triggers: () => import("@/views/TriggersView.vue"),
  reservations: () => import("@/views/ReservationsView.vue"),
  settings: () => import("@/views/SystemSettings.vue"),
  evaluations: () => import("@/views/EvaluationsView.vue"),
} as const satisfies Record<string, ControlPlaneViewLoader>;

export const ADMIN_ONLY_ROUTE_NAMES = new Set(
  CONTROL_PLANE_SURFACES.filter((surface) => surface.requires_admin).map((surface) => surface.route_name)
);

/** Build a RouteRecordRaw from a surface spec (shared by static and dynamic paths). */
function surfaceToRoute(surface: ControlPlaneSurfaceSpec): RouteRecordRaw {
  if (!(surface.route_name in VIEW_LOADERS)) {
    throw new Error(`Missing control-plane view loader for route: ${surface.route_name}`);
  }
  const component = VIEW_LOADERS[surface.route_name as keyof typeof VIEW_LOADERS];
  return {
    path: surface.route_path,
    name: surface.route_name,
    component,
    meta: {
      title: surface.label,
      requiresAuth: true,
      requiresAdmin: surface.requires_admin,
    },
  } satisfies RouteRecordRaw;
}

/**
 * Returns static control-plane routes built from the bundled JSON fallback.
 * Used at router creation time so the app works offline or before the backend
 * surfaces endpoint is reachable.
 */
export function getControlPlaneRoutes(): RouteRecordRaw[] {
  return CONTROL_PLANE_SURFACES.map(surfaceToRoute);
}

/**
 * Dynamically registers control-plane routes received from the backend
 * /api/v1/console/surfaces endpoint (ADR 0011: backend is the single
 * source of truth).
 *
 * Safe to call multiple times: routes with the same name are skipped
 * if already registered, so static fallback routes are not duplicated.
 *
 * Unknown route_names (no VIEW_LOADERS entry) are silently skipped so
 * that a new backend surface never crashes the SPA.
 */
export function addControlPlaneRoutesFromSurfaces(
  router: Router,
  surfaces: ControlPlaneSurfaceSpec[],
): void {
  for (const surface of surfaces) {
    if (!(surface.route_name in VIEW_LOADERS)) {
      continue; // Unknown view — skip gracefully
    }
    // router.hasRoute() prevents double-registration
    if (!router.hasRoute(surface.route_name)) {
      router.addRoute(surfaceToRoute(surface));
    } else {
      // Update admin-only guard set with runtime truth
      if (surface.requires_admin) {
        ADMIN_ONLY_ROUTE_NAMES.add(surface.route_name);
      } else {
        ADMIN_ONLY_ROUTE_NAMES.delete(surface.route_name);
      }
    }
  }
}

