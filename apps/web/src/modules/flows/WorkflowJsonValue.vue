<script setup lang="ts">
import { computed } from "vue";
const props = withDefaults(defineProps<{ value: unknown; depth?: number }>(), {
  depth: 0,
});
const complex = computed(
  () => props.value !== null && typeof props.value === "object",
);
const entries = computed(() =>
  Array.isArray(props.value)
    ? props.value.map((item, index) => [String(index + 1), item] as const)
    : Object.entries((props.value ?? {}) as Record<string, unknown>),
);
const scalar = computed(() =>
  props.value === undefined ? "—" : JSON.stringify(props.value),
);
</script>
<template>
  <span v-if="!complex" class="json-scalar">{{ scalar }}</span>
  <details v-else :open="depth < 1" class="json-value">
    <summary>
      {{ Array.isArray(value) ? "列表" : "对象" }} · {{ entries.length }} 项
    </summary>
    <dl v-if="depth < 8">
      <template v-for="[key, child] in entries" :key="key"
        ><dt>{{ key }}</dt>
        <dd><WorkflowJsonValue :value="child" :depth="depth + 1" /></dd
      ></template>
    </dl>
    <pre v-else>{{ JSON.stringify(value, null, 2) }}</pre>
  </details>
</template>
<style scoped>
.json-scalar {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.json-value {
  max-width: 100%;
}
.json-value > summary {
  cursor: pointer;
  color: var(--text-muted);
}
.json-value > dl {
  display: grid;
  grid-template-columns: minmax(80px, 140px) minmax(0, 1fr);
  gap: var(--sp-2);
  margin: var(--sp-2) 0;
}
.json-value dt {
  color: var(--text-muted);
  overflow-wrap: anywhere;
}
.json-value dd {
  margin: 0;
  min-width: 0;
}
.json-value pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
