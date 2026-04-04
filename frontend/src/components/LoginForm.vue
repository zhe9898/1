<template>
  <div class="zen-card-header">
    <h2>欢迎回来</h2>
    <p>登录您的控制面板</p>
  </div>
  <form
    class="zen-form"
    @submit.prevent="$emit('submit')"
  >
    <div class="zen-field">
      <label>Tenant</label>
      <div class="zen-input-wrap">
        <svg
          class="zen-input-icon"
          viewBox="0 0 20 20"
          fill="currentColor"
        ><path
          fill-rule="evenodd"
          d="M10 2a1 1 0 01.707.293l6 6A1 1 0 0117 9v8a1 1 0 01-1 1h-3a1 1 0 01-1-1v-4H8v4a1 1 0 01-1 1H4a1 1 0 01-1-1V9a1 1 0 01.293-.707l6-6A1 1 0 0110 2z"
          clip-rule="evenodd"
        /></svg>
        <input
          :value="form.tenantId"
          type="text"
          placeholder="default"
          required
          autocomplete="organization"
          @input="$emit('update:form', { ...form, tenantId: ($event.target as HTMLInputElement).value })"
        >
      </div>
    </div>
    <div class="zen-field">
      <label>账号</label>
      <div class="zen-input-wrap">
        <svg
          class="zen-input-icon"
          viewBox="0 0 20 20"
          fill="currentColor"
        ><path d="M10 9a3 3 0 100-6 3 3 0 000 6zm-7 9a7 7 0 1114 0H3z" /></svg>
        <input
          :value="form.username"
          type="text"
          placeholder="admin / family"
          required
          autocomplete="username"
          @input="$emit('update:form', { ...form, username: ($event.target as HTMLInputElement).value })"
        >
      </div>
    </div>
    <div class="zen-field">
      <label>密码</label>
      <div class="zen-input-wrap">
        <svg
          class="zen-input-icon"
          viewBox="0 0 20 20"
          fill="currentColor"
        ><path
          fill-rule="evenodd"
          d="M5 9V7a5 5 0 0110 0v2a2 2 0 012 2v5a2 2 0 01-2 2H5a2 2 0 01-2-2v-5a2 2 0 012-2zm8-2v2H7V7a3 3 0 016 0z"
          clip-rule="evenodd"
        /></svg>
        <input
          :value="form.password"
          type="password"
          placeholder="••••••••"
          required
          autocomplete="current-password"
          @input="$emit('update:form', { ...form, password: ($event.target as HTMLInputElement).value })"
        >
      </div>
    </div>
    <div
      v-if="errorMsg"
      class="zen-error"
    >
      <svg
        viewBox="0 0 20 20"
        fill="currentColor"
      ><path
        fill-rule="evenodd"
        d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z"
        clip-rule="evenodd"
      /></svg>
      {{ errorMsg }}
    </div>
    <button
      type="submit"
      class="zen-btn-primary"
      :disabled="submitting"
    >
      <span
        v-if="submitting"
        class="zen-btn-spinner"
      />
      <svg
        v-else
        viewBox="0 0 20 20"
        fill="currentColor"
        class="zen-btn-icon"
      ><path
        fill-rule="evenodd"
        d="M5 9V7a5 5 0 0110 0v2a2 2 0 012 2v5a2 2 0 01-2 2H5a2 2 0 01-2-2v-5a2 2 0 012-2zm8-2v2H7V7a3 3 0 016 0z"
        clip-rule="evenodd"
      /></svg>
      安全登录
    </button>
    <div class="zen-divider">
      <span>或</span>
    </div>
    <button
      type="button"
      class="zen-btn-outline"
      @click="$emit('webauthn')"
    >
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="1.5"
        class="zen-btn-icon"
      >
        <path
          stroke-linecap="round"
          stroke-linejoin="round"
          d="M7.864 4.243A7.5 7.5 0 0119.5 10.5c0 2.92-.556 5.709-1.568 8.268M5.742 6.364A7.465 7.465 0 004.5 10.5a7.464 7.464 0 01-1.15 3.993m1.989 3.559A11.209 11.209 0 008.25 10.5a3.75 3.75 0 117.5 0c0 .527-.021 1.049-.064 1.565M12 10.5a14.94 14.94 0 01-3.6 9.75M19.5 10.5h.008v.008H19.5V10.5z"
        />
      </svg>
      通行密钥验证
    </button>
  </form>
</template>

<script setup lang="ts">
defineProps<{
  form: { tenantId: string; username: string; password: string };
  errorMsg: string;
  submitting: boolean;
}>();

defineEmits<{
  (e: "submit"): void;
  (e: "update:form", val: { tenantId: string; username: string; password: string }): void;
  (e: "webauthn"): void;
}>();
</script>
