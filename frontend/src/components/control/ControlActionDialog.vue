<template>
  <dialog
    class="modal"
    :class="{ 'modal-open': !!action }"
  >
    <div class="modal-box max-w-xl">
      <div class="space-y-4">
        <div>
          <p class="text-xs uppercase tracking-[0.22em] text-base-content/50">
            Backend Action
          </p>
          <h3 class="mt-2 text-2xl font-semibold">
            {{ action?.label ?? "Action" }}
          </h3>
          <p
            v-if="action?.confirmation"
            class="mt-2 text-sm text-base-content/70"
          >
            {{ action.confirmation }}
          </p>
        </div>

        <div
          v-if="action?.fields.length"
          class="space-y-4"
        >
          <label
            v-for="field in action.fields"
            :key="field.key"
            class="form-control"
          >
            <span class="label-text">{{ field.label }}</span>

            <input
              v-if="field.input_type === 'text'"
              v-model="state[field.key]"
              class="input input-bordered"
              :placeholder="field.placeholder ?? ''"
              :required="field.required"
              type="text"
            >

            <input
              v-else-if="field.input_type === 'number'"
              v-model="state[field.key]"
              class="input input-bordered"
              :placeholder="field.placeholder ?? ''"
              :required="field.required"
              type="number"
            >

            <textarea
              v-else-if="field.input_type === 'json'"
              v-model="state[field.key]"
              class="textarea textarea-bordered min-h-28"
              :placeholder="field.placeholder ?? ''"
            />

            <input
              v-else
              v-model="state[field.key]"
              class="input input-bordered"
              :placeholder="field.placeholder ?? ''"
              :required="field.required"
              type="text"
            >
          </label>
        </div>
      </div>

      <div class="modal-action">
        <button
          class="btn"
          type="button"
          @click="closeDialog"
        >
          Cancel
        </button>
        <button
          class="btn btn-primary"
          :disabled="submitting"
          type="button"
          @click="submitDialog"
        >
          {{ action?.label ?? "Run" }}
        </button>
      </div>
    </div>
    <form
      method="dialog"
      class="modal-backdrop"
      @click.prevent="closeDialog"
    >
      <button type="button">
        close
      </button>
    </form>
  </dialog>
</template>

<script setup lang="ts">
import { reactive, watch } from "vue";
import type { ControlAction } from "@/types/controlPlane";

const props = defineProps<{
  action: ControlAction | null;
  submitting?: boolean;
}>();

const emit = defineEmits<{
  close: [];
  invalid: [message: string];
  submit: [payload: Record<string, unknown>];
}>();

const state = reactive<Record<string, string>>({});

function closeDialog(): void {
  emit("close");
}

function hydrateFields(action: ControlAction | null): void {
  for (const key of Object.keys(state)) {
    state[key] = "";
  }
  if (!action) {
    return;
  }
  for (const field of action.fields) {
    state[field.key] = field.value == null ? "" : String(field.value);
  }
}

watch(
  () => props.action,
  (action) => {
    hydrateFields(action);
  },
  { immediate: true, deep: true }
);

function parseFieldValue(field: ControlAction["fields"][number], rawValue: string): unknown {
  const trimmed = rawValue.trim();
  if (!trimmed) {
    return undefined;
  }
  if (field.input_type === "number") {
    return Number(trimmed);
  }
  if (field.input_type === "json") {
    return JSON.parse(trimmed) as Record<string, unknown>;
  }
  return trimmed;
}

function submitDialog(): void {
  const action = props.action;
  if (!action) {
    return;
  }
  const payload: Record<string, unknown> = {};
  try {
    for (const field of action.fields) {
      const parsed = parseFieldValue(field, state[field.key] ?? "");
      if (parsed === undefined) {
        if (field.required) {
          emit("invalid", `${field.label} is required`);
          return;
        }
        continue;
      }
      payload[field.key] = parsed;
    }
  } catch (error) {
    emit("invalid", error instanceof Error ? error.message : "Action payload is invalid");
    return;
  }
  emit("submit", payload);
}
</script>
