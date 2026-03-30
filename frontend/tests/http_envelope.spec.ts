// @vitest-environment jsdom
import { describe, expect, it } from "vitest";

import { unwrapSuccessEnvelope } from "../src/utils/http";

describe("unwrapSuccessEnvelope", () => {
  it("unwraps nested success envelopes deeper than two levels", () => {
    const payload = {
      code: "ZEN-OK-0",
      data: {
        code: "ZEN-OK-0",
        data: {
          code: "ZEN-OK-0",
          data: {
            code: "ZEN-OK-0",
            data: { value: 42 },
          },
        },
      },
    };

    expect(unwrapSuccessEnvelope(payload)).toEqual({ value: 42 });
  });

  it("stops at the first non-envelope payload", () => {
    const payload = {
      code: "ZEN-OK-0",
      data: {
        code: "ZEN-OK-0",
        data: { code: "OTHER", data: { value: 1 } },
      },
    };

    expect(unwrapSuccessEnvelope(payload)).toEqual({ code: "OTHER", data: { value: 1 } });
  });
});
