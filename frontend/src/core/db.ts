/**
 * @description 离线资产缓存数据库
 */
import { db } from "@/db";
import { http } from "@/utils/http";
import { logWarn } from "@/utils/logger";

export const cacheImage = async (id: string, url: string): Promise<void> => {
  try {
    // 先检查是否已有缓存
    const existing = await db.cachedAssets.get(id);
    if (existing) return;

    // Fetch 并转为 Base64
    const response = await http.get<Blob>(url, { responseType: "blob", baseURL: "" });
    const blob = response.data;
    const reader = new FileReader();
    reader.readAsDataURL(blob);

    reader.onloadend = () => {
      const base64data = reader.result as string;
      void db.cachedAssets.put({
        id,
        url,
        base64Data: base64data,
        timestamp: Date.now(),
      });
    };
  } catch (e: unknown) {
    logWarn("Failed to cache image for offline use:", e);
  }
};

export const getCachedImage = async (id: string): Promise<string | null> => {
  try {
    const record = await db.cachedAssets.get(id);
    return record ? record.base64Data : null;
  } catch {
    return null;
  }
};
