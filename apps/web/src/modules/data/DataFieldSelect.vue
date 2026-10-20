<script setup lang="ts">
withDefaults(
  defineProps<{
    modelValue: string | string[];
    columns: string[];
    multiple?: boolean;
    allowCreate?: boolean;
    placeholder?: string;
    limit?: number;
  }>(),
  { placeholder: "选择字段", limit: 100 },
);
const emit = defineEmits<{ "update:modelValue": [value: string | string[]] }>();
</script>
<template>
  <el-select
    :model-value="modelValue"
    :multiple="multiple"
    :multiple-limit="multiple ? limit : 0"
    filterable
    :allow-create="allowCreate"
    default-first-option
    :placeholder="placeholder"
    clearable
    @update:model-value="emit('update:modelValue', $event)"
  >
    <el-option
      v-for="column in columns"
      :key="column"
      :label="column"
      :value="column"
    />
  </el-select>
</template>
