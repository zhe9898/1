import { defineStore } from "pinia";
import { ref } from "vue";
import { http } from "@/utils/http";
import { SWITCHES } from "@/utils/api";
import { cacheSwitch, getCachedSwitches } from "@/utils/db";
import type { SwitchState } from "@/types/switch";
import type { SwitchEvent } from "@/types/sse";

export const useSwitchStore = defineStore("switch", () => {
  const switches = ref<Record<string, SwitchState>>({});
  const loading = ref(false);

  async function fetchSwitch(name: string): Promise<SwitchState | null> {
    loading.value = true;
    try {
      const { data } = await http.get<SwitchState>(SWITCHES.toggle(name));
      const state: SwitchState = data;
      switches.value[name] = state;
      await cacheSwitch(name, state);
      return state;
    } catch {
      return null;
    } finally {
      loading.value = false;
    }
  }

  function updateFromEvent(ev: SwitchEvent): void {
    const name = ev.name;
    if (!name) return;
    switches.value[name] = {
      state: (ev.state as "ON" | "OFF" | "PENDING"),
      reason: ev.reason,
      updated_at: Date.now() / 1000,
      updated_by: "system",
    };
  }

  async function loadCached(): Promise<void> {
    const cached = await getCachedSwitches();
    if (Object.keys(cached).length > 0) {
      switches.value = { ...switches.value, ...cached };
    }
  }

  return { switches, loading, fetchSwitch, updateFromEvent, loadCached };
});
