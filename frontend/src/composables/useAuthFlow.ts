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
    } catch {
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
