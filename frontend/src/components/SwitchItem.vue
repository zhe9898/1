<template>
  <button
    class="btn btn-primary btn-sm"
    :disabled="cap.status !== 'online'"
    @click="$emit('toggle', serviceName)"
  >
    {{ cap.status === "online" ? "ON" : "OFF" }}
  </button>
</template>

<script setup lang="ts">
import type { Capability } from "@/types/capability";

defineProps<{
  serviceName: string;
  cap: Capability;
}>();

defineEmits<{
  toggle: [name: string];
}>();
</script>
