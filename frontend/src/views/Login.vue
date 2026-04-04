<template>
  <div class="zen-login-root">
    <!-- 动态流体背景 -->
    <div class="zen-bg">
      <div class="zen-orb zen-orb-1" />
      <div class="zen-orb zen-orb-2" />
      <div class="zen-orb zen-orb-3" />
      <div class="zen-grid-overlay" />
    </div>

    <!-- 主容器 -->
    <div class="zen-container">
      <!-- 品牌区 -->
      <div class="zen-brand">
        <div class="zen-logo">
          <svg
            width="48"
            height="48"
            viewBox="0 0 48 48"
            fill="none"
            xmlns="http://www.w3.org/2000/svg"
          >
            <rect
              width="48"
              height="48"
              rx="14"
              fill="url(#logo-grad)"
            />
            <path
              d="M14 17h20l-7 7h-6l-7-7zm0 14l7-7h6l7 7H14z"
              fill="white"
              opacity="0.95"
            />
            <defs>
              <linearGradient
                id="logo-grad"
                x1="0"
                y1="0"
                x2="48"
                y2="48"
              >
                <stop stop-color="#6366f1" />
                <stop
                  offset="1"
                  stop-color="#8b5cf6"
                />
              </linearGradient>
            </defs>
          </svg>
        </div>
        <h1 class="zen-title">
          ZEN<span class="zen-title-accent">70</span>
        </h1>
        <p class="zen-subtitle">
          Gateway Kernel
        </p>
      </div>

      <!-- 登录卡片 -->
      <div class="zen-card">
        <!-- 加载状态 -->
        <div
          v-if="loading"
          class="zen-loading"
        >
          <div class="zen-spinner" />
          <span>正在连接安全网关…</span>
        </div>

        <!-- 初始化 -->
        <BootstrapForm
          v-else-if="viewState === 'bootstrap'"
          :form="bootForm"
          :error-msg="errorMsg"
          :submitting="submitting"
          @submit="handleBootstrap"
          @update:form="bootForm = $event"
        />

        <!-- 登录 -->
        <LoginForm
          v-else-if="viewState === 'login'"
          :form="loginForm"
          :error-msg="errorMsg"
          :submitting="submitting"
          @submit="handleLogin"
          @update:form="loginForm = $event"
          @webauthn="handleWebAuthn"
        />
      </div>

      <!-- 底部 -->
      <p class="zen-footer">
        <span class="zen-footer-dot" /> 端到端加密 · 零信任架构
      </p>
    </div>
  </div>
</template>

<script setup lang="ts">
import BootstrapForm from "@/components/BootstrapForm.vue";
import LoginForm from "@/components/LoginForm.vue";
import { useAuthFlow } from "@/composables/useAuthFlow";

defineOptions({ name: "LoginView" });

const {
  loading,
  submitting,
  viewState,
  errorMsg,
  bootForm,
  loginForm,
  handleBootstrap,
  handleLogin,
  handleWebAuthn,
} = useAuthFlow();
</script>

<style scoped>
/* ===== 全局容器 ===== */
.zen-login-root {
  position: relative;
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  background: #06070a;
  font-family: 'Inter', 'SF Pro Display', -apple-system, BlinkMacSystemFont, system-ui, sans-serif;
}

/* ===== 流体背景 ===== */
.zen-bg {
  position: fixed;
  inset: 0;
  z-index: 0;
  overflow: hidden;
}
.zen-orb {
  position: absolute;
  border-radius: 50%;
  filter: blur(120px);
  opacity: 0.4;
  animation: zen-float 20s ease-in-out infinite;
}
.zen-orb-1 {
  width: 600px; height: 600px;
  background: radial-gradient(circle, #6366f1 0%, transparent 70%);
  top: -15%; left: -10%;
  animation-delay: 0s;
}
.zen-orb-2 {
  width: 500px; height: 500px;
  background: radial-gradient(circle, #8b5cf6 0%, transparent 70%);
  bottom: -20%; right: -10%;
  animation-delay: -7s;
  animation-duration: 25s;
}
.zen-orb-3 {
  width: 350px; height: 350px;
  background: radial-gradient(circle, #06b6d4 0%, transparent 70%);
  top: 50%; left: 50%;
  transform: translate(-50%, -50%);
  animation-delay: -14s;
  animation-duration: 18s;
  opacity: 0.2;
}
.zen-grid-overlay {
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(255,255,255,.02) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255,255,255,.02) 1px, transparent 1px);
  background-size: 60px 60px;
}
@keyframes zen-float {
  0%, 100% { transform: translate(0, 0) scale(1); }
  33% { transform: translate(30px, -40px) scale(1.05); }
  66% { transform: translate(-20px, 30px) scale(0.95); }
}

/* ===== 内容容器 ===== */
.zen-container {
  position: relative;
  z-index: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 28px;
  width: 100%;
  max-width: 420px;
  padding: 24px;
  animation: zen-fadein 0.8s ease-out;
}
@keyframes zen-fadein {
  from { opacity: 0; transform: translateY(20px); }
  to { opacity: 1; transform: translateY(0); }
}

/* ===== 品牌区 ===== */
.zen-brand {
  text-align: center;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
}
.zen-logo {
  animation: zen-pulse 3s ease-in-out infinite;
}
@keyframes zen-pulse {
  0%, 100% { filter: drop-shadow(0 0 8px rgba(99,102,241,0.3)); }
  50% { filter: drop-shadow(0 0 20px rgba(99,102,241,0.6)); }
}
.zen-title {
  font-size: 2.2rem;
  font-weight: 800;
  letter-spacing: -0.03em;
  color: #f0f0f5;
  margin: 0;
  line-height: 1;
}
.zen-title-accent {
  background: linear-gradient(135deg, #818cf8, #c084fc);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}
.zen-subtitle {
  font-size: 0.85rem;
  color: rgba(255,255,255,0.35);
  letter-spacing: 0.25em;
  text-transform: uppercase;
  margin: 0;
}

/* ===== 卡片 ===== */
.zen-card {
  width: 100%;
  background: rgba(255,255,255,0.04);
  border: 1px solid rgba(255,255,255,0.08);
  border-radius: 20px;
  padding: 36px 32px;
  backdrop-filter: blur(40px);
  box-shadow:
    0 0 0 1px rgba(255,255,255,0.03) inset,
    0 24px 48px -12px rgba(0,0,0,0.5),
    0 0 80px -20px rgba(99,102,241,0.1);
  transition: border-color 0.3s, box-shadow 0.3s;
}
.zen-card:hover {
  border-color: rgba(99,102,241,0.2);
  box-shadow:
    0 0 0 1px rgba(99,102,241,0.05) inset,
    0 24px 48px -12px rgba(0,0,0,0.5),
    0 0 100px -20px rgba(99,102,241,0.15);
}

/* ===== 卡片标题 ===== */
.zen-card-header {
  text-align: center;
  margin-bottom: 28px;
}
.zen-card-header h2 {
  font-size: 1.5rem;
  font-weight: 700;
  color: #f0f0f5;
  margin: 0 0 6px;
}
.zen-card-header p {
  font-size: 0.875rem;
  color: rgba(255,255,255,0.4);
  margin: 0;
}
.zen-badge {
  display: inline-block;
  padding: 4px 14px;
  border-radius: 999px;
  background: linear-gradient(135deg, rgba(99,102,241,0.15), rgba(139,92,246,0.15));
  border: 1px solid rgba(99,102,241,0.25);
  color: #a5b4fc;
  font-size: 0.75rem;
  font-weight: 600;
  letter-spacing: 0.05em;
  margin-bottom: 14px;
}

/* ===== 表单 ===== */
.zen-form {
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.zen-field label {
  display: block;
  font-size: 0.8rem;
  font-weight: 500;
  color: rgba(255,255,255,0.55);
  margin-bottom: 8px;
  letter-spacing: 0.02em;
}
.zen-input-wrap {
  position: relative;
  display: flex;
  align-items: center;
}
.zen-input-icon {
  position: absolute;
  left: 14px;
  width: 18px;
  height: 18px;
  color: rgba(255,255,255,0.2);
  pointer-events: none;
  transition: color 0.2s;
}
.zen-input-wrap:focus-within .zen-input-icon {
  color: #818cf8;
}
.zen-input-wrap input {
  width: 100%;
  padding: 14px 14px 14px 44px;
  font-size: 0.95rem;
  color: #e8e8f0;
  background: rgba(255,255,255,0.04);
  border: 1px solid rgba(255,255,255,0.08);
  border-radius: 12px;
  outline: none;
  transition: all 0.25s;
  font-family: inherit;
}
.zen-input-wrap input::placeholder {
  color: rgba(255,255,255,0.2);
}
.zen-input-wrap input:focus {
  border-color: #6366f1;
  background: rgba(99,102,241,0.06);
  box-shadow: 0 0 0 3px rgba(99,102,241,0.08);
}

/* ===== 按钮 ===== */
.zen-btn-primary {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  width: 100%;
  padding: 14px;
  font-size: 0.95rem;
  font-weight: 600;
  color: #fff;
  background: linear-gradient(135deg, #6366f1, #8b5cf6);
  border: none;
  border-radius: 12px;
  cursor: pointer;
  transition: all 0.3s;
  font-family: inherit;
  position: relative;
  overflow: hidden;
  margin-top: 6px;
}
.zen-btn-primary::before {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(135deg, #818cf8, #a78bfa);
  opacity: 0;
  transition: opacity 0.3s;
}
.zen-btn-primary:hover::before {
  opacity: 1;
}
.zen-btn-primary:hover {
  transform: translateY(-1px);
  box-shadow: 0 8px 24px -4px rgba(99,102,241,0.4);
}
.zen-btn-primary:active {
  transform: translateY(0);
  box-shadow: 0 4px 12px -2px rgba(99,102,241,0.3);
}
.zen-btn-primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  transform: none;
  box-shadow: none;
}
.zen-btn-primary > * {
  position: relative;
  z-index: 1;
}
.zen-btn-icon {
  width: 18px;
  height: 18px;
  flex-shrink: 0;
}
.zen-btn-spinner {
  width: 18px;
  height: 18px;
  border: 2px solid rgba(255,255,255,0.3);
  border-top-color: #fff;
  border-radius: 50%;
  animation: zen-spin 0.6s linear infinite;
}
@keyframes zen-spin {
  to { transform: rotate(360deg); }
}

.zen-btn-outline {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  width: 100%;
  padding: 14px;
  font-size: 0.92rem;
  font-weight: 500;
  color: rgba(255,255,255,0.65);
  background: transparent;
  border: 1px solid rgba(255,255,255,0.1);
  border-radius: 12px;
  cursor: pointer;
  transition: all 0.25s;
  font-family: inherit;
}
.zen-btn-outline:hover {
  color: #e8e8f0;
  border-color: rgba(255,255,255,0.2);
  background: rgba(255,255,255,0.04);
}

/* ===== 分割线 ===== */
.zen-divider {
  display: flex;
  align-items: center;
  gap: 14px;
  margin: 2px 0;
}
.zen-divider::before,
.zen-divider::after {
  content: '';
  flex: 1;
  height: 1px;
  background: rgba(255,255,255,0.06);
}
.zen-divider span {
  font-size: 0.75rem;
  color: rgba(255,255,255,0.2);
}

/* ===== 错误 ===== */
.zen-error {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
  border-radius: 10px;
  background: rgba(239,68,68,0.08);
  border: 1px solid rgba(239,68,68,0.15);
  color: #fca5a5;
  font-size: 0.85rem;
  line-height: 1.4;
}
.zen-error svg {
  width: 18px;
  height: 18px;
  flex-shrink: 0;
  color: #ef4444;
}

/* ===== 加载 ===== */
.zen-loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
  padding: 24px 0;
  color: rgba(255,255,255,0.4);
  font-size: 0.875rem;
}
.zen-spinner {
  width: 36px;
  height: 36px;
  border: 3px solid rgba(99,102,241,0.15);
  border-top-color: #6366f1;
  border-radius: 50%;
  animation: zen-spin 0.8s linear infinite;
}

/* ===== 底部 ===== */
.zen-footer {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 0.75rem;
  color: rgba(255,255,255,0.15);
  letter-spacing: 0.03em;
}
.zen-footer-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #22c55e;
  box-shadow: 0 0 6px #22c55e;
  animation: zen-dot-pulse 2s ease-in-out infinite;
}
@keyframes zen-dot-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}

/* ===== 响应式 ===== */
@media (max-width: 480px) {
  .zen-card { padding: 28px 20px; border-radius: 16px; }
  .zen-title { font-size: 1.8rem; }
  .zen-container { padding: 16px; }
}
</style>
