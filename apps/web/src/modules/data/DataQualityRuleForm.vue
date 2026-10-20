<script setup lang="ts">
import {
  dataTypes,
  qualityTypes,
  comparisons,
  type QualityDraft,
  type Fields,
} from "./dataPreparation";
import DataFieldSelect from "./DataFieldSelect.vue";
import DataScalarInput from "./DataScalarInput.vue";
const props = defineProps<{
  modelValue: QualityDraft;
  columns: string[];
  index: number;
  errors: Fields;
}>();
const emit = defineEmits<{
  "update:modelValue": [value: QualityDraft];
  remove: [];
}>();
function update(key: keyof QualityDraft, value: unknown) {
  emit("update:modelValue", {
    ...props.modelValue,
    [key]: value,
    ...(key === "data_type" ? { values: [] } : {}),
  });
}
const path = () => `rules.${props.index}`;
</script>
<template>
  <section class="quality-rule">
    <div class="rule-heading">
      <strong>规则 {{ index + 1 }}</strong
      ><span class="spacer" /><el-button
        link
        type="danger"
        @click="emit('remove')"
        >删除</el-button
      >
    </div>
    <div class="form-grid">
      <el-form-item label="名称（可选）" :error="errors[`${path()}.name`]"
        ><el-input
          :model-value="modelValue.name"
          @update:model-value="update('name', $event)"
      /></el-form-item>
      <el-form-item label="检查方式" :error="errors[`${path()}.type`]"
        ><el-select
          :model-value="modelValue.type"
          @update:model-value="update('type', $event)"
          ><el-option
            v-for="item in qualityTypes"
            :key="item.value"
            :value="item.value"
            :label="item.label" /></el-select
      ></el-form-item>
      <el-form-item label="字段" :error="errors[`${path()}.field`]"
        ><DataFieldSelect
          :model-value="modelValue.field"
          :columns="columns"
          @update:model-value="update('field', $event)"
      /></el-form-item>
      <el-form-item
        v-if="['enum', 'type', 'compare'].includes(modelValue.type)"
        label="数据类型"
        :error="errors[`${path()}.data_type`]"
        ><el-select
          :model-value="modelValue.data_type"
          @update:model-value="update('data_type', $event)"
          ><el-option
            v-for="item in dataTypes"
            :key="item.value"
            :value="item.value"
            :label="item.label" /></el-select
      ></el-form-item>
      <template v-if="modelValue.type === 'range'"
        ><el-form-item label="最小值" :error="errors[`${path()}.minimum`]"
          ><el-input
            :model-value="modelValue.minimum"
            inputmode="decimal"
            @update:model-value="update('minimum', $event)" /></el-form-item
        ><el-form-item label="最大值" :error="errors[`${path()}.maximum`]"
          ><el-input
            :model-value="modelValue.maximum"
            inputmode="decimal"
            @update:model-value="update('maximum', $event)" /></el-form-item
      ></template>
      <el-form-item
        v-if="modelValue.type === 'enum'"
        label="允许值"
        :error="errors[`${path()}.values`]"
        ><DataScalarInput
          :model-value="modelValue.values"
          :data-type="modelValue.data_type"
          multiple
          @update:model-value="update('values', $event)"
      /></el-form-item>
      <template v-if="modelValue.type === 'length'"
        ><el-form-item label="最短长度" :error="errors[`${path()}.min_length`]"
          ><el-input-number
            :model-value="modelValue.min_length"
            :min="0"
            :precision="0"
            @update:model-value="update('min_length', $event)" /></el-form-item
        ><el-form-item label="最长长度" :error="errors[`${path()}.max_length`]"
          ><el-input-number
            :model-value="modelValue.max_length"
            :min="0"
            :precision="0"
            @update:model-value="update('max_length', $event)" /></el-form-item
      ></template>
      <template v-if="modelValue.type === 'compare'"
        ><el-form-item label="比较方式" :error="errors[`${path()}.operator`]"
          ><el-select
            :model-value="modelValue.operator"
            @update:model-value="update('operator', $event)"
            ><el-option
              v-for="item in comparisons.filter((x) =>
                ['eq', 'ne', 'gt', 'gte', 'lt', 'lte'].includes(x.value),
              )"
              :key="item.value"
              :value="item.value"
              :label="item.label" /></el-select></el-form-item
        ><el-form-item label="另一字段" :error="errors[`${path()}.other_field`]"
          ><DataFieldSelect
            :model-value="modelValue.other_field"
            :columns="columns"
            @update:model-value="update('other_field', $event)" /></el-form-item
      ></template>
      <el-form-item
        v-if="!['required', 'unique'].includes(modelValue.type)"
        label="空值"
        ><el-switch
          :model-value="modelValue.allow_missing"
          active-text="跳过空值"
          inactive-text="检查空值"
          @update:model-value="update('allow_missing', $event)"
      /></el-form-item>
    </div>
    <el-form-item v-if="errors[path()]" :error="errors[path()]" />
  </section>
</template>
<style scoped>
.quality-rule {
  padding: 15px;
  border: 1px solid var(--line);
  border-radius: 2px;
  margin-bottom: 15px;
}
.rule-heading {
  display: flex;
  align-items: center;
  margin-bottom: 12px;
}
</style>
