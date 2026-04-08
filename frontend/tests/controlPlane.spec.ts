import { beforeEach, describe, expect, it } from "vitest";
import { createRouter, createMemoryHistory } from "vue-router";

import {
  addControlPlaneRoutesFromSurfaces,
  ADMIN_ONLY_ROUTE_NAMES,
  getControlPlaneRoutes,
  type ControlPlaneSurfaceSpec,
} from "@/config/controlPlane";

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: getControlPlaneRoutes(),
  });
}

describe("control plane manifest sync", () => {
  beforeEach(() => {
    ADMIN_ONLY_ROUTE_NAMES.clear();
  });

  it("replaces duplicate routes with backend authority", async () => {
    const router = createTestRouter();
    const replacement: ControlPlaneSurfaceSpec = {
      capability_key: "platform.capabilities.query",
      route_name: "dashboard",
      route_path: "/capabilities",
      label: "Capabilities",
      description: "Backend authoritative dashboard",
      endpoint: "/v1/capabilities",
      backend_router: "routes",
      frontend_view: "CapabilitiesView",
      profiles: ["gateway-kernel"],
      requires_admin: true,
    };

    addControlPlaneRoutesFromSurfaces(router, [replacement]);

    const route = router.getRoutes().find((candidate) => candidate.name === "dashboard");
    expect(route?.path).toBe("/capabilities");
    expect(route?.meta.requiresAdmin).toBe(true);
    expect(ADMIN_ONLY_ROUTE_NAMES.has("dashboard")).toBe(true);
  });
});
