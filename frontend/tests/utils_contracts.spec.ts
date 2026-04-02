import { describe, expect, it } from "vitest";

import { CONTROL_PLANE_SURFACES } from "../src/constants/controlPlane";
import { apiErrorMessage, errorMessage, extractAxiosError, isAxiosError } from "../src/utils/errorMessage";
import { getRequestId } from "../src/utils/requestId";
import {
  badgeClassFromTone,
  normalizeStatusView,
  solidBadgeClassFromTone,
  surfaceClassFromTone,
} from "../src/utils/statusView";

describe("frontend utility contracts", () => {
  it("keeps control-plane surfaces aligned with the frontend shell", () => {
    expect(CONTROL_PLANE_SURFACES).toHaveLength(5);
    expect(CONTROL_PLANE_SURFACES.map((surface) => surface.routeName)).toEqual([
      "dashboard",
      "nodes",
      "jobs",
      "connectors",
      "settings",
    ]);
    expect(CONTROL_PLANE_SURFACES.map((surface) => surface.routePath)).toEqual([
      "/",
      "/nodes",
      "/jobs",
      "/connectors",
      "/settings",
    ]);
    expect(CONTROL_PLANE_SURFACES.at(-1)?.adminOnly).toBe(true);
    for (const surface of CONTROL_PLANE_SURFACES) {
      expect(surface.label.length).toBeGreaterThan(0);
      expect(surface.title.length).toBeGreaterThan(0);
      expect(surface.endpoint.startsWith("/v1/")).toBe(true);
    }
  });

  it("normalizes status views and falls back on invalid payloads", () => {
    expect(normalizeStatusView({ key: "online", label: "Online", tone: "success" })).toEqual({
      key: "online",
      label: "Online",
      tone: "success",
    });

    expect(normalizeStatusView({ key: 1, label: null, tone: ["bad"] }, "fallback", "Fallback", "warning")).toEqual({
      key: "fallback",
      label: "Fallback",
      tone: "warning",
    });

    expect(normalizeStatusView(null)).toEqual({
      key: "unknown",
      label: "Unknown",
      tone: "neutral",
    });
  });

  it("maps tones to badge and surface classes", () => {
    expect(badgeClassFromTone("success")).toBe("badge-success");
    expect(badgeClassFromTone("warning")).toBe("badge-warning");
    expect(badgeClassFromTone("danger")).toBe("badge-error");
    expect(badgeClassFromTone("info")).toBe("badge-info");
    expect(badgeClassFromTone("other")).toBe("badge-ghost");

    expect(solidBadgeClassFromTone("success")).toContain("bg-emerald-600");
    expect(solidBadgeClassFromTone("warning")).toContain("bg-amber-600");
    expect(solidBadgeClassFromTone("danger")).toContain("bg-rose-600");
    expect(solidBadgeClassFromTone("info")).toContain("bg-sky-600");
    expect(solidBadgeClassFromTone("other")).toContain("bg-base-300");

    expect(surfaceClassFromTone("success")).toContain("border-emerald-200");
    expect(surfaceClassFromTone("warning")).toContain("border-amber-200");
    expect(surfaceClassFromTone("danger")).toContain("border-rose-200");
    expect(surfaceClassFromTone("info")).toContain("border-sky-200");
    expect(surfaceClassFromTone("other")).toContain("border-base-300");
  });

  it("extracts readable error messages from generic and axios-like payloads", () => {
    expect(errorMessage(new Error("boom"))).toBe("boom");
    expect(errorMessage({ message: "from-object" })).toBe("from-object");
    expect(errorMessage("plain")).toBe("plain");

    expect(isAxiosError({ isAxiosError: true, response: { status: 500 } })).toBe(true);
    expect(isAxiosError(new Error("nope"))).toBe(false);

    expect(apiErrorMessage({ message: "backend-message" }, "fallback")).toBe("backend-message");
    expect(apiErrorMessage({ detail: "backend-detail" }, "fallback")).toBe("backend-detail");
    expect(apiErrorMessage({ detail: { message: "nested-detail" } }, "fallback")).toBe("nested-detail");
    expect(apiErrorMessage({}, "fallback")).toBe("fallback");

    expect(
      extractAxiosError(
        { isAxiosError: true, response: { status: 401, data: { message: "ignored" } }, message: "raw" },
        "fallback"
      )
    ).toBe("会话已过期，请重新登录");
    expect(
      extractAxiosError(
        { isAxiosError: true, response: { status: 403, data: { message: "ignored" } }, message: "raw" },
        "fallback"
      )
    ).toBe("权限不足，无法执行此操作");
    expect(
      extractAxiosError(
        { isAxiosError: true, response: { status: 429, data: { message: "ignored" } }, message: "raw" },
        "fallback"
      )
    ).toBe("操作过于频繁，请稍后重试");
    expect(
      extractAxiosError(
        { isAxiosError: true, response: { status: 500, data: { detail: { message: "server-detail" } } }, message: "raw" },
        "fallback"
      )
    ).toBe("server-detail");
    expect(extractAxiosError({ isAxiosError: true, message: "raw" }, "fallback")).toBe("raw");
    expect(extractAxiosError(new Error("plain-error"), "fallback")).toBe("plain-error");
  });

  it("generates request ids as UUID strings", () => {
    const requestId = getRequestId();
    expect(requestId).toMatch(/^[0-9a-f-]{36}$/i);
  });
});
