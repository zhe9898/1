// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import SystemSettings from "../src/views/SystemSettings.vue";
import { SETTINGS } from "../src/utils/api";

const settingsMocks = vi.hoisted(() => ({
  httpGet: vi.fn(),
  httpPut: vi.fn(),
  httpPost: vi.fn(),
  authStore: { isAdmin: true },
}));

vi.mock("@/utils/http", () => ({
  http: {
    get: (...args: unknown[]) => settingsMocks.httpGet(...args),
    put: (...args: unknown[]) => settingsMocks.httpPut(...args),
    post: (...args: unknown[]) => settingsMocks.httpPost(...args),
  },
}));

vi.mock("@/stores/auth", () => ({
  useAuthStore: () => settingsMocks.authStore,
}));

beforeEach(() => {
  settingsMocks.httpGet.mockReset();
  settingsMocks.httpPut.mockReset();
  settingsMocks.httpPost.mockReset();
  settingsMocks.authStore = { isAdmin: true };

  settingsMocks.httpGet.mockImplementation(async (url: string) => {
    if (url === SETTINGS.schema) {
      return {
        data: {
          product: "ZEN70 Gateway Kernel",
          profile: "gateway-kernel",
          runtime_profile: "gateway-kernel",
          sections: [
            {
              id: "kernel",
              label: "Kernel Profile",
              description: "Backend-driven runtime contract",
              fields: [
                {
                  key: "profile",
                  label: "Profile",
                  value: "gateway-kernel",
                  description: "Current runtime profile",
                  input: "readonly",
                  editable: false,
                  save_path: null,
                  placeholder: null,
                },
              ],
            },
          ],
        },
      };
    }
    if (url === SETTINGS.flags) {
      return {
        data: {
          data: [
            { key: "jobs.retry", enabled: true, description: "Retry failed jobs", category: "scheduler" },
          ],
        },
      };
    }
    if (url === SETTINGS.aiModels) {
      return {
        data: {
          by_provider: {
            openai: [
              { id: "gpt-5.4", name: "GPT-5.4", provider: "openai", capabilities: ["chat"] },
            ],
          },
        },
      };
    }
    if (url === SETTINGS.system) {
      return {
        data: {
          version: "3.4.1",
          python: "3.12",
          os: "Windows",
          architecture: "x64",
          gpu: "RTX",
          disk: { free_gb: 128, usage_pct: 35 },
          ai_models: { chat: "openai:gpt-5.4" },
          ai_providers: { openai: { status: "online" } },
        },
      };
    }
    throw new Error(`Unexpected GET ${url}`);
  });
});

describe("SystemSettings", () => {
  it("renders backend-driven schema and loads settings surfaces on mount", async () => {
    const wrapper = mount(SystemSettings);
    await flushPromises();

    expect(settingsMocks.httpGet).toHaveBeenCalledWith(SETTINGS.schema);
    expect(settingsMocks.httpGet).toHaveBeenCalledWith(SETTINGS.flags);
    expect(settingsMocks.httpGet).toHaveBeenCalledWith(SETTINGS.aiModels);
    expect(settingsMocks.httpGet).toHaveBeenCalledWith(SETTINGS.system);
    expect(wrapper.text()).toContain("Settings");
    expect(wrapper.text()).toContain("Kernel Profile");
    expect(wrapper.text()).toContain("Profile");

    const flagsTab = wrapper.findAll("button").find((button) => button.text() === "Flags");
    expect(flagsTab).toBeTruthy();
    await flagsTab!.trigger("click");
    expect(wrapper.text()).toContain("jobs.retry");
    expect(wrapper.text()).toContain("Retry failed jobs");
  });
});
