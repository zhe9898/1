<template>
  <div
    v-if="loading && isEmpty"
    class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 p-4"
  >
    <SkeletonCard
      v-for="i in 6"
      :key="i"
    />
  </div>
  <div
    v-else-if="error && isEmpty"
    class="alert alert-warning m-4"
  >
    <span>{{ error }}</span>
    <button
      class="btn btn-sm"
      @click="refresh"
    >
      重试
    </button>
  </div>
  <div
    v-else
    class="p-4"
  >
    <div
      v-if="capsStore.isOffline && !isEmpty"
      class="alert alert-info mb-4"
    >
      <span>离线模式，数据可能不是最新</span>
    </div>
    <div
      v-else-if="isEmpty"
      class="alert alert-warning m-4"
    >
      <span>暂无能力数据，请确保后端服务已启动并联网后刷新</span>
      <button
        class="btn btn-sm"
        @click="refresh"
      >
        刷新
      </button>
    </div>
    <div
      v-else
      class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4"
    >
      <div
        v-for="(cap, name) in caps"
        :key="name"
        class="card bg-base-200 shadow-xl relative overflow-hidden transition-all duration-300 transform"
      >
        <!-- 离线或维护屏蔽遮罩层 (法典 6.2.1 情绪隔离与物理锁定) -->
        <div 
          v-if="cap.status === 'offline' || capsStore.isOffline" 
          class="absolute inset-0 z-10 flex flex-col items-center justify-center bg-base-100/40 backdrop-blur-[4px] pointer-events-none"
        >
          <span class="text-lg font-bold select-none text-base-content/80 drop-shadow-md tracking-wider">
            设备维护中
          </span>
        </div>

        <div
          class="card-body"
          :class="{ 'opacity-50 pointer-events-none': cap.status === 'offline' || capsStore.isOffline }"
        >
          <h2 class="card-title flex items-center gap-2">
            {{ name }}
            <span
              :class="STATUS_CLASS[cap.status] || 'badge-warning'"
              class="badge badge-sm"
            >
              {{ cap.status }}
            </span>
          </h2>
          <p class="text-sm text-base-content/80">
            端点: {{ cap.endpoint || "-" }}
          </p>
          <p
            v-if="cap.models?.length"
            class="text-sm"
          >
            模型: {{ cap.models.join(", ") }}
          </p>
          <p
            v-if="cap.reason"
            class="text-sm text-warning"
          >
            {{ cap.reason }}
          </p>
          <div class="card-actions justify-end mt-2">
            <button
              class="btn btn-xs btn-ghost"
              @click="openDetail(String(name))"
            >
              详情
            </button>
            <SwitchItem
              :service-name="String(name)"
              :cap="cap"
              @toggle="onToggle"
            />
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- 详情弹窗：极客/管理员可查看完整字段 -->
  <dialog
    ref="detailDialog"
    class="modal"
  >
    <div class="modal-box max-w-2xl">
      <h3 class="font-bold text-lg">
        {{ detailName }}
      </h3>
      <div class="mt-3 space-y-2 text-sm">
        <div class="flex items-center gap-2">
          <span
            class="badge"
            :class="STATUS_CLASS[detailCap?.status || 'unknown'] || 'badge-warning'"
          >{{ detailCap?.status }}</span>
          <span
            v-if="capsStore.isOffline"
            class="badge badge-outline"
          >离线缓存</span>
        </div>
        <div class="bg-base-200 rounded p-3">
          <div><span class="opacity-70">enabled</span>: {{ detailCap?.enabled }}</div>
          <div><span class="opacity-70">endpoint</span>: {{ detailCap?.endpoint || "-" }}</div>
          <div><span class="opacity-70">models</span>: {{ (detailCap?.models || []).join(", ") || "-" }}</div>
          <div><span class="opacity-70">reason</span>: {{ detailCap?.reason || "-" }}</div>
        </div>
      </div>
      <div class="modal-action">
        <form method="dialog">
          <button class="btn">
            关闭
          </button>
        </form>
      </div>
    </div>
  </dialog>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { useCapabilitiesStore } from "@/stores/capabilities";
import SwitchItem from "./SwitchItem.vue";
import SkeletonCard from "./SkeletonCard.vue";
import type { Capability } from "@/types/capability";
import { logInfo } from "@/utils/logger";

const capsStore = useCapabilitiesStore();
const caps = computed(() => capsStore.caps);
const loading = computed(() => capsStore.loading);
const error = computed(() => capsStore.error);
const isEmpty = computed(() => Object.keys(caps.value).length === 0);

function refresh() {
  void capsStore.fetchCapabilities();
}

const STATUS_CLASS: Record<string, string> = {
  online: "badge-success",
  offline: "badge-error",
  unknown: "badge-warning",
};

function onToggle(name: string) {
  logInfo("[ZEN70] toggle switch (placeholder)", name);
  // 后续实现 POST /api/v1/switches/:name
}

const detailDialog = ref<HTMLDialogElement | null>(null);
const detailName = ref("");
const detailCap = ref<Capability | null>(null);
function openDetail(name: string) {
  detailName.value = name;
  detailCap.value = (caps.value as Record<string, Capability>)[name] ?? null;
  detailDialog.value?.showModal();
}
</script>
