<script setup lang="ts">
/** 现有数据权限页的行权限面板，表与业务主体都使用服务端已授权的名称。 */
import { onBeforeUnmount, watch } from "vue";
import RowPolicyRuleForm from "./RowPolicyRuleForm.vue";
import { newRule, type PolicyTable } from "./rowPolicy";
import { useRowPolicyEditor } from "./useRowPolicyEditor";
const props = defineProps<{ sourceId: string; tables: PolicyTable[] }>();
const emit = defineEmits<{ dirty: [value: boolean]; busy: [value: boolean] }>();
const editor = useRowPolicyEditor(props.sourceId, () => props.tables);
const {
  draft,
  subjects,
  columns,
  table,
  ready,
  busy,
  errors,
  canSave,
  dirty,
  scope,
} = editor;
watch(dirty, (value) => emit("dirty", value), { immediate: true });
watch(busy, (value) => emit("busy", value), { immediate: true });
onBeforeUnmount(() => {
  emit("dirty", false);
  emit("busy", false);
});
</script>

<template>
  <div class="row-policy-panel" v-loading="editor.loading.busy.value">
    <div class="policy-toolbar">
      <el-select
        :model-value="table"
        filterable
        :disabled="busy || !ready"
        placeholder="选择数据表"
        @change="editor.selectTable"
        aria-label="行权限数据表"
      >
        <el-option
          v-for="item in tables"
          :key="item.name"
          :value="item.name"
          :label="item.name"
        />
      </el-select>
      <span class="spacer" /><el-button :disabled="busy" @click="editor.reload"
        >刷新</el-button
      ><el-button
        :loading="editor.saving.busy.value"
        type="primary"
        :disabled="!canSave"
        @click="editor.save"
        >保存行权限</el-button
      >
    </div>
    <el-alert
      v-if="editor.loading.error.value"
      type="error"
      :title="editor.loading.error.value"
      :closable="false"
    >
    </el-alert>
    <template v-else-if="ready && table">
      <el-form label-position="top" :disabled="busy">
        <div class="policy-scope">
          <el-radio-group
            :model-value="scope"
            @change="editor.setScope"
            aria-label="记录范围"
          >
            <el-radio-button value="all">全部记录</el-radio-button
            ><el-radio-button value="rules">按规则</el-radio-button
            ><el-radio-button value="none">不开放</el-radio-button>
          </el-radio-group>
          <el-button
            v-if="draft.mode === 'rules'"
            :disabled="draft.rules.length >= 50 || !columns.length"
            @click="draft.rules.push(newRule(columns[0]))"
            >添加访问规则</el-button
          >
        </div>
        <div v-if="scope === 'rules'" class="policy-rules">
          <div
            v-for="(rule, index) in draft.rules"
            :key="rule.key"
            class="policy-rule-group"
          >
            <div v-if="index > 0" class="rule-alternative">或</div>
            <RowPolicyRuleForm
              :model-value="rule"
              @update:model-value="draft.rules[index] = $event"
              :index="index"
              :columns="columns"
              :subjects="subjects"
              :errors="errors"
              :disabled="busy"
              @remove="draft.rules.splice(index, 1)"
            />
          </div>
        </div>
        <el-empty
          v-else-if="scope === 'none'"
          description="不开放记录"
          :image-size="70"
        />
      </el-form>
      <el-alert
        v-if="editor.saving.error.value"
        :title="editor.saving.error.value"
        type="error"
        :closable="false"
      />
    </template>
    <el-empty v-else-if="ready" description="暂无数据表" />
  </div>
</template>

<style scoped>
.row-policy-panel {
  min-height: 260px;
}
.policy-toolbar,
.policy-scope {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 15px;
  margin-bottom: 18px;
}
.policy-toolbar > :first-child {
  width: 320px;
}
.policy-rules {
  display: grid;
  gap: 15px;
}
.rule-alternative {
  width: 34px;
  margin: 0 auto 15px;
  color: var(--brand-500);
  background: var(--brand-soft);
  text-align: center;
  line-height: 26px;
  border-radius: 2px;
}
.policy-scope {
  padding-top: 4px;
}
</style>
