<template>
  <div class="zen-card-header">
    <div class="zen-badge">
      首次运行
    </div>
    <h2>创建管理员</h2>
    <p>系统检测到首次启动，请创建控制台管理员账号</p>
  </div>
  <form
    class="zen-form"
    @submit.prevent="$emit('submit')"
  >
    <div class="zen-field">
      <label>管理员账号</label>
      <div class="zen-input-wrap">
        <svg
          class="zen-input-icon"
          viewBox="0 0 20 20"
          fill="currentColor"
        ><path d="M10 9a3 3 0 100-6 3 3 0 000 6zm-7 9a7 7 0 1114 0H3z" /></svg>
        <input
          :value="form.username"
          type="text"
          placeholder="admin"
          required
          autocomplete="username"
          @input="$emit('update:form', { ...form, username: ($event.target as HTMLInputElement).value })"
        >
      </div>
    </div>
    <div class="zen-field">
      <label>显示名称</label>
      <div class="zen-input-wrap">
        <svg
          class="zen-input-icon"
          viewBox="0 0 20 20"
          fill="currentColor"
        ><path d="M17.414 2.586a2 2 0 00-2.828 0L7 10.172V13h2.828l7.586-7.586a2 2 0 000-2.828z" /><path
          fill-rule="evenodd"
          d="M2 6a2 2 0 012-2h4a1 1 0 010 2H4v10h10v-4a1 1 0 112 0v4a2 2 0 01-2 2H4a2 2 0 01-2-2V6z"
          clip-rule="evenodd"
        /></svg>
        <input
          :value="form.displayName"
          type="text"
          placeholder="主理人"
          autocomplete="name"
          @input="$emit('update:form', { ...form, displayName: ($event.target as HTMLInputElement).value })"
        >
      </div>
    </div>
    <div class="zen-field">
      <label>安全密码</label>
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
          placeholder="至少 8 位"
          required
          minlength="8"
          autocomplete="new-password"
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
        d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-8.707l-3-3a1 1 0 00-1.414 1.414L10.586 9H7a1 1 0 100 2h3.586l-1.293 1.293a1 1 0 101.414 1.414l3-3a1 1 0 000-1.414z"
        clip-rule="evenodd"
      /></svg>
      接管系统
    </button>
  </form>
</template>

<script setup lang="ts">
defineProps<{
  form: { username: string; password: string; displayName: string };
  errorMsg: string;
  submitting: boolean;
}>();

defineEmits<{
  (e: "submit"): void;
  (e: "update:form", val: { username: string; password: string; displayName: string }): void;
}>();
</script>
