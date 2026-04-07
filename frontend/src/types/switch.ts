/** 软开关状态，与后端 SwitchStateResponse 对齐 */
export interface SwitchState {
  state: "ON" | "OFF" | "PENDING";
  reason?: string;
  updated_at: number;
  updated_by: string;
}
