<script setup lang="ts">
import { computed, reactive, watch } from "vue";
import type { Entity } from "../../api/client";
import { workflowLabel, localTime } from "./workflowLabels";
const props = defineProps<{
  mode: "quality" | "maintenance";
  row: Entity;
  actions: string[];
  people: Entity[];
  queries?: Entity[];
  analyses?: Entity[];
  busy?: boolean;
  errors?: Record<string, string>;
}>();
const emit = defineEmits<{
  submit: [data: Record<string, unknown>];
  cancel: [];
}>();
const form = reactive({
  action: "",
  note: "",
  assignee_id: "",
  due_at: "",
  disposition: "rework",
  root_cause: "",
  corrective_action: "",
  evidence_query_id: "",
  verification_analysis_id: "",
  verification_query_id: "",
  value_field: "",
  time_field: "",
  unit_field: "",
});
watch(
  () => props.actions,
  (actions) => {
    if (!actions.includes(form.action)) form.action = actions[0] ?? "";
  },
  { immediate: true },
);
watch(
  () => form.verification_query_id,
  () => {
    form.value_field = "";
    form.time_field = "";
    form.unit_field = "";
  },
);
const columns = computed<string[]>(
  () =>
    props.queries?.find((query) => query.id === form.verification_query_id)
      ?.result?.columns ?? [],
);
function submit() {
  const data: Record<string, unknown> = {
    action: form.action,
    note: form.note,
  };
  if (form.action === "assign")
    Object.assign(data, {
      assignee_id: form.assignee_id,
      due_at: form.due_at ? new Date(form.due_at).toISOString() : null,
    });
  if (form.action === "submit")
    Object.assign(data, {
      disposition: form.disposition,
      root_cause: form.root_cause,
      corrective_action: form.corrective_action,
    });
  if (form.action === "repair")
    Object.assign(data, {
      root_cause: form.root_cause,
      ...(form.evidence_query_id
        ? { evidence_query_id: form.evidence_query_id }
        : {}),
    });
  if (props.mode === "maintenance" && form.action === "verify") {
    if (props.row.analysis_id)
      data.verification_analysis_id = form.verification_analysis_id;
    else
      Object.assign(data, {
        verification_query_id: form.verification_query_id,
        value_field: form.value_field,
        time_field: form.time_field,
        ...(form.unit_field ? { unit_field: form.unit_field } : {}),
      });
  }
  emit("submit", data);
}
</script>
<template>
  <el-form label-position="top" @submit.prevent="submit">
    <el-form-item label="操作"
      ><el-select v-model="form.action"
        ><el-option
          v-for="action in actions"
          :key="action"
          :value="action"
          :label="
            action === 'repair' ? '提交维修记录' : workflowLabel(action)
          " /></el-select
    ></el-form-item>
    <template v-if="form.action === 'assign'">
      <el-form-item label="责任人" :error="errors?.assignee_id"
        ><el-select v-model="form.assignee_id" filterable
          ><el-option
            v-for="person in people"
            :key="person.id"
            :value="person.id"
            :label="person.name" /></el-select
      ></el-form-item>
      <el-form-item label="计划完成时间" :error="errors?.due_at"
        ><el-date-picker
          v-model="form.due_at"
          type="datetime"
          format="YYYY-MM-DD HH:mm"
      /></el-form-item>
    </template>
    <template v-if="form.action === 'submit'">
      <el-form-item label="处置方式" :error="errors?.disposition"
        ><el-select v-model="form.disposition"
          ><el-option
            v-for="item in [
              'rework',
              'repair',
              'scrap',
              'return',
              'concession',
            ]"
            :key="item"
            :value="item"
            :label="workflowLabel(item)" /></el-select
      ></el-form-item>
      <el-form-item label="处理结果" :error="errors?.corrective_action"
        ><el-input
          v-model="form.corrective_action"
          type="textarea"
          :rows="3"
          minlength="5"
          maxlength="3000"
          required
      /></el-form-item>
    </template>
    <el-form-item
      v-if="['submit', 'repair'].includes(form.action)"
      label="检查原因"
      :error="errors?.root_cause"
      ><el-input
        v-model="form.root_cause"
        type="textarea"
        :rows="3"
        minlength="3"
        maxlength="3000"
        required
    /></el-form-item>
    <el-form-item
      v-if="form.action === 'repair' && queries?.length"
      label="维修证据"
      :error="errors?.evidence_query_id"
      ><el-select v-model="form.evidence_query_id" clearable filterable
        ><el-option
          v-for="query in queries"
          :key="query.id"
          :value="query.id"
          :label="`${query.name || '查询'} · ${localTime(query.created_at)}`" /></el-select
    ></el-form-item>
    <template v-if="mode === 'maintenance' && form.action === 'verify'">
      <el-form-item
        v-if="row.analysis_id"
        label="维修后分析"
        :error="errors?.verification_analysis_id"
        ><el-select v-model="form.verification_analysis_id" filterable
          ><el-option
            v-for="analysis in analyses"
            :key="analysis.id"
            :value="analysis.id"
            :label="`${analysis.name} · ${localTime(analysis.created_at)}`" /></el-select
      ></el-form-item>
      <template v-else>
        <el-form-item
          label="维修后复测查询"
          :error="errors?.verification_query_id"
          ><el-select v-model="form.verification_query_id" filterable
            ><el-option
              v-for="query in queries"
              :key="query.id"
              :value="query.id"
              :label="`${query.name || '查询'} · ${localTime(query.created_at)}`" /></el-select
        ></el-form-item>
        <div class="form-grid">
          <el-form-item label="数值字段" :error="errors?.value_field"
            ><el-select v-model="form.value_field"
              ><el-option
                v-for="field in columns"
                :key="field"
                :value="field"
                :label="field" /></el-select></el-form-item
          ><el-form-item label="观测时间字段" :error="errors?.time_field"
            ><el-select v-model="form.time_field"
              ><el-option
                v-for="field in columns"
                :key="field"
                :value="field"
                :label="field" /></el-select></el-form-item
          ><el-form-item label="单位字段" :error="errors?.unit_field"
            ><el-select v-model="form.unit_field" clearable
              ><el-option
                v-for="field in columns"
                :key="field"
                :value="field"
                :label="field" /></el-select
          ></el-form-item>
        </div>
      </template>
    </template>
    <el-form-item
      :label="form.action === 'repair' ? '维修记录' : '操作记录'"
      :error="errors?.note"
      ><el-input
        v-model="form.note"
        type="textarea"
        :rows="3"
        minlength="5"
        maxlength="3000"
        required
    /></el-form-item>
    <div class="row-actions">
      <el-button @click="emit('cancel')">取消</el-button
      ><el-button
        type="primary"
        native-type="submit"
        :loading="busy"
        :disabled="
          !form.action ||
          form.note.trim().length < 5 ||
          (form.action === 'assign' && !form.assignee_id) ||
          (mode === 'maintenance' &&
            form.action === 'verify' &&
            (row.analysis_id
              ? !form.verification_analysis_id
              : !form.verification_query_id ||
                !form.value_field ||
                !form.time_field))
        "
        >提交</el-button
      >
    </div>
  </el-form>
</template>
