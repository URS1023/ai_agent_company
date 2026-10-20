<script setup lang="ts">
import { computed, ref, watch } from "vue";
import {
  graphFields,
  initialGraphInputs,
  validateGraphInputs,
  type GraphDefinition,
} from "./graphInputs";
import SchemaInput from "./SchemaInput.vue";
const props = defineProps<{
  definition: GraphDefinition;
  disabled: boolean;
  errors: Record<string, string>;
}>();
const emit = defineEmits<{ submit: [inputs: Record<string, unknown>] }>();
const values = ref<Record<string, unknown>>({}),
  localErrors = ref<Record<string, string>>({});
const prepared = computed(() => {
  try {
    return { fields: graphFields(props.definition), error: "" };
  } catch (failure) {
    return {
      fields: [],
      error: failure instanceof Error ? failure.message : "无法读取输入字段",
    };
  }
});
const errors = computed(() => ({
  ...props.errors,
  ...localErrors.value,
  ...Object.fromEntries(
    prepared.value.fields
      .filter((field) => field.issue)
      .map((field) => [`inputs.${field.key}`, field.issue!]),
  ),
}));
const unsupported = computed(
  () =>
    Boolean(prepared.value.error) ||
    prepared.value.fields.some((field) => field.issue),
);
watch(
  () => props.definition,
  () => {
    values.value = initialGraphInputs(prepared.value.fields);
    localErrors.value = {};
  },
  { immediate: true },
);
function update(key: string, value: unknown) {
  values.value = { ...values.value, [key]: value };
  localErrors.value = {};
}
function submit() {
  if (props.disabled || unsupported.value) return;
  const result = validateGraphInputs(prepared.value.fields, values.value);
  localErrors.value = result.errors;
  if (!Object.keys(result.errors).length) emit("submit", result.inputs);
}
</script>
<template>
  <el-form label-position="top" @submit.prevent="submit">
    <el-alert
      v-if="prepared.error || errors.inputs"
      :title="prepared.error || errors.inputs"
      type="error"
      :closable="false"
    />
    <template v-for="field in prepared.fields" :key="field.key"
      ><el-form-item
        v-if="field.issue"
        :label="field.label"
        :error="field.issue" /><SchemaInput
        v-else
        :schema="field.schema"
        :label="field.label"
        :path="`inputs.${field.key}`"
        :required="field.required"
        :model-value="
          Object.prototype.hasOwnProperty.call(values, field.key)
            ? values[field.key]
            : undefined
        "
        :errors="errors"
        :disabled="disabled"
        @update:model-value="update(field.key, $event)"
    /></template>
    <el-button
      type="primary"
      native-type="submit"
      :disabled="disabled || unsupported"
      >运行</el-button
    >
  </el-form>
</template>
