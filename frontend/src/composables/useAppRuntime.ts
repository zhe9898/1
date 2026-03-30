import { onMounted, onUnmounted, ref, watch } from "vue";

import { SSE } from "@/utils/api";
import { requestPersistentStorage } from "@/utils/persist";
import { initWebPush } from "@/utils/push";
import { createSSE } from "@/utils/sse";
import type { HardwareEvent, SSEEvent, SwitchEvent } from "@/types/sse";

interface AuthState {
  token: string | null;
  isAdmin: boolean;
}

interface CapabilitiesStore {
  isOffline: boolean;
  fetchCapabilities: () => Promise<void>;
  syncOnReconnect: () => Promise<void>;
  updateHardware: (event: HardwareEvent) => void;
}

interface ConsoleStore {
  refresh: () => Promise<void>;
}

interface SwitchStore {
  loadCached: () => Promise<void>;
  updateFromEvent: (event: SwitchEvent) => void;
}

interface EventsStore {
  push: (event: SSEEvent) => void;
}

interface UseAppRuntimeOptions {
  auth: AuthState;
  capsStore: CapabilitiesStore;
  consoleStore: ConsoleStore;
  switchStore: SwitchStore;
  eventsStore: EventsStore;
}

export function useAppRuntime({
  auth,
  capsStore,
  consoleStore,
  switchStore,
  eventsStore,
}: UseAppRuntimeOptions) {
  const isMaintenanceMode = ref(false);
  const maintenanceError = ref<string | null>(null);
  const persistWarn = ref(false);
  const appNotice = ref("");
  const appNoticeLevel = ref<"info" | "error">("info");
  let closeSSE: (() => void) | null = null;
  let webPushTokenBound: string | null = null;

  function refresh() {
    void capsStore.fetchCapabilities();
  }

  function resetMaintenance() {
    isMaintenanceMode.value = false;
    maintenanceError.value = null;
    refresh();
  }

  function handleMaintenanceEvent(event: Event) {
    const customEvent = event as CustomEvent<unknown>;
    isMaintenanceMode.value = true;
    maintenanceError.value = customEvent.detail != null ? JSON.stringify(customEvent.detail) : null;
  }

  function handleOnline() {
    void capsStore.syncOnReconnect();
  }

  function maybeInitWebPush() {
    if (!auth.token || webPushTokenBound === auth.token) return;
    void initWebPush().catch(() => {
      appNoticeLevel.value = "info";
      appNotice.value = "Web Push 初始化失败，可在系统设置稍后重试。";
      webPushTokenBound = null;
    });
    webPushTokenBound = auth.token;
  }

  onMounted(async () => {
    const granted = await requestPersistentStorage();
    if (!granted) persistWarn.value = true;

    maybeInitWebPush();
    void capsStore.fetchCapabilities();
    if (auth.token) {
      void consoleStore.refresh();
    }
    void switchStore.loadCached();

    window.addEventListener("online", handleOnline);
    window.addEventListener("zen70-maintenance-mode", handleMaintenanceEvent);
    closeSSE = createSSE(
      SSE.events(),
      (event) => {
        eventsStore.push(event);
        if (event.type === "hardware:events") capsStore.updateHardware(event.data as HardwareEvent);
        if (event.type === "switch:events") switchStore.updateFromEvent(event.data as SwitchEvent);
      },
      ["hardware:events", "switch:events", "node:events", "job:events", "connector:events"],
      {
        onFallbackOffline: () => {
          capsStore.isOffline = true;
        },
      },
    );
  });

  watch(
    () => auth.token,
    (token) => {
      if (!token) {
        webPushTokenBound = null;
        return;
      }
      maybeInitWebPush();
      void consoleStore.refresh();
    },
  );

  onUnmounted(() => {
    window.removeEventListener("online", handleOnline);
    window.removeEventListener("zen70-maintenance-mode", handleMaintenanceEvent);
    closeSSE?.();
  });

  return {
    appNotice,
    appNoticeLevel,
    isMaintenanceMode,
    maintenanceError,
    persistWarn,
    refresh,
    resetMaintenance,
  };
}
