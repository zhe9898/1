/**
 * @description 持久化存储申请
 * 调用 navigator.storage.persist() 防止浏览器清理 IndexedDB 数据
 */
import { logInfo, logWarn } from "@/utils/logger";

export async function requestPersistentStorage(): Promise<boolean> {
  const persist = (navigator as { storage?: { persist?: () => Promise<boolean> } }).storage?.persist;
  if (typeof persist !== "function") {
    return false;
  }
  try {
    const granted = await persist();
    if (!granted) {
      logWarn("[ZEN70 Warning] 离线灾备存储申请被拒绝，当前设备存储空间紧张时，离线缓存 (IndexedDB) 可能会被浏览器自动清理，部分断网能力可用性将受损！");
    } else {
      logInfo("[ZEN70] Persistent storage granted:", granted);
    }
    return granted;
  } catch {
    return false;
  }
}

export async function isPersisted(): Promise<boolean> {
  const persisted = (navigator as { storage?: { persisted?: () => Promise<boolean> } }).storage?.persisted;
  if (typeof persisted !== "function") {
    return false;
  }
  try {
    return await persisted();
  } catch {
    return false;
  }
}
