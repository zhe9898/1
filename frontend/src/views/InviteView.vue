<template>
  <div class="min-h-screen flex items-center justify-center bg-base-300 pointer-events-auto relative z-20">
    <div class="card w-full max-w-sm bg-base-100 shadow-xl">
      <div class="card-body items-center text-center">
        <div
          v-if="loading"
          class="py-8"
        >
          <span class="loading loading-spinner loading-lg text-primary" />
        </div>
        
        <template v-else-if="errorMsg">
          <h2 class="card-title text-error mb-2">
            邀请已失效
          </h2>
          <p class="text-sm text-base-content/70 mb-4">
            {{ errorMsg }}
          </p>
          <button
            class="btn"
            @click="goHome"
          >
            返回首页
          </button>
        </template>
        
        <template v-else-if="successMsg">
          <div class="text-success mb-4">
            <svg
              xmlns="http://www.w3.org/2000/svg"
              fill="none"
              viewBox="0 0 24 24"
              stroke-width="2"
              stroke="currentColor"
              class="w-16 h-16 mx-auto"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                d="M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
              />
            </svg>
          </div>
          <h2 class="card-title text-success mb-2">
            绑定成功
          </h2>
          <p class="text-sm text-base-content/70 mb-6">
            {{ successMsg }}
          </p>
          <button
            class="btn btn-primary"
            @click="enterSystem"
          >
            进入系统
          </button>
        </template>
        
        <template v-else>
          <div class="bg-primary/10 p-4 rounded-full mb-4">
            <svg
              xmlns="http://www.w3.org/2000/svg"
              fill="none"
              viewBox="0 0 24 24"
              stroke-width="1.5"
              stroke="currentColor"
              class="w-10 h-10 text-primary"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                d="M9 12.75L11.25 15 15 9.75M21 12c0 1.268-.63 2.39-1.593 3.068a3.745 3.745 0 01-1.043 3.296 3.745 3.745 0 01-3.296 1.043A3.745 3.745 0 0112 21c-1.268 0-2.39-.63-3.068-1.593a3.746 3.746 0 01-3.296-1.043 3.745 3.745 0 01-1.043-3.296A3.745 3.745 0 013 12c0-1.268.63-2.39 1.593-3.068a3.745 3.745 0 011.043-3.296 3.746 3.746 0 013.296-1.043A3.746 3.746 0 0112 3c1.268 0 2.39.63 3.068 1.593a3.746 3.746 0 013.296 1.043 3.746 3.746 0 011.043 3.296A3.745 3.745 0 0121 12z"
              />
            </svg>
          </div>
          <h2 class="card-title text-primary mb-2">
            ZEN70 授权邀请
          </h2>
          <p class="text-sm text-base-content/70 mb-6">
            指挥官已向您下发系统访问凭证。<br>
            请验证您的生物特征（Face ID / 指纹）以将此设备物理绑定至控制面板。
          </p>
          
          <button
            class="btn btn-primary w-full mb-3"
            :disabled="processing"
            @click="bindDevice"
          >
            <span
              v-if="processing"
              class="loading loading-spinner loading-sm"
            />
            验证身份并绑定本机
          </button>
          
          <button
            class="btn w-full btn-outline btn-sm text-base-content/60"
            :disabled="processing"
            @click="fallbackLogin"
          >
            硬件不支持？直接免密登入
          </button>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { startRegistration, type PublicKeyCredentialCreationOptionsJSON } from '@simplewebauthn/browser';
import { useAuthStore } from '@/stores/auth';
import { http } from '@/utils/http';
import { AUTH } from '@/utils/api';
import type { AxiosError } from 'axios';

const route = useRoute();
const router = useRouter();
const authStore = useAuthStore();

const token = ref('');
const loading = ref(true);
const processing = ref(false);
const errorMsg = ref('');
const successMsg = ref('');

/** 从 Axios 错误中提取人类可读信息 */
function extractAxiosError(err: unknown, fallback: string): string {
  if (err instanceof Error && 'isAxiosError' in err) {
    const ae = err as AxiosError<{ message?: string; detail?: string | { message?: string } }>;
    const body = ae.response?.data;
    if (body) {
      if (typeof body.detail === 'string') return body.detail;
      if (typeof body.detail === "object") {
        const detail = body.detail as { message?: string };
        if (typeof detail.message === "string") return detail.message;
      }
      if (body.message) return body.message;
    }
  }
  return err instanceof Error ? err.message : fallback;
}

onMounted(() => {
  const qToken = route.query.token as string;
  if (!qToken) {
    errorMsg.value = "缺少有效的邀请凭证";
  } else {
    token.value = qToken;
    // Remove token from URL to prevent leakage via browser history / Referer header.
    void router.replace({ ...route, query: { ...route.query, token: undefined } });
  }
  loading.value = false;
});

async function bindDevice() {
  processing.value = true;
  errorMsg.value = '';
  try {
    // 1. 发起注册，获取 Options（http 拦截器自动解包 envelope）
    const { data: beginData } = await http.post<{ options?: PublicKeyCredentialCreationOptionsJSON }>(
      AUTH.inviteWebauthnBegin(token.value)
    );
    if (!beginData.options) throw new Error('服务端未返回 WebAuthn options');

    const credential = await startRegistration({ optionsJSON: beginData.options });

    // 3. 将注册结果发回服务器，完成物理绑定与 Token 销毁
    const { data: completeData } = await http.post<{ access_token?: string }>(
      AUTH.inviteWebauthnComplete(token.value),
      { credential }
    );

    // 4. 保存登录态
    authStore.setToken(completeData.access_token ?? null);
    successMsg.value = '您的设备已通过最高级别安全认证！';
  } catch (err: unknown) {
    errorMsg.value = extractAxiosError(err, '由于安全原因，认证流程已中止。');
  } finally {
    processing.value = false;
  }
}

async function fallbackLogin() {
  if (!window.confirm('降级登入不会绑定硬件凭证，仅适用于无法完成 WebAuthn 的受控场景。确认继续吗？')) {
    return;
  }
  processing.value = true;
  errorMsg.value = '';
  try {
    const { data } = await http.post<{ access_token?: string }>(
      AUTH.inviteFallbackLogin(token.value),
      undefined,
      {
        headers: {
          'X-Invite-Fallback-Confirm': 'degrade-login',
        },
      }
    );
    // 保存登录态，降级无需硬件签名
    authStore.setToken(data.access_token ?? null);
    successMsg.value = '已通过降级模式免密登入系统。';
  } catch (err: unknown) {
    errorMsg.value = extractAxiosError(err, '降级登入失败。');
  } finally {
    processing.value = false;
  }
}

function goHome() {
  void router.push('/login');
}

function enterSystem() {
  if (authStore.isAdmin) {
    void router.push('/');
  } else {
    void router.push('/');
  }
}
</script>
