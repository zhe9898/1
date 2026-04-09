import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/utils/http", () => ({
  http: {
    post: vi.fn(() => Promise.resolve({ data: { ok: true } })),
  },
}));

import { createSSE } from "@/utils/sse";

function makeSSEStream(frames: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const frame of frames) {
        controller.enqueue(encoder.encode(frame));
      }
      controller.close();
    },
  });
}

function makeOpenSSEStream(frames: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const frame of frames) {
        controller.enqueue(encoder.encode(frame));
      }
    },
  });
}

async function flushAsyncWork(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
}

describe("createSSE", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("opens the event stream with cookie credentials and no Authorization header", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(makeSSEStream([": heartbeat\n\n"]), {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const close = createSSE(
      "https://api.example.test/v1/events",
      () => undefined,
      ["job:events"],
    );

    await flushAsyncWork();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = init.headers as Record<string, string>;
    expect(headers.Authorization).toBeUndefined();
    expect(headers.Accept).toBe("text/event-stream");
    expect(init.credentials).toBe("include");

    close();
  });

  it("parses typed SSE frames and forwards event payloads", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        makeSSEStream([
          "event: job:events\n",
          'data: {"job_id":"job-1","status":"done"}\n\n',
        ]),
        {
          status: 200,
          headers: { "Content-Type": "text/event-stream" },
        },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const onEvent = vi.fn();

    const close = createSSE(
      "https://api.example.test/v1/events",
      onEvent,
      ["job:events"],
    );

    await flushAsyncWork();

    expect(onEvent).toHaveBeenCalledWith({
      type: "job:events",
      data: { job_id: "job-1", status: "done" },
    });

    close();
  });

  it("can open the event stream using cookie credentials without a bearer token", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(makeSSEStream([": heartbeat\n\n"]), {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const close = createSSE(
      "https://api.example.test/v1/events",
      () => undefined,
      ["job:events"],
    );

    await flushAsyncWork();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = init.headers as Record<string, string>;
    expect(headers.Authorization).toBeUndefined();
    expect(init.credentials).toBe("include");

    close();
  });

  it("keeps probing after fallback exhaustion and reports recovery", async () => {
    vi.useFakeTimers();
    let attempts = 0;
    const fetchMock = vi.fn().mockImplementation(() => {
      attempts += 1;
      if (attempts <= 11) {
        return Promise.reject(new Error(`down-${String(attempts - 1)}`));
      }
      return Promise.resolve(
        new Response(
          makeOpenSSEStream([
            "event: connected\n",
            'data: {"connection_id":"conn-1"}\n\n',
          ]),
          {
            status: 200,
            headers: { "Content-Type": "text/event-stream" },
          },
        ),
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    const onFallbackOffline = vi.fn();
    const onRecovered = vi.fn();

    const close = createSSE(
      "https://api.example.test/v1/events",
      () => undefined,
      ["job:events"],
      { onFallbackOffline, onRecovered },
    );

    for (let i = 0; i < 12; i += 1) {
      await flushAsyncWork();
      await vi.runOnlyPendingTimersAsync();
    }
    await flushAsyncWork();

    expect(onFallbackOffline).toHaveBeenCalledTimes(1);
    expect(onRecovered).toHaveBeenCalledTimes(1);

    close();
  });
});
