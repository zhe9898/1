import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mockHttp = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
}));

vi.mock("@/utils/http", () => ({
  http: mockHttp,
}));

import { useReservationsStore } from "@/stores/reservations";
import { useTriggersStore } from "@/stores/triggers";
import { RESERVATIONS, TRIGGERS } from "@/utils/api";

describe("control-plane stores", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-10T12:00:00Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("normalizes reservation queries, stats, and incremental events", async () => {
    const store = useReservationsStore();
    mockHttp.get
      .mockResolvedValueOnce({
        data: [
          {
            job_id: "job-1",
            node_id: "node-1",
            priority: 90,
          },
        ],
      })
      .mockResolvedValueOnce({
        data: null,
      });

    await store.fetchReservations({
      node_id: "node-1",
      limit: 5,
      status: "",
      tags: ["reservation", ""],
      ignore: [0],
    });
    await store.fetchStats();

    expect(mockHttp.get).toHaveBeenNthCalledWith(1, RESERVATIONS.list, {
      params: {
        node_id: "node-1",
        limit: "5",
        tags: "reservation",
      },
    });
    expect(mockHttp.get).toHaveBeenNthCalledWith(2, RESERVATIONS.stats);
    expect(store.items).toEqual([
      {
        job_id: "job-1",
        node_id: "node-1",
        start_at: "2026-04-10T12:00:00.000Z",
        end_at: "2026-04-10T12:00:00.000Z",
        priority: 90,
        cpu_cores: 0,
        memory_mb: 0,
        gpu_vram_mb: 0,
        slots: 1,
      },
    ]);
    expect(store.stats).toEqual({
      tenant_id: "",
      active_reservations: 0,
      store_backend: "unknown",
      node_counts: {},
      config: {},
    });
    expect(store.nodeCountEntries).toEqual([]);

    store.applyReservationEvent({
      action: "created",
      reservation: {
        job_id: "job-1",
        node_id: "node-1",
        slots: 3,
      },
    });
    store.applyReservationEvent({
      action: "created",
      reservation: {
        job_id: "job-2",
        node_id: "node-2",
      },
    });
    store.applyReservationEvent({
      action: "created",
      reservation: {
        job_id: "job-missing-node",
      },
    });
    store.applyReservationEvent({
      action: "expired",
      reservation: {
        job_id: "job-1",
        node_id: "node-1",
      },
    });

    expect(store.items.map((item) => item.job_id)).toEqual(["job-2"]);
    expect(store.items[0]?.slots).toBe(1);
    expect(store.lastUpdatedAt).toBeGreaterThan(0);
  });

  it("records reservation fetch failures without leaving loading state stuck", async () => {
    const store = useReservationsStore();
    mockHttp.get.mockRejectedValueOnce(new Error("reservations unavailable"));

    await store.fetchReservations();

    expect(store.error).toBe("reservations unavailable");
    expect(store.loading).toBe(false);
  });

  it("normalizes trigger queries, cached deliveries, and control events", async () => {
    const store = useTriggersStore();
    mockHttp.get
      .mockResolvedValueOnce({
        data: [
          {
            trigger_id: "trigger-1",
            status: "active",
          },
        ],
      })
      .mockResolvedValueOnce({
        data: [
          {
            delivery_id: "delivery-1",
            trigger_id: "trigger-1",
            status: "delivered",
          },
        ],
      })
      .mockResolvedValueOnce({
        data: [
          {
            delivery_id: "delivery-2",
            trigger_id: "trigger-1",
            status: "queued",
          },
        ],
      })
      .mockRejectedValueOnce(new Error("deliveries unavailable"));

    await store.fetchTriggers({
      status: "active",
      limit: 10,
      kinds: ["manual", ""],
      empty: "",
    });
    const initialDeliveries = await store.fetchTriggerDeliveries("trigger-1");
    const cachedDeliveries = await store.fetchTriggerDeliveries("trigger-1");
    const refreshedDeliveries = await store.fetchTriggerDeliveries("trigger-1", 10, true);
    const failedDeliveries = await store.fetchTriggerDeliveries("trigger-2");

    expect(mockHttp.get).toHaveBeenNthCalledWith(1, TRIGGERS.list, {
      params: {
        status: "active",
        limit: "10",
        kinds: "manual",
      },
    });
    expect(mockHttp.get).toHaveBeenNthCalledWith(2, TRIGGERS.deliveries("trigger-1"), {
      params: { limit: "50" },
    });
    expect(mockHttp.get).toHaveBeenNthCalledWith(3, TRIGGERS.deliveries("trigger-1"), {
      params: { limit: "10" },
    });
    expect(mockHttp.get).toHaveBeenNthCalledWith(4, TRIGGERS.deliveries("trigger-2"), {
      params: { limit: "50" },
    });
    expect(initialDeliveries).toHaveLength(1);
    expect(cachedDeliveries).toEqual(initialDeliveries);
    expect(refreshedDeliveries[0]?.delivery_id).toBe("delivery-2");
    expect(failedDeliveries).toEqual([]);
    expect(store.error).toBe("deliveries unavailable");
    expect(store.loading).toBe(false);
    expect(store.deliveriesLoading["trigger-2"]).toBe(false);

    store.applyTriggerEvent({
      action: "fired",
      trigger: {
        trigger_id: "trigger-1",
        name: "Trigger One",
      },
      delivery: {
        delivery_id: "delivery-3",
        status: "delivered",
      },
    });
    store.applyTriggerEvent({
      action: "upserted",
      delivery: {
        trigger_id: "trigger-1",
      },
    });

    expect(store.items[0]?.name).toBe("Trigger One");
    expect(store.deliveriesByTrigger["trigger-1"]?.[0]?.delivery_id).toBe("delivery-3");
  });

  it("routes trigger activate and pause actions and clears action loading on failure", async () => {
    const store = useTriggersStore();
    mockHttp.post
      .mockResolvedValueOnce({
        data: {
          trigger_id: "trigger-1",
          status: "active",
        },
      })
      .mockResolvedValueOnce({
        data: {
          trigger_id: "trigger-1",
          status: "paused",
        },
      })
      .mockRejectedValueOnce(new Error("pause failed"));

    const activated = await store.runStatusAction("trigger-1", "activate", "resume");
    const paused = await store.runStatusAction("trigger-1", "pause");
    const failed = await store.runStatusAction("trigger-2", "pause");

    expect(mockHttp.post).toHaveBeenNthCalledWith(1, TRIGGERS.activate("trigger-1"), { reason: "resume" });
    expect(mockHttp.post).toHaveBeenNthCalledWith(2, TRIGGERS.pause("trigger-1"), {});
    expect(mockHttp.post).toHaveBeenNthCalledWith(3, TRIGGERS.pause("trigger-2"), {});
    expect(activated?.status).toBe("active");
    expect(paused?.status).toBe("paused");
    expect(failed).toBeNull();
    expect(store.items[0]?.status).toBe("paused");
    expect(store.error).toBe("pause failed");
    expect(store.actionLoading["trigger-2"]).toBe(false);
  });
});
