import type { RouteRecordRaw } from "vue-router";
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

const VIEW_LOADERS = {
  dashboard: () => import("@/views/ControlDashboard.vue"),
  nodes: () => import("@/views/NodesView.vue"),
  jobs: () => import("@/views/JobsView.vue"),
  connectors: () => import("@/views/ConnectorsView.vue"),
  settings: () => import("@/views/SystemSettings.vue"),
  evaluations: () => import("@/views/EvaluationsView.vue"),
} as const satisfies Record<string, ControlPlaneViewLoader>;

export const ADMIN_ONLY_ROUTE_NAMES = new Set(
  CONTROL_PLANE_SURFACES.filter((surface) => surface.requires_admin).map((surface) => surface.route_name)
);

export function getControlPlaneRoutes(): RouteRecordRaw[] {
  return CONTROL_PLANE_SURFACES.map((surface) => {
    if (!(surface.route_name in VIEW_LOADERS)) {
      throw new Error(`Missing control-plane view loader for route ${surface.route_name}`);
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
  });
}
