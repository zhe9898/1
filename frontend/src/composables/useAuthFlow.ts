/**
 * Auth flow composable — encapsulates bootstrap, login, and WebAuthn flows.
 *
 * Centralises all auth-related state + handlers so that Login.vue becomes a
 * thin orchestrator / layout shell.
 */

import { ref, onMounted } from "vue";
import { useRouter } from "vue-router";
import { useAuthStore } from "@/stores/auth";
import {
  startAuthentication,
  type PublicKeyCredentialRequestOptionsJSON,
} from "@simplewebauthn/browser";
import { http } from "@/utils/http";
import { AUTH } from "@/utils/api";
import { extractAxiosError } from "@/utils/errorMessage";
import { logWarn } from "@/utils/logger";
import type { AxiosError } from "axios";

export type ViewState = "bootstrap" | "login";

export function useAuthFlow() {
  const router = useRouter();
  const auth = useAuthStore();

  const loading = ref(true);
  const submitting = ref(false);
  const viewState = ref<ViewState>("login");
  const errorMsg = ref("");

  const bootForm = ref({ username: "", password: "", displayName: "" });
  const loginForm = ref({ tenantId: "default", username: "", password: "" });

  // Client-side login rate limiter: max 5 attempts, 30s cooldown after threshold.
  const MAX_LOGIN_ATTEMPTS = 5;
  const LOGIN_COOLDOWN_MS = 30_000;
  let loginAttemptCount = 0;
  let lastAttemptTime = 0;

  // ------------------------------------------------------------------
  // Init: probe system status
  // ------------------------------------------------------------------

  onMounted(async () => {
    try {
      const { data } = await http.get<{ initialized?: boolean; is_empty?: boolean }>(
        AUTH.sysStatus,
      );
      if (data.initialized === false || data.is_empty === true) {
        viewState.value = "bootstrap";
      } else {
        viewState.value = "login";
      }
    } catch (err: unknown) {
      logWarn("[ZEN70 Auth] 网关探针不可达", err);
      errorMsg.value = "无法连接至网关探针";
    } finally {
      loading.value = false;
    }
  });

  // ------------------------------------------------------------------
  // Handlers
  // ------------------------------------------------------------------

  async function handleBootstrap() {
    submitting.value = true;
    errorMsg.value = "";
    try {
      const { data } = await http.post<{ access_token: string }>(AUTH.bootstrap, {
        username: bootForm.value.username,
        password: bootForm.value.password,
        display_name: bootForm.value.displayName || "Admin",
      });
      auth.setToken(data.access_token);
      void router.push("/");
    } catch (err: unknown) {
      errorMsg.value = extractAxiosError(err, "初始化失败");
    } finally {
      submitting.value = false;
    }
  }

  async function handleLogin() {
    // Rate limit: cooldown after too many failed attempts.
    const now = Date.now();
    if (loginAttemptCount >= MAX_LOGIN_ATTEMPTS) {
      const elapsed = now - lastAttemptTime;
      if (elapsed < LOGIN_COOLDOWN_MS) {
        const remaining = Math.ceil((LOGIN_COOLDOWN_MS - elapsed) / 1000);
        errorMsg.value = `登录尝试过于频繁，请 ${remaining} 秒后重试`;
        return;
      }
      loginAttemptCount = 0;
    }
    loginAttemptCount += 1;
    lastAttemptTime = now;

    submitting.value = true;
    errorMsg.value = "";
    try {
      const username = loginForm.value.username.trim();
      const tenantId = loginForm.value.tenantId.trim() || "default";
      const { data } = await http.post<{ access_token: string }>(AUTH.passwordLogin, {
        ...loginForm.value,
        tenant_id: tenantId,
        username,
      });
      auth.setToken(data.access_token);
      loginAttemptCount = 0;
      void router.push("/");
    } catch (err: unknown) {
      errorMsg.value = extractAxiosError(err, "登录失败");
    } finally {
      submitting.value = false;
    }
  }

  async function handleWebAuthn(): Promise<void> {
    errorMsg.value = "";
    submitting.value = true;

    try {
      if (typeof window.PublicKeyCredential === "undefined") {
        errorMsg.value =
          "当前浏览器不支持通行密钥。请使用 Chrome/Edge/Safari 或升级版本。";
        return;
      }

      const hasPlatform =
        await PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable();
      if (!hasPlatform) {
        errorMsg.value =
          "未检测到平台认证器（指纹/Face ID）。请先在系统设置中启用生物识别，或使用密码登录。";
        return;
      }

      const username = loginForm.value.username.trim();
      const tenantId = loginForm.value.tenantId.trim() || "default";
      if (!username) {
        errorMsg.value = "请先输入账号，再进行通行密钥验证";
        return;
      }

      let beginData: { options?: PublicKeyCredentialRequestOptionsJSON };
      try {
        const { data } = await http.post<{ options?: PublicKeyCredentialRequestOptionsJSON }>(
          AUTH.webauthnLoginBegin,
          { tenant_id: tenantId, username },
        );
        beginData = data;
      } catch (err: unknown) {
        const ae = err as AxiosError;
        if (ae.response?.status === 404) {
          errorMsg.value =
            "通行密钥服务尚未部署。当前版本请使用密码登录，未来版本将自动启用。";
          return;
        }
        throw new Error("获取验证挑战失败");
      }

      if (!beginData.options) {
        throw new Error("服务端未返回 WebAuthn 登录参数");
      }

      const credential = await startAuthentication({
        optionsJSON: beginData.options,
      });

      const { data: verifyData } = await http.post<{ access_token: string }>(
        AUTH.webauthnLoginComplete,
        { tenant_id: tenantId, username, credential },
      );

      auth.setToken(verifyData.access_token);
      void router.push("/");
    } catch (err: unknown) {
      if (!errorMsg.value) {
        errorMsg.value = extractAxiosError(err, "通行密钥验证失败");
      }
    } finally {
      submitting.value = false;
    }
  }

  return {
    loading,
    submitting,
    viewState,
    errorMsg,
    bootForm,
    loginForm,
    handleBootstrap,
    handleLogin,
    handleWebAuthn,
  };
}
