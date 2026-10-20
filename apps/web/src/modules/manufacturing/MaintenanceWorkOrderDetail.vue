<script setup lang="ts">
import { computed, shallowRef, watch } from "vue";
import { api, type Entity, type ListResult } from "../../api/client";
import { useSession } from "../../stores/session";
import { useOperation } from "../../composables/useOperation";
import WorkflowStatus from "./WorkflowStatus.vue";
import WorkflowActionForm from "./WorkflowActionForm.vue";
import { availableActions, localTime, workflowLabel } from "./workflowLabels";
import { allRecords } from "./workflowOptions";
const props = defineProps<{ orderId: string }>();
const emit = defineEmits<{ back: []; updated: [] }>();
const session = useSession(),
  op = useOperation();
const row = shallowRef<Entity | null>(null),
  people = shallowRef<Entity[]>([]),
  queries = shallowRef<Entity[]>([]),
  analyses = shallowRef<Entity[]>([]),
  dialog = shallowRef(false);
const actions = computed(() =>
  row.value
    ? availableActions(
        row.value,
        "maintenance",
        session.value?.user.id,
        session.can("m10.write"),
        session.can("m10.publish"),
      )
    : [],
);
const criterion = computed(() => row.value?.source_snapshot ?? {});
const operatorLabels: Record<string, string> = {
  gt: "大于",
  gte: "大于等于",
  lt: "小于",
  lte: "小于等于",
};
async function load() {
  await op.run(async () => {
    row.value = await api.get(`/maintenance-work-orders/${props.orderId}`);
  });
}
async function beginAction() {
  if (!row.value) return;
  await op.run(async () => {
    people.value = (
      await api.get<ListResult>("/manufacturing-assignees?area=maintenance")
    ).items as Entity[];
    queries.value = session.can("m06.read") ? await allRecords("/queries") : [];
    analyses.value = row.value!.analysis_id
      ? (
          await allRecords(
            `/maintenance-analyses?device_id=${row.value!.device_id}`,
          )
        ).filter(
          (item) =>
            item.result?.status === "normal" &&
            new Date(item.created_at) >= new Date(row.value!.repaired_at ?? 0),
        )
      : [];
    if (row.value!.repaired_at)
      queries.value = queries.value.filter(
        (item) => new Date(item.created_at) >= new Date(row.value!.repaired_at),
      );
    dialog.value = true;
  });
}
async function act(data: Record<string, unknown>) {
  if (!row.value) return;
  await op.run(async () => {
    row.value = await api.post(
      `/maintenance-work-orders/${props.orderId}/actions`,
      { ...data, revision: row.value!.revision },
    );
    dialog.value = false;
    emit("updated");
  }, "工单记录已保存");
}
watch(() => props.orderId, load, { immediate: true });
</script>
<template>
  <div class="page-stack" v-loading="op.busy.value">
    <div class="page-heading">
      <div class="row-actions">
        <el-button @click="emit('back')">返回</el-button>
        <h2>{{ row?.name }}</h2>
        <WorkflowStatus :value="row?.status" />
      </div>
      <div class="row-actions">
        <el-button @click="load">刷新</el-button
        ><el-button v-if="actions.length" type="primary" @click="beginAction"
          >处理</el-button
        >
      </div>
    </div>
    <template v-if="row"
      ><section class="panel">
        <el-descriptions :column="2" border
          ><el-descriptions-item label="设备">{{
            row.device_name
          }}</el-descriptions-item
          ><el-descriptions-item label="来源">{{
            row.incident_id ? "设备预警" : "维护分析"
          }}</el-descriptions-item
          ><el-descriptions-item label="责任人">{{
            row.assignee_name || "待分派"
          }}</el-descriptions-item
          ><el-descriptions-item label="计划完成">{{
            localTime(row.due_at)
          }}</el-descriptions-item
          ><el-descriptions-item
            v-if="row.root_cause"
            label="检查原因"
            :span="2"
            >{{ row.root_cause }}</el-descriptions-item
          ><el-descriptions-item
            v-if="row.repair_note"
            label="维修记录"
            :span="2"
            >{{ row.repair_note }}</el-descriptions-item
          ><el-descriptions-item v-if="row.repaired_at" label="维修完成">{{
            localTime(row.repaired_at)
          }}</el-descriptions-item
          ><el-descriptions-item v-if="row.performed_by_name" label="维修人">{{
            row.performed_by_name
          }}</el-descriptions-item
          ><el-descriptions-item v-if="row.verified_at" label="复核时间">{{
            localTime(row.verified_at)
          }}</el-descriptions-item
          ><el-descriptions-item v-if="row.verified_by_name" label="复核人">{{
            row.verified_by_name
          }}</el-descriptions-item></el-descriptions
        >
      </section>
      <section class="panel">
        <div class="panel-heading"><h3>检查依据</h3></div>
        <el-descriptions :column="2" border
          ><template v-if="criterion.type === 'incident'"
            ><el-descriptions-item label="原预警条件"
              >{{ operatorLabels[criterion.operator] }}
              {{ criterion.threshold }}
              {{ criterion.unit }}</el-descriptions-item
            ><el-descriptions-item label="触发观测"
              >{{ criterion.evidence?.value }}
              {{ criterion.evidence?.unit }}</el-descriptions-item
            ><el-descriptions-item label="观测时间">{{
              localTime(criterion.evidence?.observed_at)
            }}</el-descriptions-item></template
          ><template v-else
            ><el-descriptions-item label="同工况变化">{{
              criterion.result?.relative_change === undefined
                ? "—"
                : `${(criterion.result.relative_change * 100).toFixed(2)}%`
            }}</el-descriptions-item
            ><el-descriptions-item label="工况覆盖率">{{
              criterion.result?.coverage === undefined
                ? "—"
                : `${(criterion.result.coverage * 100).toFixed(2)}%`
            }}</el-descriptions-item></template
          ><template v-if="row.verification_snapshot?.type === 'query'"
            ><el-descriptions-item label="复测观测"
              >{{ row.verification_snapshot.value }}
              {{ row.verification_snapshot.unit }}</el-descriptions-item
            ><el-descriptions-item label="复测时间">{{
              localTime(row.verification_snapshot.observed_at)
            }}</el-descriptions-item></template
          ><el-descriptions-item
            v-if="row.verification_snapshot?.type === 'analysis'"
            label="复测分析"
            ><WorkflowStatus
              :value="
                row.verification_snapshot.result?.status
              " /></el-descriptions-item
        ></el-descriptions>
      </section>
      <section class="panel">
        <div class="panel-heading"><h3>处理记录</h3></div>
        <el-timeline
          ><el-timeline-item
            v-for="event in row.events ?? []"
            :key="event.id"
            :timestamp="localTime(event.created_at)"
            ><strong
              >{{
                event.action === "repair"
                  ? "提交维修记录"
                  : workflowLabel(event.action)
              }}
              · {{ event.actor_name || "—" }}</strong
            >
            <p class="event-note">{{ event.note }}</p></el-timeline-item
          ></el-timeline
        >
      </section></template
    ><el-dialog
      v-model="dialog"
      :title="row?.name"
      width="680px"
      destroy-on-close
      ><WorkflowActionForm
        v-if="row"
        mode="maintenance"
        :row="row"
        :actions="actions"
        :people="people"
        :queries="queries"
        :analyses="analyses"
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
