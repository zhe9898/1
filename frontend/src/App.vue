<template>
  <div class="min-h-screen bg-base-300">
    <nav class="navbar bg-base-100 shadow-md">
      <div class="flex-1">
        <router-link
          to="/"
          class="btn btn-ghost text-xl"
        >
          ZEN70
        </router-link>
        <router-link
          v-for="it in protocolNavItems"
          :key="it.to"
          :to="it.to"
          class="btn btn-ghost btn-sm"
          :class="{ 'btn-disabled opacity-60': !it.enabled }"
        >
          {{ it.label }}
        </router-link>
      </div>
      <div class="flex-none gap-2">
        <!-- 可选能力状态条：网页开关驱动（capabilities 协议闭环） -->
        <div
          v-if="auth.isAuthenticated"
          class="btn btn-sm btn-ghost pointer-events-none"
        >
          <span class="mr-2 text-xs opacity-60">{{ profileBadge }}</span>
          <span
            class="badge badge-xs"
            :class="nodeApiEnabled ? 'badge-success' : 'badge-ghost'"
          >N</span>
          <span
            class="badge badge-xs"
            :class="jobApiEnabled ? 'badge-success' : 'badge-ghost'"
          >J</span>
          <span
            class="badge badge-xs"
            :class="connectorApiEnabled ? 'badge-success' : 'badge-ghost'"
          >C</span>
        </div>

        <div class="dropdown dropdown-end">
          <label
            tabindex="0"
            class="btn btn-sm btn-ghost m-1"
          >
            主题 / 壁纸
          </label>
          <ul
            tabindex="0"
            class="dropdown-content z-[100] menu p-2 shadow-2xl bg-base-100/90 backdrop-blur-xl rounded-box w-56"
          >
            <li class="menu-title">
              <span>视觉引擎</span>
            </li>
            <li><a @click="themeStore.toggleWallpaper()">动态流体背景 ({{ themeStore.liveWallpaperEnabled ? '开' : '关' }})</a></li>
            <li><a @click="triggerWallpaperUpload">🖼️ 设置自定义壁纸</a></li>
            <li v-if="themeStore.customWallpaperUrl">
              <a
                class="text-error"
                @click="themeStore.clearCustomWallpaper"
              >✕ 恢复默认壁纸</a>
            </li>
            <div class="divider my-0" />
            <li class="menu-title">
              <span>色彩主题</span>
            </li>
            <li
              v-for="t in themeStore.availableThemes"
              :key="t"
            >
              <a
                :class="{ 'active': themeStore.currentTheme === t }"
                @click="themeStore.setTheme(t)"
              >{{ t }}</a>
            </li>
          </ul>
        </div>
        
        <!-- 隐藏的壁纸上传输入框 -->
        <input
          ref="wallpaperInput"
          type="file"
          class="hidden"
          accept="image/*"
          @change="handleWallpaperUpload"
        >
        <div
          v-if="auth.isAuthenticated && !auth.isElder && !auth.isChild"
          class="dropdown dropdown-end"
        >
          <label
            tabindex="0"
            class="btn btn-sm btn-ghost m-1 flex items-center gap-1"
          >
            <span v-if="auth.aiRoutePreference === 'cloud'">☁️ 云端增强</span>
            <span v-else-if="auth.aiRoutePreference === 'local'">🛡️ 本地优先</span>
            <span v-else>🤖 自动路由</span>
          </label>
          <ul
            tabindex="0"
            class="dropdown-content z-[100] menu p-2 shadow bg-base-100 rounded-box w-52"
          >
            <li class="menu-title">
              <span>AI 大脑计算链路</span>
            </li>
            <li>
              <a
                :class="{'active': auth.aiRoutePreference === 'local'}"
                @click="auth.updateAiPreference('local')"
              >🛡️ 私有本地版 (安全)</a>
            </li>
            <li>
              <a
                :class="{'active': auth.aiRoutePreference === 'cloud'}"
                @click="auth.updateAiPreference('cloud')"
              >☁️ 云端增强版 (高速)</a>
            </li>
            <li>
              <a
                :class="{'active': auth.aiRoutePreference === 'auto'}"
                @click="auth.updateAiPreference('auto')"
              >🤖 自动智能路由</a>
            </li>
          </ul>
        </div>
        <button
          class="btn btn-sm btn-ghost"
          @click="refresh"
        >
          刷新
        </button>
      </div>
    </nav>
    <LiveWallpaper />
    <main class="relative z-10 p-4">
      <RouterView />
    </main>

    <!-- 情绪隔离与优雅降级控制层 (法典 2.5 & 3.3.1) -->
    <div 
      v-if="isMaintenanceMode" 
      class="fixed inset-0 z-50 flex items-center justify-center bg-base-100/30 backdrop-blur-md"
    >
      <div class="text-center bg-base-100 p-8 rounded-xl shadow-2xl max-w-sm">
        <svg
          xmlns="http://www.w3.org/2000/svg"
          class="w-16 h-16 mx-auto mb-4 text-warning"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path
            stroke-linecap="round"
            stroke-linejoin="round"
            stroke-width="2"
            d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
          />
        </svg>
        <h2 class="text-2xl font-bold mb-2">
          设备维护中
        </h2>
        <p class="text-base-content/70 mb-4">
          硬件暂时离线或系统过载，请稍后再试。
        </p>
        <div
          v-if="auth.isAdmin"
          class="text-left bg-base-300 p-3 rounded text-xs overflow-auto"
        >
          <p class="font-bold text-error">
            控制台调试信息:
          </p>
          <pre>{{ maintenanceError || 'No trace available' }}</pre>
        </div>
        <button
          class="btn btn-primary mt-4"
          @click="resetMaintenance"
        >
          尝试恢复
        </button>
      </div>
    </div>

    <!-- 离线存储警告 Toast (法典 6.1.3 免责) -->
    <div
      v-if="persistWarn"
      class="toast toast-end z-50"
    >
      <div class="alert alert-warning">
        <span>离线灾备存储受限。应用数据可能被清理。</span>
        <button
          class="btn btn-ghost btn-sm"
          @click="persistWarn = false"
        >
          ✕
        </button>
      </div>
    </div>
    <div
      v-if="appNotice"
      class="toast toast-end z-50"
    >
      <div
        class="alert"
        :class="appNoticeLevel === 'error' ? 'alert-error' : 'alert-info'"
      >
        <span>{{ appNotice }}</span>
        <button
          class="btn btn-ghost btn-sm"
          @click="appNotice = ''"
        >
          ✕
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { useAuthStore } from "@/stores/auth";
import { useThemeStore } from "@/stores/theme";
import { useCapabilitiesStore } from "@/stores/capabilities";
import { useConsoleStore } from "@/stores/console";
import { useSwitchStore } from "@/stores/switch";
import { useEventsStore } from "@/stores/events";
import { findCapabilityByEndpoint } from "@/utils/controlPlane";
import LiveWallpaper from "@/components/LiveWallpaper.vue";
import { useAppRuntime } from "@/composables/useAppRuntime";
import { useWallpaperPicker } from "@/composables/useWallpaperPicker";

const auth = useAuthStore();
const themeStore = useThemeStore();
const capsStore = useCapabilitiesStore();
const consoleStore = useConsoleStore();
const switchStore = useSwitchStore();
const eventsStore = useEventsStore();

function routeEnabled(routeName: string): boolean {
  const item = consoleStore.menu.find((entry) => entry.route_name === routeName);
  if (!item) return false;
  const cap = findCapabilityByEndpoint(capsStore.caps, item.endpoint);
  if (cap == null) return item.enabled;
  return item.enabled && (cap.enabled ?? true) && cap.status.toLowerCase() !== "offline";
}

const nodeApiEnabled = computed(() => routeEnabled("nodes"));
const jobApiEnabled = computed(() => routeEnabled("jobs"));
const connectorApiEnabled = computed(() => routeEnabled("connectors"));

const profileBadge = computed(() => consoleStore.profile?.product ?? "ZEN70 Gateway Kernel");

interface NavItem {
  to: string;
  label: string;
  enabled: boolean;
}

const protocolNavItems = computed<NavItem[]>(() => {
  if (!auth.isAuthenticated || !consoleStore.hasMenu) return [];
  return consoleStore.menu.map((item) => ({
    to: item.route_path,
    label: item.label,
    enabled: item.enabled,
  }));
});

const { appNotice, appNoticeLevel, isMaintenanceMode, maintenanceError, persistWarn, refresh, resetMaintenance } = useAppRuntime({
  auth,
  capsStore,
  consoleStore,
  switchStore,
  eventsStore,
});
const { handleWallpaperUpload, triggerWallpaperUpload, wallpaperInput } = useWallpaperPicker(themeStore, appNotice, appNoticeLevel);
</script>
