import { beforeEach, describe, expect, it, vi } from "vitest";

const logInfoMock = vi.fn();
const logWarnMock = vi.fn();

vi.mock("@/utils/logger", () => ({
  logInfo: (...args: unknown[]) => logInfoMock(...args),
  logWarn: (...args: unknown[]) => logWarnMock(...args),
}));

import { isPersisted, requestPersistentStorage } from "@/utils/persist";

describe("persist utils", () => {
  beforeEach(() => {
    logInfoMock.mockReset();
    logWarnMock.mockReset();
  });

  it("returns false when storage API is unavailable", async () => {
    Object.defineProperty(globalThis.navigator, "storage", {
      configurable: true,
      value: undefined,
    });

    await expect(requestPersistentStorage()).resolves.toBe(false);
    await expect(isPersisted()).resolves.toBe(false);
  });

  it("logs info when persistent storage is granted", async () => {
    Object.defineProperty(globalThis.navigator, "storage", {
      configurable: true,
      value: {
        persist: vi.fn().mockResolvedValue(true),
        persisted: vi.fn().mockResolvedValue(true),
      },
    });

    await expect(requestPersistentStorage()).resolves.toBe(true);
    await expect(isPersisted()).resolves.toBe(true);
    expect(logInfoMock).toHaveBeenCalled();
    expect(logWarnMock).not.toHaveBeenCalled();
  });

  it("logs warning when browser denies persistence", async () => {
    Object.defineProperty(globalThis.navigator, "storage", {
      configurable: true,
      value: {
        persist: vi.fn().mockResolvedValue(false),
        persisted: vi.fn().mockResolvedValue(false),
      },
    });

    await expect(requestPersistentStorage()).resolves.toBe(false);
    expect(logWarnMock).toHaveBeenCalled();
  });

  it("swallows persist errors and returns false", async () => {
    Object.defineProperty(globalThis.navigator, "storage", {
      configurable: true,
      value: {
        persist: vi.fn().mockRejectedValue(new Error("blocked")),
        persisted: vi.fn().mockRejectedValue(new Error("blocked")),
      },
    });

    await expect(requestPersistentStorage()).resolves.toBe(false);
    await expect(isPersisted()).resolves.toBe(false);
  });
});
