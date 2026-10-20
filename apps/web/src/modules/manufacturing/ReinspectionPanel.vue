<script setup lang="ts">
import { computed, reactive, shallowRef, watch } from "vue";
import { api, type Entity } from "../../api/client";
import { useOperation } from "../../composables/useOperation";
import { useSession } from "../../stores/session";
import StatusTag from "../../components/common/StatusTag.vue";
import { localTime } from "./workflowLabels";
const props = defineProps<{ inspection: Entity; caseId?: string }>();
const emit = defineEmits<{ changed: []; openInspection: [id: string] }>();
const session = useSession(),
  op = useOperation();
const lineage = shallowRef<Entity[]>([]),
  links = shallowRef<Entity[]>([]),
  dialog = shallowRef(false);
const form = reactive({
  name: "",
  reason: "",
  selection: "exceptions",
  sample_ids: [] as string[],
});
const resultSamples = computed<any[]>(
  () => props.inspection.result?.samples ?? [],
);
async function load() {
  await op.run(async () => {
    const data = await api.get(`/inspections/${props.inspection.id}/lineage`);
    lineage.value = data.items;
    links.value = data.links;
  });
}
function open() {
  form.name = `${props.inspection.name} · 复检`;
  form.reason = "";
  form.selection = "exceptions";
  form.sample_ids = [];
  dialog.value = true;
}
async function create() {
  await op.run(async () => {
    const row = await api.post<Entity>(
      `/inspections/${props.inspection.id}/reinspections`,
      {
        revision: props.inspection.revision,
        run_id: props.inspection.result.run_id,
        name: form.name,
        reason: form.reason,
        ...(props.caseId ? { case_id: props.caseId } : {}),
        ...(form.selection === "selected"
          ? { sample_ids: form.sample_ids }
          : {}),
      },
    );
    dialog.value = false;
    emit("changed");
    emit("openInspection", row.id);
  }, "复检任务已创建");
}
watch(() => props.inspection.id, load, { immediate: true });
</script>
<template>
  <div class="page-stack">
    <div class="toolbar">
      <span class="spacer"></span><el-button @click="load">刷新</el-button
      ><el-button
        v-if="session.can('m11.write') && inspection.status === 'reviewed'"
        type="primary"
        @click="open"
        >创建复检</el-button
      >
    </div>
    <el-table :data="lineage" v-loading="op.busy.value"
      ><el-table-column label="任务" min-width="220"
        ><template #default="{ row }"
          ><el-button
            link
            type="primary"
            :disabled="row.id === inspection.id"
            @click="emit('openInspection', row.id)"
            >{{ row.name }}</el-button
          ></template
        ></el-table-column
      ><el-table-column label="类型" width="90"
        ><template #default="{ row }">{{
          links.some((link) => link.child_inspection_id === row.id)
            ? "复检"
            : "首检"
        }}</template></el-table-column
      ><el-table-column label="状态" width="120"
        ><template #default="{ row }"
          ><StatusTag :value="row.status" /></template></el-table-column
      ><el-table-column label="结论" width="120"
        ><template #default="{ row }"
          ><StatusTag :value="row.outcome" /></template></el-table-column
      ><el-table-column label="创建时间" min-width="170"
        ><template #default="{ row }">{{
          localTime(row.created_at)
        }}</template></el-table-column
      ><el-table-column label="复检原因" min-width="200"
        ><template #default="{ row }">{{
          links.find((link) => link.child_inspection_id === row.id)?.reason ||
          "—"
        }}</template></el-table-column
      ></el-table
    >
    <el-dialog v-model="dialog" title="创建复检" width="620px" destroy-on-close
      ><el-form label-position="top" @submit.prevent="create">
        <el-form-item label="任务名称" :error="op.fields.value.name"
          ><el-input v-model="form.name" required maxlength="160"
        /></el-form-item>
        <el-form-item label="复检样本"
          ><el-radio-group v-model="form.selection"
            ><el-radio-button value="exceptions">异常及缺失样本</el-radio-button
            ><el-radio-button value="selected" :disabled="!resultSamples.length"
              >选择样本</el-radio-button
            ></el-radio-group
          ></el-form-item
        >
        <el-form-item
          v-if="form.selection === 'selected'"
          label="样本"
          :error="op.fields.value.sample_ids"
          ><el-select v-model="form.sample_ids" multiple filterable
            ><el-option
              v-for="(sample, index) in resultSamples"
              :key="sample.sample_id"
              :value="sample.sample_id"
              :label="sample.external_ref || `样本 ${index + 1}`" /></el-select
        ></el-form-item>
        <el-form-item label="复检原因" :error="op.fields.value.reason"
          ><el-input
            v-model="form.reason"
            type="textarea"
            :rows="3"
            minlength="5"
            maxlength="2000"
            required
        /></el-form-item>
        <div class="row-actions">
          <el-button @click="dialog = false">取消</el-button
          ><el-button
            type="primary"
            native-type="submit"
            :loading="op.busy.value"
            :disabled="
              !form.name ||
              form.reason.trim().length < 5 ||
              (form.selection === 'selected' && !form.sample_ids.length)
            "
            >创建并进入</el-button
          >
        </div>
      </el-form></el-dialog
    >
  </div>
</template>
