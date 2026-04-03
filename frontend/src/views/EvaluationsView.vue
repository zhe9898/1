<template>
  <section class="mx-auto max-w-6xl space-y-4 px-6 py-8">
    <header class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <p class="text-sm uppercase tracking-[0.25em] text-base-content/60">
          Gateway Control Plane
        </p>
        <h1 class="mt-2 text-3xl font-semibold">
          {{ evalSchema?.title ?? "Software Evaluations" }}
        </h1>
        <p
          v-if="evalSchema?.description"
          class="mt-2 max-w-3xl text-sm text-base-content/70"
        >
          {{ evalSchema.description }}
        </p>
      </div>
      <button
        class="btn btn-sm btn-primary"
        :disabled="store.loading"
        @click="refreshNow"
      >
        Refresh
      </button>
    </header>

    <div
      v-if="store.error"
      class="alert alert-error"
    >
      <span>{{ store.error }}</span>
    </div>

    <BackendForm
      v-if="evalSchema"
      title="Submit Evaluation"
      :sections="evalSchema.sections"
      :submit-label="evalSchema.submit_action?.label ?? 'Submit Evaluation'"
      :submitting="store.submitting"
      @submit="submitCreate"
      @invalid="handleFormError"
    />
    <div
      v-else
      class="card border border-base-300 bg-base-100 p-4 shadow-sm"
    >
      <h2 class="text-lg font-medium">
        Submit Evaluation
      </h2>
      <p class="mt-3 text-sm text-base-content/65">
        Loading evaluation schema…
      </p>
    </div>

    <div
      v-if="!store.loading && store.items.length === 0"
      class="rounded-3xl border border-base-300 bg-base-100 p-6 text-base-content/60"
    >
      {{ evalSchema?.empty_state ?? "No evaluations have been submitted yet." }}
    </div>

    <div class="grid gap-4 lg:grid-cols-2">
      <article
        v-for="item in store.items"
        :key="item.evaluation_id"
        class="card border border-base-300 bg-base-100 shadow-sm"
      >
        <div class="card-body gap-4">
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 class="card-title text-xl">
                {{ item.software_id }}
              </h2>
              <p class="font-mono text-xs text-base-content/50">
                {{ item.evaluation_id }}
              </p>
            </div>
            <span
              class="badge"
              :class="badgeClassFromTone(item.status_view.tone)"
            >
              {{ item.status_view.label }}
            </span>
          </div>

          <div class="grid gap-3 md:grid-cols-2">
            <div class="rounded-2xl border border-base-300 bg-base-200/40 p-4">
              <p class="text-xs uppercase tracking-[0.2em] text-base-content/50">
                Details
              </p>
              <p class="mt-2 text-sm">
                Branch: {{ item.branch }}
              </p>
              <p class="mt-1 text-sm">
                Category: {{ item.category }}
              </p>
              <p class="mt-1 text-sm">
                Evaluator: {{ item.evaluator || "-" }}
              </p>
              <p class="mt-1 text-sm">
                Created: {{ formatTs(item.created_at) }}
              </p>
            </div>

            <div class="rounded-2xl border border-base-300 bg-base-200/40 p-4">
              <p class="text-xs uppercase tracking-[0.2em] text-base-content/50">
                Rating
              </p>
              <div class="mt-2 flex items-center gap-1">
                <span
                  v-for="star in 5"
                  :key="star"
                  class="text-xl"
                  :class="star <= item.rating ? 'text-warning' : 'text-base-content/20'"
                >★</span>
                <span class="ml-2 text-sm font-semibold">{{ item.rating }}/5</span>
              </div>
              <p
                v-if="item.comment"
                class="mt-2 text-sm italic text-base-content/70"
              >
                "{{ item.comment }}"
              </p>
              <p
                v-else
                class="mt-2 text-sm text-base-content/40"
              >
                No comment
              </p>
            </div>
          </div>

          <div class="flex flex-wrap gap-2">
            <button
              v-for="action in item.actions"
              :key="`${item.evaluation_id}:${action.key}`"
              class="btn btn-sm"
              :class="action.key === 'delete' ? 'btn-error btn-outline' : ''"
              :disabled="store.submitting || !action.enabled"
              :title="action.reason ?? ''"
              @click="executeAction(item.evaluation_id, action)"
            >
              {{ action.label }}
            </button>
          </div>
        </div>
      </article>
    </div>

    <ControlActionDialog
      :action="pendingAction"
      :submitting="store.submitting"
      @close="closePendingAction"
      @invalid="handleFormError"
      @submit="submitAction"
    />
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import BackendForm from "@/components/control/BackendForm.vue";
import ControlActionDialog from "@/components/control/ControlActionDialog.vue";
import { useEvaluationsStore } from "@/stores/evaluations";
import { badgeClassFromTone } from "@/utils/statusView";
import type { ControlAction } from "@/types/controlPlane";
import type { ResourceSchema } from "@/types/backendUi";

const store = useEvaluationsStore();
const route = useRoute();
const pendingAction = ref<ControlAction | null>(null);
const pendingEvaluationId = ref<string | null>(null);

const evalSchema = computed<ResourceSchema | null>(() => store.schema);

const listQueryParams = computed<Record<string, unknown>>(() => {
  const q = route.query;
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(q)) {
    if (typeof v === "string" && v) out[k] = v;
  }
  return out;
});

function formatTs(ts: string): string {
  if (!ts) return "-";
  return new Date(ts).toLocaleString();
}

function refreshNow(): void {
  void store.fetchEvaluations(listQueryParams.value);
}

function handleFormError(message: string): void {
  store.error = message;
}

async function submitCreate(payload: Record<string, unknown>): Promise<void> {
  await store.createEvaluation({
    evaluation_id: typeof payload.evaluation_id === "string" ? payload.evaluation_id : "",
    software_id: typeof payload.software_id === "string" ? payload.software_id : "",
    branch: typeof payload.branch === "string" && payload.branch ? payload.branch : "main",
    rating: typeof payload.rating === "number" ? payload.rating : Number(payload.rating ?? 0),
    category:
      typeof payload.category === "string" && payload.category ? payload.category : "general",
    comment:
      typeof payload.comment === "string" && payload.comment ? payload.comment : null,
  });
}

function executeAction(evaluationId: string, action: ControlAction): void {
  if (!action.enabled) return;
  pendingEvaluationId.value = evaluationId;
  pendingAction.value = action;
}

function closePendingAction(): void {
  pendingAction.value = null;
  pendingEvaluationId.value = null;
}

async function submitAction(_payload: Record<string, unknown>): Promise<void> {
  const evaluationId = pendingEvaluationId.value;
  const action = pendingAction.value;
  if (!evaluationId || !action) return;
  if (action.key === "delete") {
    await store.deleteEvaluation(evaluationId);
  }
  closePendingAction();
}

watch(
  () => route.query,
  () => {
    void store.fetchEvaluations(listQueryParams.value);
  }
);

onMounted(async () => {
  await store.fetchSchema();
  await store.fetchEvaluations(listQueryParams.value);
});
</script>
