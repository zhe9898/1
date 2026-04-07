/** 单个能力描述，与后端 CapabilityResponse 对齐 */
export interface Capability {
  endpoint?: string | null;
  models?: string[];
  status: "online" | "offline" | "unknown";
  reason?: string;
  enabled?: boolean;
}

/** 能力矩阵：服务名 -> 能力 */
export type Capabilities = Record<string, Capability>;
