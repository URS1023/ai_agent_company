<script setup lang="ts">
import {
  newFilter,
  newAggregate,
  operationTypes,
  dataTypes,
  type OperationDraft,
  type Fields,
} from "./dataPreparation";
import DataFieldSelect from "./DataFieldSelect.vue";
import DataFilterCondition from "./DataFilterCondition.vue";
import AggregateDefinitionForm from "./AggregateDefinitionForm.vue";
import StructuredExpressionEditor from "./StructuredExpressionEditor.vue";
const props = defineProps<{
  modelValue: OperationDraft;
  columns: string[];
  index: number;
  errors: Fields;
  first: boolean;
  last: boolean;
  removable: boolean;
}>();
const emit = defineEmits<{
  "update:modelValue": [value: OperationDraft];
  move: [offset: number];
  remove: [];
}>();
function update(key: keyof OperationDraft, value: unknown) {
  emit("update:modelValue", { ...props.modelValue, [key]: value });
}
function updateList(
  key: "conditions" | "aggregations",
  index: number,
  value: unknown,
) {
  const values: any[] = [...props.modelValue[key]];
  values[index] = value;
  update(key, values);
}
const path = () => `operations.${props.index}`;
</script>
<template>
  <section class="step-panel">
    <div class="step-heading">
      <strong>步骤 {{ index + 1 }}</strong
      ><el-select
        :model-value="modelValue.type"
        aria-label="处理方式"
        @update:model-value="update('type', $event)"
        ><el-option
          v-for="item in operationTypes"
          :key="item.value"
          :label="item.label"
          :value="item.value" /></el-select
      ><span class="spacer" /><el-button
        link
        :disabled="first"
        @click="emit('move', -1)"
        >上移</el-button
      ><el-button link :disabled="last" @click="emit('move', 1)">下移</el-button
      ><el-button
        link
        type="danger"
        :disabled="!removable"
        @click="emit('remove')"
        >删除</el-button
      >
    </div>
    <el-form-item
      v-if="['select', 'deduplicate'].includes(modelValue.type)"
      label="字段"
      :error="errors[`${path()}.fields`]"
      ><DataFieldSelect
        :model-value="modelValue.fields"
        :columns="columns"
        multiple
        @update:model-value="update('fields', $event)"
    /></el-form-item>
    <div
      v-if="['drop_null', 'rename', 'cast'].includes(modelValue.type)"
      class="form-grid"
    >
      <el-form-item label="字段" :error="errors[`${path()}.field`]"
        ><DataFieldSelect
          :model-value="modelValue.field"
          :columns="columns"
          @update:model-value="update('field', $event)"
      /></el-form-item>
      <el-form-item
        v-if="modelValue.type !== 'drop_null'"
        :label="
          modelValue.type === 'cast' ? '输出字段（留空覆盖原字段）' : '新字段名'
        "
        :error="errors[`${path()}.output_field`]"
        ><el-input
          :model-value="modelValue.output_field"
          @update:model-value="update('output_field', $event)"
      /></el-form-item>
      <el-form-item
        v-if="modelValue.type === 'cast'"
        label="目标类型"
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
    </div>
    <template v-if="modelValue.type === 'filter'">
      <el-form-item label="匹配方式"
        ><el-radio-group
          :model-value="modelValue.match"
          @update:model-value="update('match', $event)"
          ><el-radio-button value="all">全部满足</el-radio-button
          ><el-radio-button value="any"
            >任一满足</el-radio-button
          ></el-radio-group
        ></el-form-item
      >
      <div
        v-for="(condition, i) in modelValue.conditions"
        :key="condition.key"
        class="detail-row"
      >
        <DataFilterCondition
          :model-value="condition"
          :columns="columns"
          :path="`${path()}.conditions.${i}`"
          :errors="errors"
          @update:model-value="updateList('conditions', i, $event)"
        /><el-button
          link
          type="danger"
          :disabled="modelValue.conditions.length <= 1"
          @click="
            update(
              'conditions',
              modelValue.conditions.filter((_, j) => i !== j),
            )
          "
          >移除</el-button
        >
      </div>
      <el-button
        :disabled="modelValue.conditions.length >= 20"
        @click="update('conditions', [...modelValue.conditions, newFilter()])"
        >添加条件</el-button
      >
    </template>
    <template v-if="modelValue.type === 'derive'">
      <el-form-item label="结果字段名" :error="errors[`${path()}.output_field`]"
        ><el-input
          :model-value="modelValue.output_field"
          @update:model-value="update('output_field', $event)"
      /></el-form-item>
      <el-form-item label="计算" :error="errors[`${path()}.expression`]"
        ><StructuredExpressionEditor
          :model-value="modelValue.expression"
          :columns="columns"
          :path="`${path()}.expression`"
          :errors="errors"
          root
          @update:model-value="update('expression', $event)"
      /></el-form-item>
    </template>
    <template v-if="modelValue.type === 'aggregate'">
      <el-form-item label="分组字段" :error="errors[`${path()}.group_by`]"
        ><DataFieldSelect
          :model-value="modelValue.group_by"
          :columns="columns"
          :limit="10"
          multiple
          @update:model-value="update('group_by', $event)"
      /></el-form-item>
      <div
        v-for="(aggregate, i) in modelValue.aggregations"
        :key="i"
        class="aggregate-row"
      >
        <AggregateDefinitionForm
          :model-value="aggregate"
          :columns="columns"
          :path="`${path()}.aggregations.${i}`"
          :errors="errors"
          @update:model-value="updateList('aggregations', i, $event)"
        /><el-button
          link
          type="danger"
          :disabled="modelValue.aggregations.length <= 1"
          @click="
            update(
              'aggregations',
              modelValue.aggregations.filter((_, j) => i !== j),
            )
          "
          >移除汇总项</el-button
        >
      </div>
      <el-button
        :disabled="modelValue.aggregations.length >= 20"
        @click="
          update('aggregations', [...modelValue.aggregations, newAggregate()])
        "
        >添加汇总项</el-button
      >
    </template>
    <el-form-item
      v-if="['derive', 'cast'].includes(modelValue.type)"
      label="无效值处理"
      :error="errors[`${path()}.on_error`]"
      ><el-radio-group
        :model-value="modelValue.on_error"
        @update:model-value="update('on_error', $event)"
        ><el-radio value="fail">停止并定位错误</el-radio
        ><el-radio value="null">保留为空值</el-radio></el-radio-group
      ></el-form-item
    >
    <el-form-item
      v-if="
        errors[path()] ||
        errors[`${path()}.conditions`] ||
        errors[`${path()}.aggregations`]
      "
      :error="
        errors[path()] ||
        errors[`${path()}.conditions`] ||
        errors[`${path()}.aggregations`]
      "
    />
  </section>
</template>
<style scoped>
.step-panel {
  padding: 16px;
  border: 1px solid #e5eaf2;
  border-radius: 2px;
  margin-bottom: 15px;
}
.step-heading {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}
.step-heading > .el-select {
  width: 190px;
}
.detail-row {
  display: flex;
  gap: 12px;
  align-items: flex-start;
}
.detail-row > :first-child {
  flex: 1;
  min-width: 0;
}
.aggregate-row {
  padding: 12px;
  background: #f7f9fc;
  margin-bottom: 12px;
}
</style>
