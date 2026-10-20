<script setup lang="ts">
import { type MetricDraft, type Fields } from "./dataPreparation";
import DataFieldSelect from "./DataFieldSelect.vue";
import AggregateDefinitionForm from "./AggregateDefinitionForm.vue";
const props = defineProps<{
  modelValue: MetricDraft;
  columns: string[];
  errors: Fields;
}>();
const emit = defineEmits<{ "update:modelValue": [value: MetricDraft] }>();
function update(key: keyof MetricDraft, value: unknown) {
  emit("update:modelValue", { ...props.modelValue, [key]: value });
}
</script>
<template>
  <el-form-item label="口径类型" :error="errors['definition.kind']"
    ><el-radio-group
      :model-value="modelValue.kind"
      @update:model-value="update('kind', $event)"
      ><el-radio-button value="aggregate">汇总指标</el-radio-button
      ><el-radio-button value="ratio">比率指标</el-radio-button></el-radio-group
    ></el-form-item
  >
  <div class="form-grid">
    <el-form-item label="分组字段" :error="errors['definition.group_by']"
      ><DataFieldSelect
        :model-value="modelValue.group_by"
        :columns="columns"
        :limit="10"
        multiple
        allow-create
        @update:model-value="update('group_by', $event)" /></el-form-item
    ><el-form-item label="结果单位" :error="errors['definition.unit']"
      ><el-input
        :model-value="modelValue.unit"
        @update:model-value="update('unit', $event)"
    /></el-form-item>
  </div>
  <AggregateDefinitionForm
    v-if="modelValue.kind === 'aggregate'"
    :model-value="modelValue.aggregation"
    :columns="columns"
    path="definition.aggregation"
    fixed-name
    :errors="errors"
    allow-create
    @update:model-value="update('aggregation', $event)"
  />
  <template v-else
    ><div class="metric-section">
      <h3>分子</h3>
      <AggregateDefinitionForm
        :model-value="modelValue.numerator"
        :columns="columns"
        path="definition.numerator"
        fixed-name
        :errors="errors"
        allow-create
        @update:model-value="update('numerator', $event)"
      />
    </div>
    <div class="metric-section">
      <h3>分母</h3>
      <AggregateDefinitionForm
        :model-value="modelValue.denominator"
        :columns="columns"
        path="definition.denominator"
        fixed-name
        :errors="errors"
        allow-create
        @update:model-value="update('denominator', $event)"
      />
    </div>
    <el-form-item label="结果倍数" :error="errors['definition.scale']"
      ><el-input
        :model-value="modelValue.scale"
        inputmode="decimal"
        @update:model-value="update('scale', $event)" /></el-form-item
  ></template>
  <el-form-item v-if="errors.definition" :error="errors.definition" />
</template>
<style scoped>
.metric-section {
  border-top: 1px solid #e5eaf2;
  padding-top: 15px;
  margin-top: 10px;
}
.metric-section h3 {
  margin: 0 0 15px;
  font-size: 16px;
}
</style>
