import { describe, expect, it, vi } from "vitest";

const logWarnMock = vi.fn();

vi.mock("@/utils/logger", () => ({
  logError: vi.fn(),
  logInfo: vi.fn(),
  logWarn: (...args: unknown[]) => logWarnMock(...args),
}));

vi.mock("@/utils/http", () => ({
  http: {
    get: vi.fn(),
    post: vi.fn(),
  },
  isCircuitOpen: vi.fn(() => false),
}));

import { initWebPush, urlBase64ToUint8Array } from "@/utils/push";

describe("push utils", () => {
  it("decodes url-safe base64 into uint8 array", () => {
    const bytes = urlBase64ToUint8Array("SGVsbG8");
    const text = String.fromCharCode(...bytes);
    expect(text).toBe("Hello");
  });

  it("returns false when browser push APIs are unavailable", async () => {
    const originalServiceWorker = (navigator as Navigator & { serviceWorker?: ServiceWorkerContainer }).serviceWorker;
    const originalPushManager = (window as Window & { PushManager?: unknown }).PushManager;

    Object.defineProperty(navigator, "serviceWorker", { configurable: true, value: undefined });
    Object.defineProperty(window, "PushManager", { configurable: true, value: undefined });

    await expect(initWebPush()).resolves.toBe(false);

    Object.defineProperty(navigator, "serviceWorker", { configurable: true, value: originalServiceWorker });
    Object.defineProperty(window, "PushManager", { configurable: true, value: originalPushManager });
  });
});
