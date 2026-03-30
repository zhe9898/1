import { describe, expect, it } from "vitest";
import { CONTROL_PLANE_SURFACES, getControlPlaneRoutes } from "../src/config/controlPlane";

describe("control-plane registry", () => {
  it("builds router entries for every declared surface", () => {
    const routes = getControlPlaneRoutes();
    expect(routes).toHaveLength(CONTROL_PLANE_SURFACES.length);

    const routeNames = new Set(routes.map((route) => String(route.name)));
    const routePaths = new Set(routes.map((route) => route.path));

    for (const surface of CONTROL_PLANE_SURFACES) {
      expect(routeNames.has(surface.route_name)).toBe(true);
      expect(routePaths.has(surface.route_path)).toBe(true);
      const route = routes.find((item) => String(item.name) === surface.route_name);
      expect(route).toBeDefined();
      expect(typeof route?.component).toBe("function");
      expect(route?.meta?.requiresAuth).toBe(true);
      expect(route?.meta?.requiresAdmin).toBe(surface.requires_admin);
    }
  });

  it("keeps the kernel surface set closed", () => {
    const kernelRoutes = CONTROL_PLANE_SURFACES
      .filter((surface) => surface.profiles.includes("gateway-kernel"))
      .map((surface) => surface.route_name);

    expect(kernelRoutes).toEqual(["dashboard", "nodes", "jobs", "connectors", "settings"]);
  });
});
