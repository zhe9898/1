/**
 * @description 能力矩阵 store，与后端 /api/v1/capabilities 契约对齐
 */
import { defineStore } from "pinia";
import { ref } from "vue";
import { http } from "@/utils/http";
import { SYSTEM } from "@/utils/api";
import { saveCapabilities, loadCapabilities } from "@/services/offlineStorage";
import type { Capabilities } from "@/types/capability";
import type { HardwareEvent } from "@/types/sse";
import { logWarn } from "@/utils/logger";

function pathToSvc(p: string): string | null {
  const match = /\/([^/]+)$/.exec(p);
  return match?.[1] ?? null;
}

export const useCapabilitiesStore = defineStore("capabilities", () => {
  const caps = ref<Capabilities>({});
  const loading = ref(false);
  const error = ref<string | null>(null);
  /** 当前数据来自离线缓存 */
  const isOffline = ref(false);

  async function fetchCapabilities(): Promise<void> {
    loading.value = true;
    error.value = null;
    isOffline.value = false;
    if (!navigator.onLine) {
      const cached = await loadCapabilities();
      if (cached && Object.keys(cached.data).length > 0) {
        caps.value = cached.data;
        isOffline.value = true;
        if (cached.isStale) {
          logWarn("[ZEN70] 离线缓存已超过 24h，设备状态可能已过期");
        }
      } else {
        error.value = null;
      }
      loading.value = false;
      return;
    }
    try {
      const { data } = await http.get<Capabilities>(SYSTEM.capabilities);
      caps.value = data;
      void saveCapabilities(caps.value);
    } catch (e: unknown) {
      error.value = e instanceof Error ? e.message : "加载失败";
      const cached = await loadCapabilities();
      if (cached && Object.keys(cached.data).length > 0) {
        caps.value = cached.data;
        isOffline.value = true;
      }
    } finally {
      loading.value = false;
    }
  }

  /** 网络恢复后调用，拉取最新数据并刷新 UI */
  async function syncOnReconnect(): Promise<void> {
    if (!navigator.onLine) return;
    await fetchCapabilities();
  }

  /** 根据 SSE 硬件事件更新对应能力状态 */
  function updateHardware(ev: HardwareEvent): void {
    const path = ev.path;
    const state = ev.state as "online" | "offline" | "unknown" | undefined;
    if (!path || !state) return;
    const svc = pathToSvc(path);
    for (const [name, cap] of Object.entries(caps.value)) {
      const match =
        (svc !== null && name.toLowerCase().includes(svc)) ||
        (typeof cap.endpoint === "string" && cap.endpoint.includes(path));
      if (match) caps.value[name] = { ...cap, status: state, reason: ev.reason };
    }
  }

  return { caps, loading, error, isOffline, fetchCapabilities, syncOnReconnect, updateHardware };
});
