/**
 * @description Authenticated SSE client with exponential backoff, heartbeat ping,
 * and visibility-aware connection lifecycle.
 */
import type {
  HardwareEvent,
  SSEEvent,
  SwitchEvent,
  NodeControlEvent,
  JobControlEvent,
  ConnectorControlEvent,
} from "@/types/sse";
import { http } from "@/utils/http";
import { SSE } from "@/utils/api";
import { logError, logWarn } from "@/utils/logger";

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 30000;
const MAX_RETRIES = 10;
const PING_INTERVAL_MS = 30_000;
const MAX_PING_FAILURES = 3;

type SSEChannel =
  | "hardware:events"
  | "switch:events"
  | "node:events"
  | "job:events"
  | "connector:events";

interface ParsedFrame {
  event: string;
  data: string;
}

function parseData(data: string): unknown {
  if (!data) {
    return null;
  }
  try {
    return JSON.parse(data);
  } catch {
    return null;
  }
}

function parseFrame(frame: string): ParsedFrame | null {
  if (!frame.trim()) {
    return null;
  }
  const lines = frame.split("\n");
  let event = "message";
  const dataLines: string[] = [];

  for (const rawLine of lines) {
    const line = rawLine.trimEnd();
    if (!line || line.startsWith(":")) {
      continue;
    }
    if (line.startsWith("event:")) {
      event = line.slice("event:".length).trim();
      continue;
    }
    if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trimStart());
    }
  }

  return {
    event,
    data: dataLines.join("\n"),
  };
}

export interface SSEOptions {
  onFallbackOffline?: () => void;
}

export function createSSE(
  url: string,
  onEvent: (ev: SSEEvent) => void,
  channels: readonly SSEChannel[] = [
    "hardware:events",
    "switch:events",
    "node:events",
    "job:events",
    "connector:events",
  ],
  options: SSEOptions = {},
): () => void {
  let closed = false;
  let attempt = 0;
  let currentClientToken: string | null = null;
  let pingTimer: ReturnType<typeof setInterval> | null = null;
  let pingFailures = 0;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let abortController: AbortController | null = null;
  let suppressReconnect = false;

  function stopPing(): void {
    if (pingTimer !== null) {
      clearInterval(pingTimer);
      pingTimer = null;
    }
    pingFailures = 0;
  }

  function stopReconnectTimer(): void {
    if (reconnectTimer !== null) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  }

  function abortActiveStream(noReconnect: boolean): void {
    if (noReconnect) {
      suppressReconnect = true;
    }
    abortController?.abort();
    abortController = null;
  }

  function scheduleReconnect(): void {
    if (closed) {
      return;
    }
    if (attempt >= MAX_RETRIES) {
      options.onFallbackOffline?.();
      return;
    }
    const delay = Math.min(RECONNECT_BASE_MS * 2 ** attempt, RECONNECT_MAX_MS);
    attempt += 1;
    stopReconnectTimer();
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      void connect();
    }, delay);
  }

  function startPing(): void {
    stopPing();
    if (!currentClientToken) {
      return;
    }

    pingTimer = setInterval(() => {
      if (closed || !currentClientToken) {
        stopPing();
        return;
      }
      http.post(SSE.ping, { connection_id: currentClientToken }).then(() => {
        pingFailures = 0;
      }).catch((err: unknown) => {
        pingFailures += 1;
        logWarn(`[ZEN70 SSE] Ping failed (${String(pingFailures)}/${String(MAX_PING_FAILURES)})`, err);
        if (pingFailures >= MAX_PING_FAILURES) {
          logWarn("[ZEN70 SSE] Ping suspended after consecutive failures");
          stopPing();
        }
      });
    }, PING_INTERVAL_MS);
  }

  function handleParsedFrame(parsed: ParsedFrame): void {
    if (parsed.event === "connected") {
      attempt = 0;
      startPing();
      return;
    }
    if (!channels.includes(parsed.event as SSEChannel)) {
      return;
    }
    try {
      onEvent({
        type: parsed.event as SSEChannel,
        data: parseData(parsed.data) as
          | HardwareEvent
          | SwitchEvent
          | NodeControlEvent
          | JobControlEvent
          | ConnectorControlEvent,
      });
    } catch (err: unknown) {
      logError(`[ZEN70] SSE parse ${parsed.event}`, err);
    }
  }

  async function readStreamBody(response: Response): Promise<void> {
    if (!response.body) {
      throw new Error("SSE response missing body");
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    for (;;) {
      const chunk = await reader.read();
      if (chunk.done) {
        break;
      }
      buffer += decoder.decode(chunk.value, { stream: true }).replace(/\r\n/g, "\n").replace(/\r/g, "\n");
      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const parsed = parseFrame(frame);
        if (parsed) {
          handleParsedFrame(parsed);
        }
        boundary = buffer.indexOf("\n\n");
      }
    }

    const tail = decoder.decode().replace(/\r\n/g, "\n").replace(/\r/g, "\n");
    if (tail) {
      buffer += tail;
    }
    const parsedTail = parseFrame(buffer);
    if (parsedTail) {
      handleParsedFrame(parsedTail);
    }
  }

  async function connect(): Promise<void> {
    if (closed || document.visibilityState === "hidden") {
      return;
    }

    stopReconnectTimer();
    currentClientToken = crypto.randomUUID();
    const sep = url.includes("?") ? "&" : "?";
    const sseUrl = `${url}${sep}client_token=${currentClientToken}`;

    abortController = new AbortController();
    suppressReconnect = false;

    try {
      const headers: Record<string, string> = {
        Accept: "text/event-stream",
        "Cache-Control": "no-cache",
      };
      // eslint-disable-next-line zen70/no-bare-fetch -- SSE requires a streaming fetch with custom auth headers.
      const response = await fetch(sseUrl, {
        method: "GET",
        credentials: "include",
        headers,
        signal: abortController.signal,
      });

      if (!response.ok) {
        throw new Error(`SSE HTTP ${String(response.status)}`);
      }

      await readStreamBody(response);
      throw new Error("SSE stream closed");
    } catch (err: unknown) {
      const aborted = err instanceof DOMException && err.name === "AbortError";
      // eslint-disable-next-line @typescript-eslint/no-unnecessary-condition -- runtime flags are mutated by visibility/close handlers between awaits.
      const shouldSuppressReconnect = aborted || closed || suppressReconnect;
      suppressReconnect = false;
      if (shouldSuppressReconnect) {
        return;
      }
      stopPing();
      logWarn("[ZEN70 SSE] Stream reconnect scheduled", err);
      scheduleReconnect();
    }
  }

  function closeConnectionOnly(): void {
    stopPing();
    stopReconnectTimer();
    abortActiveStream(true);
  }

  function onVisibilityChange(): void {
    if (document.visibilityState === "hidden") {
      closeConnectionOnly();
      return;
    }
    if (!closed) {
      attempt = 0;
      void connect();
    }
  }

  document.addEventListener("visibilitychange", onVisibilityChange);
  void connect();

  return () => {
    closed = true;
    document.removeEventListener("visibilitychange", onVisibilityChange);
    stopPing();
    stopReconnectTimer();
    abortActiveStream(true);
  };
}
