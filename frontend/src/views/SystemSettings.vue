<template>
  <section class="mx-auto max-w-6xl space-y-6 px-6 py-8">
    <header class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <p class="text-sm uppercase tracking-[0.25em] text-base-content/60">
          Gateway Control Plane
        </p>
        <h1 class="mt-2 text-3xl font-semibold">
          Settings
        </h1>
        <p class="mt-2 max-w-2xl text-sm text-base-content/70">
          Kernel settings are rendered from backend schema. Profile, network, connector,
          and security sections no longer depend on handwritten frontend forms.
        </p>
      </div>
      <button
        class="btn btn-sm btn-primary"
        :disabled="loadingPage"
        @click="refreshAll"
      >
        Refresh
      </button>
    </header>

    <div
      v-if="pageError"
      class="alert alert-error"
    >
      <span>{{ pageError }}</span>
    </div>

    <div class="tabs tabs-boxed bg-base-200">
      <button
        v-for="tab in tabs"
        :key="tab.id"
        class="tab"
        :class="{ 'tab-active': activeTab === tab.id }"
        @click="activeTab = tab.id"
      >
        {{ tab.label }}
      </button>
    </div>

    <div
      v-if="activeTab === 'kernel'"
      class="grid gap-4 lg:grid-cols-2"
    >
      <article
        v-for="section in schema.sections"
        :key="section.id"
        class="card border border-base-300 bg-base-100 shadow-sm"
      >
        <div class="card-body space-y-4">
          <div>
            <h2 class="card-title text-xl">
              {{ section.label }}
            </h2>
            <p
              v-if="section.description"
              class="text-sm text-base-content/60"
            >
              {{ section.description }}
            </p>
          </div>

          <div class="space-y-3">
            <div
              v-for="field in section.fields"
              :key="field.key"
              class="rounded-2xl border border-base-300 bg-base-200/40 p-4"
            >
              <div class="flex flex-wrap items-start justify-between gap-3">
                <div class="max-w-xl">
                  <p class="font-medium">
                    {{ field.label }}
                  </p>
                  <p class="mt-1 text-xs text-base-content/60">
                    {{ field.description }}
                  </p>
                </div>
                <span
                  class="badge badge-sm"
                  :class="field.editable ? 'badge-primary' : 'badge-ghost'"
                >
                  {{ field.editable ? "editable" : "runtime" }}
                </span>
              </div>

              <div class="mt-3 flex flex-col gap-3 md:flex-row md:items-center">
                <input
                  v-if="field.input !== 'readonly'"
                  v-model="kernelValues[field.key]"
                  class="input input-bordered w-full"
                  :placeholder="field.placeholder ?? ''"
                  :disabled="!field.editable"
                >
                <div
                  v-else
                  class="w-full rounded-xl border border-base-300 bg-base-100 px-4 py-3 font-mono text-sm"
                >
                  {{ displayFieldValue(field.value) }}
                </div>

                <button
                  v-if="field.editable && field.save_path"
                  class="btn btn-sm btn-primary"
                  :disabled="savingFieldKey === field.key"
                  @click="saveField(field)"
                >
                  <span
                    v-if="savingFieldKey === field.key"
                    class="loading loading-spinner loading-xs"
                  />
                  Save
                </button>
              </div>
            </div>
          </div>
        </div>
      </article>
    </div>

    <div
      v-else-if="activeTab === 'flags'"
      class="space-y-5"
    >
      <article
        v-for="category in flagCategories"
        :key="category"
        class="card border border-base-300 bg-base-100 shadow-sm"
      >
        <div class="card-body">
          <h2 class="card-title text-xl">
            {{ category }}
          </h2>
          <div class="grid gap-3 md:grid-cols-2">
            <div
              v-for="flag in flagsByCategory(category)"
              :key="flag.key"
              class="rounded-2xl border border-base-300 bg-base-200/40 p-4"
            >
              <div class="flex items-start justify-between gap-3">
                <div>
                  <p class="font-medium">
                    {{ flag.key }}
                  </p>
                  <p class="mt-1 text-xs text-base-content/60">
                    {{ flag.description }}
                  </p>
                </div>
                <input
                  type="checkbox"
                  class="toggle toggle-primary"
                  :checked="flag.enabled"
                  :disabled="!authStore.isAdmin || togglingFlag === flag.key"
                  @change="toggleFlag(flag.key)"
                >
              </div>
            </div>
          </div>
        </div>
      </article>
    </div>

    <div
      v-else-if="activeTab === 'ai'"
      class="space-y-4"
    >
      <div class="flex flex-wrap items-center justify-between gap-3">
        <p class="text-sm text-base-content/60">
          AI models are discovered from backend providers and selected by capability.
        </p>
        <button
          class="btn btn-sm btn-primary"
          :disabled="scanningModels"
          @click="scanModels"
        >
          <span
            v-if="scanningModels"
            class="loading loading-spinner loading-xs"
          />
          Scan Models
        </button>
      </div>

      <article
        v-for="[provider, models] in providerEntries"
        :key="provider"
        class="card border border-base-300 bg-base-100 shadow-sm"
      >
        <div class="card-body">
          <div class="flex items-center justify-between gap-3">
            <h2 class="card-title text-xl">
              {{ provider }}
            </h2>
            <span class="badge badge-ghost">
              {{ models.length }}
            </span>
          </div>
          <div class="grid gap-3 lg:grid-cols-2">
            <button
              v-for="model in models"
              :key="`${provider}:${model.id}`"
              class="rounded-2xl border p-4 text-left transition hover:-translate-y-0.5 hover:shadow-md"
              :class="isModelSelected(model) ? 'border-primary bg-primary/5' : 'border-base-300 bg-base-200/30'"
              @click="selectModel(model)"
            >
              <div class="flex flex-wrap items-center gap-2">
                <span class="font-medium">{{ model.name || model.id }}</span>
                <span
                  v-if="isModelSelected(model)"
                  class="badge badge-primary badge-sm"
                >
                  current
                </span>
              </div>
              <p
                v-if="model.description"
                class="mt-2 text-sm text-base-content/60"
              >
                {{ model.description }}
              </p>
              <div class="mt-3 flex flex-wrap gap-2 text-xs">
                <span
                  v-for="capability in model.capabilities ?? []"
                  :key="capability"
                  class="badge badge-outline"
                >
                  {{ capability }}
                </span>
              </div>
            </button>
          </div>
        </div>
      </article>
    </div>

    <div
      v-else
      class="grid gap-4 md:grid-cols-2"
    >
      <article class="stat rounded-3xl border border-base-300 bg-base-100 shadow-sm">
        <div class="stat-title">
          Version
        </div>
        <div class="stat-value text-2xl">
          {{ systemInfo?.version ?? "-" }}
        </div>
        <div class="stat-desc">
          Python {{ systemInfo?.python ?? "-" }}
        </div>
      </article>
      <article class="stat rounded-3xl border border-base-300 bg-base-100 shadow-sm">
        <div class="stat-title">
          Platform
        </div>
        <div class="stat-value text-2xl">
          {{ systemInfo?.os ?? "-" }}
        </div>
        <div class="stat-desc">
          {{ systemInfo?.architecture ?? "-" }}
        </div>
      </article>
      <article class="stat rounded-3xl border border-base-300 bg-base-100 shadow-sm">
        <div class="stat-title">
          GPU
        </div>
        <div class="stat-value text-2xl">
          {{ systemInfo?.gpu ?? "-" }}
        </div>
      </article>
      <article class="stat rounded-3xl border border-base-300 bg-base-100 shadow-sm">
        <div class="stat-title">
          Disk
        </div>
        <div class="stat-value text-2xl">
          {{ diskValue }}
        </div>
        <div class="stat-desc">
          {{ diskUsage }}
        </div>
      </article>
      <article class="card border border-base-300 bg-base-100 shadow-sm md:col-span-2">
        <div class="card-body">
          <h2 class="card-title text-xl">
            Provider Health
          </h2>
          <div class="flex flex-wrap gap-2">
            <span
              v-for="(health, name) in systemInfo?.ai_providers ?? {}"
              :key="name"
              class="badge"
              :class="health.status === 'online' ? 'badge-success' : health.status === 'available' ? 'badge-info' : 'badge-ghost'"
            >
              {{ name }}: {{ health.status }}
            </span>
          </div>
        </div>
      </article>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useAuthStore } from "@/stores/auth";
import { http } from "@/utils/http";
import { SETTINGS } from "@/utils/api";
import { extractAxiosError } from "@/utils/errorMessage";

type TabId = "kernel" | "flags" | "ai" | "system";

interface SettingsSchemaField {
  key: string;
  label: string;
  value: string | boolean | null;
  description?: string | null;
  input: string;
  editable: boolean;
  save_path?: string | null;
  placeholder?: string | null;
}

interface SettingsSchemaSection {
  id: string;
  label: string;
  description?: string | null;
  fields: SettingsSchemaField[];
}

interface SettingsSchemaResponse {
  product: string;
  profile: string;
  runtime_profile: string;
  sections: SettingsSchemaSection[];
}

interface FeatureFlag {
  key: string;
  enabled: boolean;
  description: string;
  category: string;
}

interface AIModel {
  id: string;
  name?: string;
  provider: string;
  description?: string;
  capabilities?: string[];
}

interface DiskInfo {
  status?: string;
  free_gb?: number;
  usage_pct?: number;
}

interface SystemInfo {
  version: string;
  python: string;
  os: string;
  architecture: string;
  gpu?: string;
  disk?: DiskInfo;
  ai_models?: Record<string, string>;
  ai_providers?: Record<string, { status: string }>;
}

const authStore = useAuthStore();

const tabs: { id: TabId; label: string }[] = [
  { id: "kernel", label: "Kernel" },
  { id: "flags", label: "Flags" },
  { id: "ai", label: "AI" },
  { id: "system", label: "System" },
];

const activeTab = ref<TabId>("kernel");
const pageError = ref("");
const savingFieldKey = ref<string | null>(null);
const togglingFlag = ref<string | null>(null);
const scanningModels = ref(false);

const schema = ref<SettingsSchemaResponse>({
  product: "ZEN70 Gateway Kernel",
  profile: "gateway-kernel",
  runtime_profile: "gateway-kernel",
  sections: [],
});
const kernelValues = ref<Record<string, string>>({});
const flags = ref<FeatureFlag[]>([]);
const modelsByProvider = ref<Record<string, AIModel[]>>({});
const selectedModels = ref<Record<string, string>>({});
const systemInfo = ref<SystemInfo | null>(null);

const loadingPage = computed(
  () => savingFieldKey.value !== null || togglingFlag.value !== null || scanningModels.value
);

const flagCategories = computed(() => {
  return Array.from(new Set(flags.value.map((flag) => flag.category)));
});

const providerEntries = computed(() => Object.entries(modelsByProvider.value));

const diskValue = computed(() => {
  if (!systemInfo.value?.disk) return "-";
  if (typeof systemInfo.value.disk.free_gb === "number") {
    return `${systemInfo.value.disk.free_gb.toString()} GB`;
  }
  return systemInfo.value.disk.status ?? "-";
});

const diskUsage = computed(() => {
  if (!systemInfo.value?.disk || typeof systemInfo.value.disk.usage_pct !== "number") {
    return "runtime reported";
  }
  return `used ${systemInfo.value.disk.usage_pct.toString()}%`;
});

function displayFieldValue(value: string | boolean | null): string {
  if (typeof value === "boolean") return value ? "true" : "false";
  if (value == null || value === "") return "-";
  return value;
}

function flagsByCategory(category: string): FeatureFlag[] {
  return flags.value.filter((flag) => flag.category === category);
}

function detectCapability(model: AIModel): string {
  const capabilities = model.capabilities ?? [];
  if (capabilities.includes("embed")) return "embed";
  if (capabilities.includes("vision")) return "vision";
  return "chat";
}

function isModelSelected(model: AIModel): boolean {
  const capability = detectCapability(model);
  return selectedModels.value[capability] === `${model.provider}:${model.id}`;
}

async function loadKernelSchema(): Promise<void> {
  const { data } = await http.get<SettingsSchemaResponse>(SETTINGS.schema);
  schema.value = data;
  const nextValues: Record<string, string> = {};
  for (const section of data.sections) {
    for (const field of section.fields) {
      nextValues[field.key] = field.value == null ? "" : String(field.value);
    }
  }
  kernelValues.value = nextValues;
}

async function loadFlags(): Promise<void> {
  const { data } = await http.get<{ data?: FeatureFlag[] }>(SETTINGS.flags);
  flags.value = data.data ?? [];
}

async function loadModels(): Promise<void> {
  const [{ data: modelsData }, { data: systemData }] = await Promise.all([
    http.get<{ by_provider?: Record<string, AIModel[]> }>(SETTINGS.aiModels),
    http.get<SystemInfo>(SETTINGS.system),
  ]);
  modelsByProvider.value = modelsData.by_provider ?? {};
  selectedModels.value = systemData.ai_models ?? {};
}

async function loadSystemInfo(): Promise<void> {
  const { data } = await http.get<SystemInfo>(SETTINGS.system);
  systemInfo.value = data;
}

async function refreshAll(): Promise<void> {
  pageError.value = "";
  try {
    await Promise.all([loadKernelSchema(), loadFlags(), loadModels(), loadSystemInfo()]);
  } catch (error: unknown) {
    pageError.value = extractAxiosError(error, "Failed to refresh settings");
  }
}

async function saveField(field: SettingsSchemaField): Promise<void> {
  if (!field.save_path) return;
  savingFieldKey.value = field.key;
  pageError.value = "";
  try {
    await http.put(field.save_path, { value: kernelValues.value[field.key] ?? "" });
    await loadKernelSchema();
  } catch (error: unknown) {
    pageError.value = extractAxiosError(error, `Failed to save ${field.label}`);
  } finally {
    savingFieldKey.value = null;
  }
}

async function toggleFlag(key: string): Promise<void> {
  togglingFlag.value = key;
  pageError.value = "";
  try {
    await http.put(SETTINGS.flagToggle(key));
    await loadFlags();
  } catch (error: unknown) {
    pageError.value = extractAxiosError(error, `Failed to toggle ${key}`);
  } finally {
    togglingFlag.value = null;
  }
}

async function scanModels(): Promise<void> {
  scanningModels.value = true;
  pageError.value = "";
  try {
    await http.post(SETTINGS.aiModelsScan);
    await loadModels();
  } catch (error: unknown) {
    pageError.value = extractAxiosError(error, "Failed to scan models");
  } finally {
    scanningModels.value = false;
  }
}

async function selectModel(model: AIModel): Promise<void> {
  pageError.value = "";
  try {
    const capability = detectCapability(model);
    await http.put(SETTINGS.aiModelUpdate, {
      capability,
      model_id: model.id,
      provider: model.provider,
    });
    await loadModels();
  } catch (error: unknown) {
    pageError.value = extractAxiosError(error, `Failed to select ${model.id}`);
  }
}

onMounted(() => {
  void refreshAll();
});
</script>
