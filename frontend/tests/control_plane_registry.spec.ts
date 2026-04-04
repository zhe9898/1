import { describe, expect, it } from "vitest";
import { CONTROL_PLANE_SURFACES, addControlPlaneRoutesFromSurfaces, getControlPlaneRoutes } from "../src/config/controlPlane";

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

  it("kernel surface set matches backend control_plane.py definition (ADR 0011)", () => {
    // Backend is the single source of truth. This test guards against
    // the frontend JSON silently diverging from backend/core/control_plane.py.
    // Update this list whenever a new surface is added to the backend.
    const expectedKernelRoutes = [
      "dashboard",
      "nodes",
      "jobs",
      "connectors",
      "triggers",
      "reservations",
      "evaluations",
      "settings",
    ];

    const actualKernelRoutes = CONTROL_PLANE_SURFACES
      .filter((surface) => surface.profiles.includes("gateway-kernel"))
      .map((surface) => surface.route_name);

    expect(new Set(actualKernelRoutes)).toEqual(new Set(expectedKernelRoutes));
  });

  it("addControlPlaneRoutesFromSurfaces skips unknown route_names gracefully", () => {
    const fakeRouter = {
      hasRoute: (_name: string) => false,
      addRoute: (_route: unknown) => Symbol(),
    };

    expect(() =>
      addControlPlaneRoutesFromSurfaces(
        fakeRouter as Parameters<typeof addControlPlaneRoutesFromSurfaces>[0],
        [
          {
            capability_key: "gateway.unknown_future_surface",
            route_name: "unknown_future_surface",
            route_path: "/future",
            label: "Future",
            description: "A future surface not yet supported by this frontend version.",
            endpoint: "/v1/future",
            backend_router: "future",
            frontend_view: "FutureView",
            profiles: ["gateway-kernel"],
            requires_admin: false,
          },
        ],
      )
    ).not.toThrow();
  });
});

