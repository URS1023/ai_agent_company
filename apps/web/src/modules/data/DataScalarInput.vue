<script setup lang="ts">
import { computed } from "vue";
import type { DataType, Scalar } from "./dataPreparation";
const props = withDefaults(
  defineProps<{
    modelValue: Scalar | Scalar[];
    dataType?: DataType;
    multiple?: boolean;
  }>(),
  { dataType: "text" },
);
const emit = defineEmits<{ "update:modelValue": [value: Scalar | Scalar[]] }>();
const text = computed(() =>
  typeof props.modelValue === "string" ? props.modelValue : "",
);
const items = computed(() =>
  Array.isArray(props.modelValue) ? props.modelValue : [],
);
const placeholder = computed(
  () =>
    ({
      date: "2026-10-21",
      datetime: "2026-10-21T08:00:00+08:00",
      decimal: "输入数值",
      integer: "输入整数",
      text: "输入文字",
      boolean: "选择值",
    })[props.dataType],
);
</script>
<template>
  <el-select
    v-if="multiple"
    :model-value="items"
    multiple
    :multiple-limit="100"
    filterable
    :allow-create="dataType !== 'boolean'"
    default-first-option
    :placeholder="placeholder"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <template v-if="dataType === 'boolean'"
      ><el-option :value="true" label="是" /><el-option
        :value="false"
        label="否"
    /></template>
    <el-option
      v-for="(item, index) in items.filter(
        (x) => typeof x !== 'boolean' && x !== null,
      )"
      :key="index"
      :value="item"
      :label="String(item)"
    />
  </el-select>
  <el-select
    v-else-if="dataType === 'boolean'"
    :model-value="typeof modelValue === 'boolean' ? modelValue : undefined"
    @update:model-value="emit('update:modelValue', $event)"
    ><el-option :value="true" label="是" /><el-option :value="false" label="否"
  /></el-select>
  <el-input
    v-else
    :model-value="text"
    :placeholder="placeholder"
    :inputmode="['decimal', 'integer'].includes(dataType) ? 'decimal' : 'text'"
    @update:model-value="emit('update:modelValue', $event)"
  />
</template>
