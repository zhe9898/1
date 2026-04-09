import { beforeEach, describe, expect, it, vi } from "vitest";
import { createMemoryHistory, createRouter } from "vue-router";

import { getControlPlaneRoutes, type ControlPlaneSurfaceSpec } from "@/config/controlPlane";
import { resetControlPlaneSurfaceSyncState, syncControlPlaneSurfacesForIdentity } from "@/router";

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: getControlPlaneRoutes(),
  });
}

function replacementSurface(path: string): ControlPlaneSurfaceSpec {
  return {
    capability_key: "platform.capabilities.query",
    route_name: "dashboard",
    route_path: path,
    label: "Capabilities",
    description: "Backend authoritative dashboard",
    endpoint: "/v1/capabilities",
    backend_router: "routes",
    frontend_view: "CapabilitiesView",
    profiles: ["gateway-kernel"],
    requires_admin: true,
  };
}

describe("control plane router surface sync state", () => {
  beforeEach(() => {
    resetControlPlaneSurfaceSyncState();
  });

  it("retries the backend manifest fetch after a same-identity failure", async () => {
    const router = createTestRouter();
    const fetchSurfaces = vi
      .fn<() => Promise<ControlPlaneSurfaceSpec[]>>()
      .mockRejectedValueOnce(new Error("network"))
      .mockResolvedValueOnce([replacementSurface("/capabilities")]);

    await expect(syncControlPlaneSurfacesForIdentity(router, "user-1:admin", fetchSurfaces)).resolves.toBe(false);
    await expect(syncControlPlaneSurfacesForIdentity(router, "user-1:admin", fetchSurfaces)).resolves.toBe(true);

    expect(fetchSurfaces).toHaveBeenCalledTimes(2);
    expect(router.getRoutes().find((candidate) => candidate.name === "dashboard")?.path).toBe("/capabilities");
  });

  it("clears the loaded identity on logout so the same identity refreshes on re-login", async () => {
    const router = createTestRouter();
    const fetchSurfaces = vi
      .fn<() => Promise<ControlPlaneSurfaceSpec[]>>()
      .mockResolvedValueOnce([replacementSurface("/capabilities-a")])
      .mockResolvedValueOnce([replacementSurface("/capabilities-b")]);

    await expect(syncControlPlaneSurfacesForIdentity(router, "user-1:admin", fetchSurfaces)).resolves.toBe(true);
    await expect(syncControlPlaneSurfacesForIdentity(router, null, fetchSurfaces)).resolves.toBe(false);
    await expect(syncControlPlaneSurfacesForIdentity(router, "user-1:admin", fetchSurfaces)).resolves.toBe(true);

    expect(fetchSurfaces).toHaveBeenCalledTimes(2);
    expect(router.getRoutes().find((candidate) => candidate.name === "dashboard")?.path).toBe("/capabilities-b");
  });
});
