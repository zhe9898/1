import { CAPABILITIES_ROW_ID, db } from "@/db";
import type { Capabilities } from "@/types/capability";
import type { SwitchState } from "@/types/switch";
const SW_PREFIX = "switch:";

async function safe<T>(fn: () => Promise<T>, fallback: T): Promise<T> {
  try {
    return await fn();
  } catch {
    return fallback;
  }
}

export async function cacheCapabilities(caps: Capabilities): Promise<void> {
  await safe(
    () =>
      db.capabilities.put({
        id: CAPABILITIES_ROW_ID,
        data: caps,
        lastUpdated: Date.now(),
      }),
    undefined
  );
}

export async function getCachedCapabilities(): Promise<Capabilities | null> {
  const row = await safe(() => db.capabilities.get(CAPABILITIES_ROW_ID), null);
  return row?.data ?? null;
}

export async function cacheSwitch(name: string, data: SwitchState): Promise<void> {
  await safe(() => db.switches.put({ name: SW_PREFIX + name, data, updated: Date.now() }), undefined);
}

export async function getCachedSwitches(): Promise<Record<string, SwitchState>> {
  const rows = await safe(() => db.switches.where("name").startsWith(SW_PREFIX).toArray(), []);
  const out: Record<string, SwitchState> = {};
  for (const r of rows) {
    out[r.name.slice(SW_PREFIX.length)] = r.data;
  }
  return out;
}
