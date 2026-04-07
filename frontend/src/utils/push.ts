/**
 * @description Web Push 订阅模块 (M5.3) — 工单 FE-HTTP-UNIFY-002 统一 HTTP 栈
 */

import { logError, logInfo, logWarn } from "@/utils/logger";
import { http, isCircuitOpen } from "@/utils/http";
import { AUTH } from "@/utils/api";

// Base64Url 解析工具（VAPID 需要用到）
export function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding)
    .replace(/-/g, "+")
    .replace(/_/g, "/");
  const rawData = window.atob(base64);
  const outputArray = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; ++i) {
    outputArray[i] = rawData.charCodeAt(i);
  }
  return outputArray;
}

interface VapidResponse {
  vapid_public_key: string;
}

export async function initWebPush(): Promise<boolean> {
  // 1. 检查浏览器是否支持 Service Worker 和 PushManager
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    logWarn("当前浏览器不支持 Web Push");
    return false;
  }

  try {
    // 2. 只有在此之前已经授权过，或者主动调起 requestPermission，才继续
    const permission = await Notification.requestPermission();
    if (permission !== "granted") {
      logWarn("用户拒绝了通知权限");
      return false;
    }

    // 3. 等待 PWA Service Worker 就绪
    const registration = await navigator.serviceWorker.ready;

    // 获取当前已有的订阅
    let subscription = await registration.pushManager.getSubscription();

    if (!subscription) {
      // ADR 0015: http 实例自带熔断器 + X-Request-ID + cookie-backed session auth
      if (isCircuitOpen()) {
        logWarn("Circuit Breaker OPEN: Web Push 注册延迟");
        return false;
      }

      // 4. 去后端拿 VAPID 公钥（统一走 http 实例，自动解包 envelope）
      const { data: vapidData } = await http.get<VapidResponse>(AUTH.pushVapidKey);
      const convertedVapidKey = urlBase64ToUint8Array(vapidData.vapid_public_key);

      // 5. 调用浏览器原生注册
      subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: convertedVapidKey.buffer as ArrayBuffer,
      });
    }

    // 6. 将 subscription 序列化通过 API 传给后端（统一走 http 实例）
    const subJSON = subscription.toJSON();
    if (subJSON.endpoint && subJSON.keys) {
      await http.post(AUTH.pushSubscribe, {
        endpoint: subJSON.endpoint,
        keys: subJSON.keys,
        user_agent: navigator.userAgent,
      });
    }

    logInfo("Web Push 订阅成功并上报");
    return true;
  } catch (err: unknown) {
    logError("Web Push 订阅全链路失败", err);
    return false;
  }
}
