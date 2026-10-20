<script setup lang="ts">
/** 一项字段条件的类型化控件；动态主体与空值比较不会把旧的字面值提交到服务端。 */
import { computed } from "vue";
import {
  newCondition,
  operatorsFor,
  hasLiteralValue,
  isSetOperator,
  type ConditionDraft,
  type PolicyColumn,
} from "./rowPolicy";
const condition = defineModel<ConditionDraft>({ required: true });
const props = defineProps<{
  columns: PolicyColumn[];
  errors: Record<string, string>;
  path: string;
  disabled: boolean;
}>();
defineEmits<{ remove: [] }>();
const operators = computed(() => operatorsFor(condition.value));
const literal = computed(() => hasLiteralValue(condition.value));
const multiple = computed(() => isSetOperator(condition.value.operator));
const nullComparison = computed(() =>
  ["is_null", "not_null"].includes(condition.value.operator),
);
function fieldChanged() {
  condition.value = {
    ...newCondition(
      props.columns.find((column) => column.name === condition.value.field),
    ),
    key: condition.value.key,
  };
}
function sourceChanged() {
  if (!operators.value.some((item) => item.value === condition.value.operator))
    condition.value.operator = "eq";
}
function operatorChanged() {
  if (nullComparison.value) condition.value.value_source = "literal";
  if (
    multiple.value &&
    !condition.value.values_text.length &&
    condition.value.value_text !== ""
  )
    condition.value.values_text = [condition.value.value_text];
  if (
    multiple.value &&
    !condition.value.values_boolean.length &&
    condition.value.value_boolean !== null
  )
    condition.value.values_boolean = [condition.value.value_boolean];
}
</script>

<template>
  <div class="row-condition" :data-condition-key="condition.key">
    <el-form-item label="字段" :error="errors[`${path}.field`]">
      <el-select
        v-model="condition.field"
        filterable
        :disabled="disabled"
        @change="fieldChanged"
        aria-label="条件字段"
      >
        <el-option
          v-for="column in columns"
          :key="column.name"
          :value="column.name"
          :label="column.name"
        >
          <span>{{ column.name }}</span
          ><span class="field-type">{{ column.type }}</span>
        </el-option>
      </el-select>
    </el-form-item>
    <el-form-item label="比较方式" :error="errors[`${path}.operator`]">
      <el-select
        v-model="condition.operator"
        :disabled="disabled"
        @change="operatorChanged"
        aria-label="比较方式"
      >
        <el-option
          v-for="item in operators"
          :key="item.value"
          :value="item.value"
          :label="item.label"
        />
      </el-select>
    </el-form-item>
    <el-form-item
      v-if="!nullComparison"
      label="取值"
      :error="errors[`${path}.value_source`]"
    >
      <el-select
        v-model="condition.value_source"
        :disabled="disabled"
        @change="sourceChanged"
        aria-label="条件取值"
      >
        <el-option label="填写值" value="literal" /><el-option
          label="当前用户"
          value="current_user_id"
        /><el-option label="当前工作区" value="current_workspace_id" />
      </el-select>
    </el-form-item>
    <template v-if="literal">
      <el-form-item label="值类型">
        <el-select
          v-model="condition.value_kind"
          :disabled="disabled"
          @change="sourceChanged"
          aria-label="值类型"
        >
          <el-option label="文本" value="text" /><el-option
            label="数字"
            value="number"
          /><el-option label="布尔" value="boolean" />
        </el-select>
      </el-form-item>
      <el-form-item
        label="匹配值"
        :error="errors[`${path}.value`]"
        class="condition-value"
      >
        <el-select
          v-if="condition.value_kind === 'boolean' && multiple"
          v-model="condition.values_boolean"
          multiple
          :disabled="disabled"
          aria-label="匹配值"
        >
          <el-option label="是" :value="true" /><el-option
            label="否"
            :value="false"
          />
        </el-select>
        <el-select
          v-else-if="condition.value_kind === 'boolean'"
          v-model="condition.value_boolean"
          :disabled="disabled"
          aria-label="匹配值"
        >
          <el-option label="是" :value="true" /><el-option
            label="否"
            :value="false"
          />
        </el-select>
        <el-select
          v-else-if="multiple"
          v-model="condition.values_text"
          multiple
          filterable
          allow-create
          default-first-option
          :multiple-limit="100"
          :disabled="disabled"
          placeholder="输入后按回车添加"
          aria-label="匹配值"
        />
        <el-input
          v-else
          v-model="condition.value_text"
          :disabled="disabled"
          :inputmode="condition.value_kind === 'number' ? 'decimal' : 'text'"
          aria-label="匹配值"
        />
      </el-form-item>
    </template>
    <el-button
      class="remove-condition"
      link
      type="danger"
      :disabled="disabled"
      @click="$emit('remove')"
      aria-label="删除条件"
      >删除</el-button
    >
  </div>
</template>

<style scoped>
.row-condition {
  display: grid;
  grid-template-columns:
    minmax(160px, 1.2fr) 130px 145px 100px minmax(160px, 1.5fr)
    42px;
  gap: 12px;
  align-items: start;
}
.row-condition :deep(.el-form-item) {
  margin-bottom: 12px;
  min-width: 0;
}
.row-condition :deep(.el-select) {
  width: 100%;
}
.field-type {
  margin-left: 12px;
  color: var(--el-text-color-secondary);
}
.remove-condition {
  margin-top: 32px;
  grid-column: -2 / -1;
}
.condition-value {
  min-width: 160px;
}
</style>
