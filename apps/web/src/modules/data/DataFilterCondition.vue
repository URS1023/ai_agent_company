<script setup lang="ts">
import { computed } from "vue";
import {
  dataTypes,
  comparisons,
  type FilterDraft,
  type Fields,
} from "./dataPreparation";
import DataFieldSelect from "./DataFieldSelect.vue";
import DataScalarInput from "./DataScalarInput.vue";
const props = defineProps<{
  modelValue: FilterDraft;
  columns: string[];
  path: string;
  errors: Fields;
}>();
const emit = defineEmits<{ "update:modelValue": [value: FilterDraft] }>();
const options = computed(() =>
  comparisons.filter(
    (x) =>
      (x.value !== "contains" || props.modelValue.data_type === "text") &&
      (props.modelValue.data_type !== "boolean" ||
        ["eq", "ne", "in", "not_in", "is_null", "not_null"].includes(x.value)),
  ),
);
function update(key: keyof FilterDraft, value: unknown) {
  const draft = { ...props.modelValue, [key]: value };
  if (key === "operator")
    draft.value = ["in", "not_in"].includes(String(value)) ? [] : "";
  if (key === "data_type") {
    draft.value = ["in", "not_in"].includes(draft.operator)
      ? []
      : value === "boolean"
        ? false
        : "";
    if (
      (value !== "text" && draft.operator === "contains") ||
      (value === "boolean" &&
        !["eq", "ne", "in", "not_in", "is_null", "not_null"].includes(
          draft.operator,
        ))
    )
      draft.operator = "eq";
  }
  emit("update:modelValue", draft);
}
</script>
<template>
  <div class="condition-grid">
    <el-form-item label="字段" :error="errors[`${path}.field`]"
      ><DataFieldSelect
        :model-value="modelValue.field"
        :columns="columns"
        @update:model-value="update('field', $event)"
    /></el-form-item>
    <el-form-item label="类型" :error="errors[`${path}.data_type`]"
      ><el-select
        :model-value="modelValue.data_type"
        @update:model-value="update('data_type', $event)"
        ><el-option
          v-for="item in dataTypes"
          :key="item.value"
          :label="item.label"
          :value="item.value" /></el-select
    ></el-form-item>
    <el-form-item label="比较" :error="errors[`${path}.operator`]"
      ><el-select
        :model-value="modelValue.operator"
        @update:model-value="update('operator', $event)"
        ><el-option
          v-for="item in options"
          :key="item.value"
          :label="item.label"
          :value="item.value" /></el-select
    ></el-form-item>
    <el-form-item
      v-if="!['is_null', 'not_null'].includes(modelValue.operator)"
      label="比较值"
      :error="errors[`${path}.value`]"
      ><DataScalarInput
        :model-value="modelValue.value"
        :data-type="modelValue.data_type"
        :multiple="['in', 'not_in'].includes(modelValue.operator)"
        @update:model-value="update('value', $event)"
    /></el-form-item>
  </div>
</template>
<style scoped>
.condition-grid {
  display: grid;
  grid-template-columns: minmax(120px, 1fr) 110px 130px minmax(150px, 1.2fr);
  gap: 12px;
}
</style>
