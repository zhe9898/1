import { mount } from "@vue/test-utils";
import { defineComponent, h } from "vue";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BROWSER_REALTIME_CHANNELS } from "@/types/sse";

const createSSEMock = vi.fn<() => () => void>();
const requestPersistentStorageMock = vi.fn(async () => true);
const initWebPushMock = vi.fn(async () => undefined);

vi.mock("@/utils/sse", () => ({
  createSSE: (...args: unknown[]) => createSSEMock(...args),
}));

vi.mock("@/utils/persist", () => ({
  requestPersistentStorage: (...args: unknown[]) => requestPersistentStorageMock(...args),
}));

vi.mock("@/utils/push", () => ({
  initWebPush: (...args: unknown[]) => initWebPushMock(...args),
}));

import { useAppRuntime } from "@/composables/useAppRuntime";

async function flushAsyncWork(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
}

describe("useAppRuntime realtime recovery", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    createSSEMock.mockReset();
    requestPersistentStorageMock.mockClear();
    initWebPushMock.mockClear();
  });

  it("wires fallback/recovery callbacks and restarts SSE on browser online", async () => {
    const closeFirst = vi.fn();
    const closeSecond = vi.fn();
    createSSEMock.mockReturnValueOnce(closeFirst).mockReturnValueOnce(closeSecond);

    const capsStore = {
      isOffline: false,
      fetchCapabilities: vi.fn(async () => undefined),
      syncOnReconnect: vi.fn(async () => undefined),
      updateHardware: vi.fn(),
    };
    const consoleStore = {
      refresh: vi.fn(async () => undefined),
    };
    const switchStore = {
      loadCached: vi.fn(async () => undefined),
      updateFromEvent: vi.fn(),
    };
    const eventsStore = {
      push: vi.fn(),
    };
    const auth = {
      isAdmin: true,
      isAuthenticated: true,
      identityKey: "user-1:admin",
    };

    const Harness = defineComponent({
      setup() {
        useAppRuntime({
          auth,
          capsStore,
          consoleStore,
          switchStore,
          eventsStore,
        });
        return () => h("div");
      },
    });

    const wrapper = mount(Harness);
    await flushAsyncWork();

    expect(createSSEMock).toHaveBeenCalledTimes(1);
    expect(createSSEMock.mock.calls[0]?.[2]).toEqual(BROWSER_REALTIME_CHANNELS);
    const options = createSSEMock.mock.calls[0]?.[3] as { onFallbackOffline?: () => void; onRecovered?: () => void };
    options.onFallbackOffline?.();
    expect(capsStore.isOffline).toBe(true);

    options.onRecovered?.();
    await flushAsyncWork();
    expect(capsStore.isOffline).toBe(false);
    expect(capsStore.syncOnReconnect).toHaveBeenCalledTimes(1);

    window.dispatchEvent(new Event("online"));
    await flushAsyncWork();

    expect(closeFirst).toHaveBeenCalledTimes(1);
    expect(createSSEMock).toHaveBeenCalledTimes(2);
    expect(capsStore.syncOnReconnect).toHaveBeenCalledTimes(2);

    wrapper.unmount();
    expect(closeSecond).toHaveBeenCalledTimes(1);
  });
});
