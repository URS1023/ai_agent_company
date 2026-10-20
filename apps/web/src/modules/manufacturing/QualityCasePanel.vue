<script setup lang="ts">
import { computed, reactive, shallowRef, watch } from "vue";
import { api, type Entity, type ListResult } from "../../api/client";
import { useCollection } from "../../composables/useCollection";
import { useOperation } from "../../composables/useOperation";
import { useSession } from "../../stores/session";
import WorkflowStatus from "./WorkflowStatus.vue";
import WorkflowActionForm from "./WorkflowActionForm.vue";
import ReinspectionPanel from "./ReinspectionPanel.vue";
import { availableActions, localTime, workflowLabel } from "./workflowLabels";
const props = defineProps<{ inspection?: Entity; targetType?: string }>();
const emit = defineEmits<{ changed: []; openInspection: [id: string] }>();
const session = useSession(),
  op = useOperation();
const status = shallowRef(""),
  selected = shallowRef<Entity | null>(null),
  people = shallowRef<Entity[]>([]),
  dialog = shallowRef(false),
  creation = shallowRef(false),
  parent = shallowRef<Entity | null>(null);
const form = reactive({ name: "", note: "" });
const list = useCollection(
  () =>
    `/quality-cases?${new URLSearchParams({ ...(props.inspection ? { inspection_id: props.inspection.id } : {}), ...(props.targetType ? { target_type: props.targetType } : {}), ...(status.value ? { status: status.value } : {}) })}`,
);
const actions = computed(() =>
  selected.value
    ? availableActions(
        selected.value,
        "quality",
        session.value?.user.id,
        session.can("m11.write"),
        session.can("m11.publish"),
      )
    : [],
);
const actionable = computed(
  () =>
    props.inspection?.status === "reviewed" &&
    props.inspection?.result?.outcome !== "passed" &&
    !list.total.value,
);
async function open(row: Entity) {
  await op.run(async () => {
    selected.value = await api.get(`/quality-cases/${row.id}`);
    parent.value = await api.get(`/inspections/${row.inspection_id}`);
  });
}
function beginCreate() {
  form.name = `${props.inspection?.name} · 异常处置`;
  form.note = "";
  creation.value = true;
}
async function create() {
  if (!props.inspection) return;
  await op.run(async () => {
    selected.value = await api.post("/quality-cases", {
      ...form,
      inspection_id: props.inspection!.id,
      run_id: props.inspection!.result.run_id,
      revision: props.inspection!.revision,
    });
    parent.value = await api.get(`/inspections/${props.inspection!.id}`);
    creation.value = false;
    emit("changed");
    await list.load();
  }, "处置单已创建");
}
async function beginAction() {
  await op.run(async () => {
    people.value = (
      await api.get<ListResult>("/manufacturing-assignees?area=quality")
    ).items as Entity[];
    dialog.value = true;
  });
}
async function act(data: Record<string, unknown>) {
  if (!selected.value) return;
  await op.run(async () => {
    selected.value = await api.post(
      `/quality-cases/${selected.value!.id}/actions`,
      { ...data, revision: selected.value!.revision },
    );
    dialog.value = false;
    await list.load();
    emit("changed");
  }, "处置记录已保存");
}
async function changed() {
  if (selected.value) await open(selected.value);
  await list.load();
  emit("changed");
}
watch(
  () => props.inspection?.id,
  () => {
    selected.value = null;
    void list.load();
  },
  { immediate: true },
);
watch(
  () => props.targetType,
  () => {
    selected.value = null;
    void list.load();
  },
);
</script>
<template>
  <div class="page-stack">
    <template v-if="selected">
      <div class="toolbar">
        <el-button @click="selected = null">返回列表</el-button
        ><strong>{{ selected.name }}</strong
        ><WorkflowStatus :value="selected.status" /><span class="spacer"></span
        ><el-button @click="open(selected)">刷新</el-button
        ><el-button v-if="actions.length" type="primary" @click="beginAction"
          >处理</el-button
        >
      </div>
      <el-descriptions :column="2" border
        ><el-descriptions-item label="检测任务"
          ><el-button
            link
            type="primary"
            @click="emit('openInspection', selected.inspection_id)"
            >{{ selected.inspection_name }}</el-button
          ></el-descriptions-item
        ><el-descriptions-item label="处置方式">{{
          workflowLabel(selected.disposition)
        }}</el-descriptions-item
        ><el-descriptions-item label="责任人">{{
          selected.assignee_name || "待分派"
        }}</el-descriptions-item
        ><el-descriptions-item label="计划完成">{{
          localTime(selected.due_at)
        }}</el-descriptions-item
        ><el-descriptions-item
          v-if="selected.root_cause"
          label="检查原因"
          :span="2"
          >{{ selected.root_cause }}</el-descriptions-item
        ><el-descriptions-item
          v-if="selected.corrective_action"
          label="处理结果"
          :span="2"
          >{{ selected.corrective_action }}</el-descriptions-item
        ><el-descriptions-item v-if="selected.closed_by_name" label="复核人">{{
          selected.closed_by_name
        }}</el-descriptions-item
        ><el-descriptions-item v-if="selected.closed_at" label="关闭时间">{{
          localTime(selected.closed_at)
        }}</el-descriptions-item></el-descriptions
      >
      <div
        v-if="
          parent &&
          selected.status === 'awaiting_reinspection' &&
          (!selected.reinspection_id ||
            (selected.reinspection?.status === 'reviewed' &&
              selected.reinspection?.outcome !== 'passed'))
        "
        class="panel"
      >
        <ReinspectionPanel
          :inspection="parent"
          :case-id="selected.id"
          @changed="changed"
          @open-inspection="emit('openInspection', $event)"
        />
      </div>
      <el-button
        v-if="selected.reinspection"
        @click="emit('openInspection', selected.reinspection.id)"
        >进入复检：{{ selected.reinspection.name }}</el-button
      >
      <el-timeline
        ><el-timeline-item
          v-for="event in selected.events ?? []"
          :key="event.id"
          :timestamp="localTime(event.created_at)"
          ><strong
            >{{ workflowLabel(event.action) }} ·
            {{ event.actor_name || "—" }}</strong
          >
          <p class="event-note">{{ event.note }}</p></el-timeline-item
        ></el-timeline
      >
    </template>
    <template v-else>
      <div class="toolbar">
        <el-select
          v-model="status"
          clearable
          placeholder="全部状态"
          style="max-width: 180px"
          @change="
            list.page.value = 1;
            list.load();
          "
          ><el-option
            v-for="value in [
              'open',
              'contained',
              'assigned',
              'in_progress',
              'awaiting_reinspection',
              'awaiting_review',
              'closed',
            ]"
            :key="value"
            :value="value"
            :label="workflowLabel(value)" /></el-select
        ><el-button @click="list.load">刷新</el-button
        ><span class="spacer"></span
        ><el-button
          v-if="actionable && session.can('m11.write')"
          type="primary"
          @click="beginCreate"
          >创建处置单</el-button
        >
      </div>
      <el-table :data="list.items.value" v-loading="list.loading.value" stripe
        ><el-table-column label="处置单" min-width="220"
          ><template #default="{ row }"
            ><el-button link type="primary" @click="open(row)">{{
              row.name
            }}</el-button></template
          ></el-table-column
        ><el-table-column
          v-if="!inspection"
          prop="inspection_name"
          label="检测任务"
          min-width="180"
        /><el-table-column label="状态" width="120"
          ><template #default="{ row }"
            ><WorkflowStatus :value="row.status" /></template></el-table-column
        ><el-table-column label="处置方式" width="120"
          ><template #default="{ row }">{{
            workflowLabel(row.disposition)
          }}</template></el-table-column
        ><el-table-column
          prop="assignee_name"
          label="责任人"
          width="120"
        /><el-table-column label="计划完成" min-width="170"
          ><template #default="{ row }">{{
            localTime(row.due_at)
          }}</template></el-table-column
        ></el-table
      >
      <el-pagination
        v-model:current-page="list.page.value"
        :total="list.total.value"
        :page-size="list.size.value"
        layout="total,prev,pager,next"
        @current-change="list.load"
      />
    </template>
    <el-dialog v-model="creation" title="创建异常处置单" width="600px"
      ><el-form label-position="top" @submit.prevent="create"
        ><el-form-item label="处置单名称" :error="op.fields.value.name"
          ><el-input
            v-model="form.name"
            required
            maxlength="160" /></el-form-item
        ><el-form-item label="问题记录" :error="op.fields.value.note"
          ><el-input
            v-model="form.note"
            type="textarea"
            :rows="3"
            minlength="5"
            maxlength="5000"
            required /></el-form-item
        ><el-button
          type="primary"
          native-type="submit"
          :disabled="!form.name || form.note.trim().length < 5"
          :loading="op.busy.value"
          >创建</el-button
        ></el-form
      ></el-dialog
    >
    <el-dialog
      v-model="dialog"
      :title="selected?.name"
      width="660px"
      destroy-on-close
      ><WorkflowActionForm
        v-if="selected"
        mode="quality"
        :row="selected"
        :actions="actions"
        :people="people"
        :busy="op.busy.value"
        :errors="op.fields.value"
        @submit="act"
        @cancel="dialog = false"
    /></el-dialog>
  </div>
</template>
<style scoped>
.event-note {
  white-space: pre-wrap;
  line-height: 1.7;
  margin: 6px 0 0;
}
</style>
