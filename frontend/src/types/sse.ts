/** 硬件事件：path、state、reason */
export interface HardwareEvent {
  type?: string;
  path?: string;
  state?: string;
  reason?: string;
}

/** 软开关事件 */
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

/** 物联网设备 ACK 事件（来自 IoT Bridge） */
export interface IoTUpdateEvent {
  device_id?: string;
  state?: string;
  source?: string;
}

export interface NodeControlEvent {
  event_id?: string;
  action?: "registered" | "updated" | "heartbeat";
  ts?: string;
  node?: Record<string, unknown>;
}

export interface JobControlEvent {
  event_id?: string;
  action?: "created" | "leased" | "completed" | "failed";
  ts?: string;
  node_id?: string;
  job?: Record<string, unknown>;
  jobs?: Record<string, unknown>[];
}

export interface ConnectorControlEvent {
  event_id?: string;
  action?: "upserted" | "invoked" | "tested";
  ts?: string;
  connector?: Record<string, unknown>;
  connector_id?: string;
  job_id?: string;
  status?: string;
  ok?: boolean;
  message?: string;
}

export interface SSEEvent {
  type: string;
  data:
    | HardwareEvent
    | SwitchEvent
    | BoardEvent
    | IoTUpdateEvent
    | NodeControlEvent
    | JobControlEvent
    | ConnectorControlEvent;
}
