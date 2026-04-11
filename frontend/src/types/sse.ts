/** Hardware state updates from the control plane. */
export interface HardwareEvent {
  type?: string;
  path?: string;
  state?: string;
  reason?: string;
}

/** Switch toggles and safety-state changes. */
export interface SwitchEvent {
  name?: string;
  state?: string;
  reason?: string;
}

export interface BoardEvent {
  action?: string;
  message_id?: string;
  author?: string;
}

/** Device ACK or bridge-originated update. */
export interface IoTUpdateEvent {
  device_id?: string;
  state?: string;
  source?: string;
}

export interface NodeControlEvent {
  event_id?: string;
  action?: "registered" | "updated" | "heartbeat";
  ts?: string;
  tenant_id?: string;
  node?: Record<string, unknown>;
}

export interface JobControlEvent {
  event_id?: string;
  action?: "created" | "leased" | "completed" | "failed";
  ts?: string;
  tenant_id?: string;
  node_id?: string;
  job?: Record<string, unknown>;
  jobs?: Record<string, unknown>[];
}

export interface ConnectorControlEvent {
  event_id?: string;
  action?: "upserted" | "invoked" | "tested";
  ts?: string;
  tenant_id?: string;
  connector?: Record<string, unknown>;
  connector_id?: string;
  job_id?: string;
  status?: string;
  ok?: boolean;
  message?: string;
}

export interface ReservationControlEvent {
  event_id?: string;
  action?: "created" | "canceled" | "expired";
  ts?: string;
  tenant_id?: string;
  reservation?: Record<string, unknown>;
  reason?: string;
  source?: string;
}

export interface TriggerControlEvent {
  event_id?: string;
  action?: "upserted" | "activated" | "paused" | "fired" | "delivery_failed";
  ts?: string;
  tenant_id?: string;
  trigger?: Record<string, unknown>;
  delivery?: Record<string, unknown>;
}

export const BROWSER_REALTIME_CHANNELS = [
  "hardware:events",
  "switch:events",
  "node:events",
  "job:events",
  "connector:events",
  "reservation:events",
  "trigger:events",
] as const;

export type SSEChannel = (typeof BROWSER_REALTIME_CHANNELS)[number];

export interface SSEPayloadByType {
  "hardware:events": HardwareEvent;
  "switch:events": SwitchEvent;
  "node:events": NodeControlEvent;
  "job:events": JobControlEvent;
  "connector:events": ConnectorControlEvent;
  "reservation:events": ReservationControlEvent;
  "trigger:events": TriggerControlEvent;
}

export type SSEEvent = {
  [K in keyof SSEPayloadByType]: {
    type: K;
    data: SSEPayloadByType[K];
  };
}[keyof SSEPayloadByType];
