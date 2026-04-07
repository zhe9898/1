/**
 */
import axios, { type AxiosInstance, type AxiosError, type InternalAxiosRequestConfig } from "axios";
import { getRequestId } from "./requestId";
import { useAuthStore } from "@/stores/auth";
import { AGENT } from "@/utils/api";
import { decodePayload, isWellFormedJwt } from "@/utils/jwt";
import { logError, logInfo, logWarn } from "@/utils/logger";

const baseURL = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "/api";

// Circuit breaker state.
let _circuitOpen = false;
let circuitBreakTimer: ReturnType<typeof setTimeout> | null = null;
const CIRCUIT_BREAKER_TIMEOUT = 15000; // 15 seconds
const SUCCESS_ENVELOPE_CODE = "ZEN-OK-0";
const SUCCESS_ENVELOPE_MAX_DEPTH = 8;

export function isCircuitOpen(): boolean {
  return _circuitOpen;
}

export function resetCircuit(): void {
  _circuitOpen = false;
  if (circuitBreakTimer) {
    clearTimeout(circuitBreakTimer);
    circuitBreakTimer = null;
  }
  logInfo("[ZEN70 Circuit Breaker] MANUALLY RESET");
}

export function unwrapSuccessEnvelope(body: unknown, maxDepth = SUCCESS_ENVELOPE_MAX_DEPTH): unknown {
  let current = body;
  for (let depth = 0; depth < maxDepth; depth++) {
    if (!current || typeof current !== "object") {
      break;
    }
    const candidate = current as Record<string, unknown>;
    if (
      candidate.code === SUCCESS_ENVELOPE_CODE &&
      Object.prototype.hasOwnProperty.call(candidate, "data")
    ) {
      current = candidate.data;
      continue;
    }
    break;
  }
  return current;
}

function isAcceptableRefreshToken(value: unknown): value is string {
  return typeof value === "string" && isWellFormedJwt(value) && decodePayload(value) !== null;
}

export const http: AxiosInstance = axios.create({
  baseURL,
  timeout: 10000,
  withCredentials: true,
});

http.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  // Drop requests locally while the circuit is open to reduce backend pressure.
  if (isCircuitOpen()) {
    return Promise.reject(new Error("Circuit Breaker OPEN: request dropped to protect backend."));
  }

  config.headers["X-Request-ID"] = getRequestId();
  // CSRF mitigation: custom request headers cannot be set by cross-origin forms or
  // img/script tags, so this header acts as a lightweight CSRF token for same-origin
  // verification on the backend (browsers only send it for XHR/fetch requests).
  config.headers["X-Requested-With"] = "XMLHttpRequest";
  const token = useAuthStore().token;
  if (token) config.headers.Authorization = `Bearer ${token}`;

  // Attach idempotency keys for AI proxy writes.
  const path = `${config.baseURL ?? ""}${config.url ?? ""}`;
  const isAiProxy = path.includes(AGENT.aiRoot);
  if (isAiProxy && ["post", "put", "patch"].includes(config.method?.toLowerCase() ?? "")) {
    config.headers["X-Idempotency-Key"] = crypto.randomUUID();
  }

  return config;
});

http.interceptors.response.use(
  (r) => {
    // Leave empty responses untouched.
    if (r.status === 204 || r.data === "" || r.data == null) {
      r.data = null;
      return r;
    }

    // Unwrap success envelopes emitted by the gateway middleware.
    r.data = unwrapSuccessEnvelope(r.data);

    const newToken: unknown = r.headers["x-new-token"];
    if (isAcceptableRefreshToken(newToken)) {
      useAuthStore().setToken(newToken);
    }
    return r;
  },
  (err: AxiosError) => {
    const newToken: unknown = err.response?.headers["x-new-token"];
    if (isAcceptableRefreshToken(newToken)) {
      useAuthStore().setToken(newToken);
    }
    if (err.response && (err.response.status === 503 || err.response.status === 504)) {
      if (!_circuitOpen) {
        _circuitOpen = true;
        logWarn("[ZEN70 Circuit Breaker] OPEN - Hardware or Gateway Offline Detected");

        window.dispatchEvent(
          new CustomEvent("zen70-maintenance-mode", {
            detail: err.response.data,
          }),
        );

        if (circuitBreakTimer) clearTimeout(circuitBreakTimer);
        circuitBreakTimer = setTimeout(() => {
          _circuitOpen = false;
          logInfo("[ZEN70 Circuit Breaker] CLOSED - Retrying resumes");
        }, CIRCUIT_BREAKER_TIMEOUT);
      }
    }

    logError("[ZEN70] API error", {
      url: err.config?.url,
      status: err.response?.status,
      message: err.message,
    });
    return Promise.reject(err);
  },
);
