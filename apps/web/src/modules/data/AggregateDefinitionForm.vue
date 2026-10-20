<script setup lang="ts">
import {
  aggregations,
  type AggregateDraft,
  type Fields,
} from "./dataPreparation";
import DataFieldSelect from "./DataFieldSelect.vue";
const props = withDefaults(
  defineProps<{
    modelValue: AggregateDraft;
    columns: string[];
    path: string;
    errors: Fields;
    allowCreate?: boolean;
    fixedName?: boolean;
  }>(),
  { allowCreate: false },
);
const emit = defineEmits<{ "update:modelValue": [value: AggregateDraft] }>();
function update(key: keyof AggregateDraft, value: unknown) {
  emit("update:modelValue", { ...props.modelValue, [key]: value });
}
</script>
<template>
  <div class="form-grid">
    <el-form-item
      v-if="!fixedName"
      label="结果字段"
      :error="errors[`${path}.name`]"
      ><el-input
        :model-value="modelValue.name"
        @update:model-value="update('name', $event)"
    /></el-form-item>
    <el-form-item label="汇总方式" :error="errors[`${path}.function`]"
      ><el-select
        :model-value="modelValue.function"
        @update:model-value="update('function', $event)"
        ><el-option
          v-for="item in aggregations"
          :key="item.value"
          :value="item.value"
          :label="item.label" /></el-select
    ></el-form-item>
    <el-form-item
      :label="modelValue.function === 'count' ? '计数字段（可选）' : '数值字段'"
      :error="errors[`${path}.field`]"
      ><DataFieldSelect
        :model-value="modelValue.field"
        :columns="columns"
        :allow-create="allowCreate"
        @update:model-value="update('field', $event)"
    /></el-form-item>
    <el-form-item label="比较类型" :error="errors[`${path}.data_type`]"
      ><el-select
        :model-value="modelValue.data_type"
        @update:model-value="update('data_type', $event)"
        ><el-option value="decimal" label="数值" /><el-option
          value="text"
          label="文本" /></el-select
    ></el-form-item>
    <el-form-item label="单位字段（可选）" :error="errors[`${path}.unit_field`]"
      ><DataFieldSelect
        :model-value="modelValue.unit_field"
        :columns="columns"
        :allow-create="allowCreate"
        @update:model-value="update('unit_field', $event)"
    /></el-form-item>
  </div>
</template>
