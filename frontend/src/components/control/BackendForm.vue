<template>
  <form
    class="card border border-base-300 bg-base-100 p-4 shadow-sm"
    @submit.prevent="submitForm"
  >
    <h2 class="text-lg font-medium">
      {{ title }}
    </h2>
    <div
      v-for="section in sections"
      :key="section.id"
      class="mt-4 space-y-3"
    >
      <div>
        <p class="text-xs uppercase tracking-[0.2em] text-base-content/50">
          {{ section.label }}
        </p>
        <p
          v-if="section.description"
          class="mt-1 text-sm text-base-content/65"
        >
          {{ section.description }}
        </p>
      </div>

      <div class="grid gap-3 md:grid-cols-2">
        <label
          v-for="field in section.fields"
          :key="`${section.id}:${field.key}`"
          class="form-control"
        >
          <span class="label-text">{{ field.label }}</span>

          <input
            v-if="field.input_type === 'text' || field.input_type === 'url'"
            v-model="state[field.key]"
            class="input input-bordered"
            :placeholder="field.placeholder ?? ''"
            :required="field.required"
            :type="field.input_type === 'url' ? 'url' : 'text'"
          >

          <input
            v-else-if="field.input_type === 'number'"
            v-model="state[field.key]"
            class="input input-bordered"
            :placeholder="field.placeholder ?? ''"
            :required="field.required"
            type="number"
          >

          <select
            v-else-if="field.input_type === 'select'"
            v-model="state[field.key]"
            class="select select-bordered"
            :required="field.required"
          >
            <option
              v-for="option in field.options"
              :key="`${field.key}:${option.value}`"
              :value="option.value"
            >
              {{ option.label }}
            </option>
          </select>

          <textarea
            v-else-if="field.input_type === 'json'"
            v-model="state[field.key]"
            class="textarea textarea-bordered min-h-28"
            :placeholder="field.placeholder ?? ''"
          />

          <input
            v-else-if="field.input_type === 'tags'"
            v-model="state[field.key]"
            class="input input-bordered"
            :placeholder="field.placeholder ?? ''"
          >

          <input
            v-else
            v-model="state[field.key]"
            class="input input-bordered"
            :placeholder="field.placeholder ?? ''"
            :required="field.required"
            type="text"
          >

          <span
            v-if="field.description"
            class="label-text-alt mt-1 text-base-content/55"
          >
            {{ field.description }}
          </span>
        </label>
      </div>
    </div>

    <button
      class="btn btn-primary mt-4 w-fit"
      :disabled="submitting"
      type="submit"
    >
      {{ submitLabel }}
    </button>
  </form>
</template>

<script setup lang="ts">
import { reactive, watch } from "vue";
import type { FormFieldSchema, FormSectionSchema } from "@/types/backendUi";

const props = defineProps<{
  title: string;
  sections: FormSectionSchema[];
  submitLabel: string;
  submitting?: boolean;
}>();

const emit = defineEmits<{
  submit: [payload: Record<string, unknown>];
  invalid: [message: string];
}>();

const state = reactive<Record<string, string>>({});

function applyDefaults(fields: FormFieldSchema[]): void {
  for (const field of fields) {
    const nextValue = field.value == null ? "" : String(field.value);
    if (!(field.key in state)) {
      state[field.key] = nextValue;
    }
  }
}

watch(
  () => props.sections,
  (sections) => {
    for (const section of sections) {
      applyDefaults(section.fields);
    }
  },
  { immediate: true, deep: true }
);

function parseFieldValue(field: FormFieldSchema, rawValue: string): unknown {
  const trimmed = rawValue.trim();
  if (!trimmed) {
    return field.input_type === "json" ? {} : undefined;
  }
  if (field.input_type === "number") {
    return Number(trimmed);
  }
  if (field.input_type === "json") {
    return JSON.parse(trimmed) as Record<string, unknown>;
  }
  if (field.input_type === "tags") {
    return trimmed.split(",").map((item) => item.trim()).filter(Boolean);
  }
  return trimmed;
}

function submitForm(): void {
  const payload: Record<string, unknown> = {};
  try {
    for (const section of props.sections) {
      for (const field of section.fields) {
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
    }
  } catch (error) {
    emit("invalid", error instanceof Error ? error.message : "Form payload is invalid");
    return;
  }
  emit("submit", payload);
}
</script>
