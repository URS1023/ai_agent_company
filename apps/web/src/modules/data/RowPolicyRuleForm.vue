<script setup lang="ts">
/** 一组适用对象和同时成立的条件；同表多组访问规则由服务端合并。 */
import { computed } from "vue";
import RowPolicyConditionForm from "./RowPolicyConditionForm.vue";
import {
  newCondition,
  type PolicyColumn,
  type PolicySubjects,
  type RuleDraft,
} from "./rowPolicy";
const rule = defineModel<RuleDraft>({ required: true });
const props = defineProps<{
  index: number;
  columns: PolicyColumn[];
  subjects: PolicySubjects;
  errors: Record<string, string>;
  disabled: boolean;
}>();
defineEmits<{ remove: [] }>();
const path = computed(() => `rules.${props.index}`);
const subjects = computed(() =>
  rule.value.subject_type === "role"
    ? props.subjects.roles
    : props.subjects.users,
);
const unavailableSubjects = computed(() =>
  rule.value.subject_ids.filter(
    (id) => !subjects.value.some((item) => item.id === id),
  ),
);
function changeSubject() {
  rule.value.subject_ids = [];
}
</script>

<template>
  <article class="row-policy-rule">
    <header class="rule-header">
      <strong>访问规则 {{ index + 1 }}</strong
      ><el-button
        link
        type="danger"
        :disabled="disabled"
        @click="$emit('remove')"
        >删除规则</el-button
      >
    </header>
    <div class="rule-subjects">
      <el-form-item label="适用对象">
        <el-select
          v-model="rule.subject_type"
          :disabled="disabled"
          @change="changeSubject"
          aria-label="适用对象"
        >
          <el-option label="所有用户" value="all" /><el-option
            label="指定角色"
            value="role"
          /><el-option label="指定用户" value="user" />
        </el-select>
      </el-form-item>
      <el-form-item
        v-if="rule.subject_type !== 'all'"
        :label="rule.subject_type === 'role' ? '角色' : '用户'"
        :error="errors[`${path}.subject_ids`]"
        class="subject-picker"
      >
        <el-select
          v-model="rule.subject_ids"
          multiple
          filterable
          :multiple-limit="100"
          :disabled="disabled"
          :aria-label="rule.subject_type === 'role' ? '适用角色' : '适用用户'"
        >
          <el-option
            v-for="item in subjects"
            :key="item.id"
            :value="item.id"
            :label="item.name"
          />
          <el-option
            v-for="id in unavailableSubjects"
            :key="id"
            :value="id"
            label="已不可用对象"
            disabled
          />
        </el-select>
      </el-form-item>
    </div>
    <div class="condition-heading">
      <span>同时满足</span
      ><el-button
        link
        type="primary"
        :disabled="disabled || rule.conditions.length >= 20"
        @click="rule.conditions.push(newCondition(columns[0]))"
        >添加条件</el-button
      >
    </div>
    <el-form-item
      v-if="errors[`${path}.conditions`]"
      :error="errors[`${path}.conditions`]"
    />
    <RowPolicyConditionForm
      v-for="(condition, conditionIndex) in rule.conditions"
      :key="condition.key"
      :model-value="condition"
      @update:model-value="rule.conditions[conditionIndex] = $event"
      :columns="columns"
      :errors="errors"
      :path="`${path}.conditions.${conditionIndex}`"
      :disabled="disabled"
      @remove="rule.conditions.splice(conditionIndex, 1)"
    />
  </article>
</template>

<style scoped>
.row-policy-rule {
  padding: 16px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 2px;
  background: var(--surface);
}
.rule-header,
.condition-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}
.rule-header strong {
  font-size: 14px;
  color: #243c53;
}
.rule-subjects {
  display: grid;
  grid-template-columns: 170px minmax(240px, 1fr);
  gap: 16px;
}
.rule-subjects :deep(.el-select) {
  width: 100%;
}
.condition-heading {
  color: var(--el-text-color-secondary);
  margin-bottom: 8px;
}
.subject-picker {
  max-width: 740px;
}
</style>
