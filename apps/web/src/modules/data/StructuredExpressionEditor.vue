<script setup lang="ts">
import { computed } from "vue";
import {
  expressionOperations,
  dataTypes,
  newExpression,
  type ExpressionDraft,
  type Fields,
  type DataType,
} from "./dataPreparation";
import DataFieldSelect from "./DataFieldSelect.vue";
import DataScalarInput from "./DataScalarInput.vue";
const props = withDefaults(
  defineProps<{
    modelValue: ExpressionDraft;
    columns: string[];
    path: string;
    errors: Fields;
    depth?: number;
    root?: boolean;
  }>(),
  { depth: 1 },
);
const emit = defineEmits<{ "update:modelValue": [value: ExpressionDraft] }>();
function update(key: keyof ExpressionDraft, value: unknown) {
  emit("update:modelValue", { ...props.modelValue, [key]: value });
}
function changeKind(kind: ExpressionDraft["kind"]) {
  emit("update:modelValue", newExpression(kind));
}
const fixedArguments = computed(() =>
  props.modelValue.op === "round"
    ? 1
    : ["add", "subtract", "multiply", "divide"].includes(props.modelValue.op)
      ? 2
      : 0,
);
function changeOperation(op: string) {
  const count =
    op === "round"
      ? 1
      : ["add", "subtract", "multiply", "divide"].includes(op)
        ? 2
        : Math.max(1, props.modelValue.args.length);
  const args = Array.from(
    { length: count },
    (_, i) => props.modelValue.args[i] ?? newExpression("field"),
  );
  emit("update:modelValue", { ...props.modelValue, op, args });
}
function changeLiteralType(literal_type: DataType) {
  emit("update:modelValue", {
    ...props.modelValue,
    literal_type,
    value: literal_type === "boolean" ? false : "",
  });
}
function updateArg(index: number, node: ExpressionDraft) {
  const args = [...props.modelValue.args];
  args[index] = node;
  update("args", args);
}
function removeArg(index: number) {
  update(
    "args",
    props.modelValue.args.filter((_, i) => index !== i),
  );
}
</script>
<template>
  <div class="expression-node">
    <div class="expression-head">
      <el-select
        v-if="!root"
        :model-value="modelValue.kind"
        aria-label="计算项类型"
        @update:model-value="changeKind"
        ><el-option value="field" label="字段" /><el-option
          value="literal"
          label="固定值" /><el-option value="null" label="空值" /><el-option
          v-if="depth < 8"
          value="expression"
          label="计算"
      /></el-select>
      <el-form-item
        v-if="modelValue.kind === 'field'"
        :error="errors[`${path}.field`]"
        ><DataFieldSelect
          :model-value="modelValue.field"
          :columns="columns"
          @update:model-value="update('field', $event)"
      /></el-form-item>
      <template v-else-if="modelValue.kind === 'literal'">
        <el-select
          :model-value="modelValue.literal_type"
          aria-label="固定值类型"
          @update:model-value="changeLiteralType"
          ><el-option
            v-for="item in dataTypes"
            :key="item.value"
            :value="item.value"
            :label="item.label"
        /></el-select>
        <el-form-item :error="errors[`${path}.value`]"
          ><DataScalarInput
            :model-value="modelValue.value"
            :data-type="modelValue.literal_type"
            @update:model-value="update('value', $event)"
        /></el-form-item>
      </template>
      <template v-else-if="modelValue.kind === 'expression'">
        <el-select
          :model-value="modelValue.op"
          aria-label="计算方式"
          @update:model-value="changeOperation"
          ><el-option
            v-for="item in expressionOperations"
            :key="item.value"
            :value="item.value"
            :label="item.label"
        /></el-select>
        <el-input-number
          v-if="modelValue.op === 'round'"
          :model-value="modelValue.digits"
          :min="0"
          :max="18"
          aria-label="小数位数"
          @update:model-value="update('digits', $event ?? 0)"
        />
      </template>
    </div>
    <el-form-item
      v-if="errors[path] || errors[`${path}.args`]"
      :error="errors[path] || errors[`${path}.args`]"
    />
    <div v-if="modelValue.kind === 'expression'" class="expression-args">
      <div
        v-for="(arg, index) in modelValue.args"
        :key="index"
        class="expression-arg"
      >
        <StructuredExpressionEditor
          :model-value="arg"
          :columns="columns"
          :path="`${path}.args.${index}`"
          :errors="errors"
          :depth="depth + 1"
          @update:model-value="updateArg(index, $event)"
        />
        <el-button
          v-if="!fixedArguments"
          link
          type="danger"
          :disabled="modelValue.args.length <= 1"
          @click="removeArg(index)"
          >移除</el-button
        >
      </div>
      <el-button
        v-if="!fixedArguments"
        :disabled="modelValue.args.length >= 10"
        @click="update('args', [...modelValue.args, newExpression('field')])"
        >添加计算项</el-button
      >
    </div>
  </div>
</template>
<style scoped>
.expression-node {
  min-width: 0;
  flex: 1;
}
.expression-head {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}
.expression-head > .el-select {
  width: 140px;
  flex-shrink: 0;
}
.expression-head > .el-form-item {
  flex: 1;
  margin-bottom: 8px;
}
.expression-args {
  margin: 10px 0 0 12px;
  padding-left: 15px;
  border-left: 2px solid #e5eaf2;
}
.expression-arg {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin-bottom: 12px;
}
</style>
