import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Capabilities } from "@/types/capability";

const putMock = vi.fn();
const getMock = vi.fn();

vi.mock("@/db", () => ({
  CAPABILITIES_ROW_ID: "capabilities",
  db: {
    capabilities: {
      put: (...args: unknown[]) => putMock(...args),
      get: (...args: unknown[]) => getMock(...args),
    },
  },
}));

import { loadCapabilities, saveCapabilities } from "@/services/offlineStorage";

describe("offlineStorage", () => {
  beforeEach(() => {
    putMock.mockReset();
    getMock.mockReset();
  });

  it("saves capabilities payload", async () => {
    const payload: Capabilities = {
      chat: { status: "online", enabled: true },
    };
    putMock.mockResolvedValue(undefined);

    await saveCapabilities(payload);

    expect(putMock).toHaveBeenCalledOnce();
    const row = putMock.mock.calls[0][0] as { id: string; data: Capabilities; lastUpdated: number };
    expect(row.id).toBe("capabilities");
    expect(row.data).toEqual(payload);
    expect(typeof row.lastUpdated).toBe("number");
  });

  it("returns null when cache is absent", async () => {
    getMock.mockResolvedValue(null);

    await expect(loadCapabilities()).resolves.toBeNull();
  });

  it("returns fresh cache with stale flag false", async () => {
    const now = Date.now();
    getMock.mockResolvedValue({
      id: "capabilities",
      data: { jobs: { status: "online", enabled: true } },
      lastUpdated: now,
    });

    const result = await loadCapabilities();

    expect(result).not.toBeNull();
    expect(result?.isStale).toBe(false);
    expect(result?.data.jobs?.status).toBe("online");
  });

  it("marks cache stale when ttl is exceeded", async () => {
    const twentyFiveHoursAgo = Date.now() - 25 * 60 * 60 * 1000;
    getMock.mockResolvedValue({
      id: "capabilities",
      data: { jobs: { status: "offline", enabled: false } },
      lastUpdated: twentyFiveHoursAgo,
    });

    const result = await loadCapabilities();

    expect(result).not.toBeNull();
    expect(result?.isStale).toBe(true);
    expect(result?.data.jobs?.status).toBe("offline");
  });
});
