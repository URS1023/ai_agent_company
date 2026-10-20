<script setup lang="ts">
import { onMounted, reactive, shallowRef } from "vue";
import { useSession } from "../../stores/session";
import { api, type Entity } from "../../api/client";
import { useCollection } from "../../composables/useCollection";
import { useOperation } from "../../composables/useOperation";
import WorkflowStatus from "./WorkflowStatus.vue";
import WorkOrderCreateForm from "./WorkOrderCreateForm.vue";
import MaintenanceWorkOrderDetail from "./MaintenanceWorkOrderDetail.vue";
import { localTime, workflowLabel } from "./workflowLabels";
const session = useSession(),
  list = useCollection(() => "/incidents"),
  op = useOperation();
const selected = shallowRef<Entity | null>(null),
  work = shallowRef(""),
  creation = shallowRef(false),
  noteDialog = shallowRef(false);
const form = reactive({ action: "acknowledge", note: "" });
async function open(row: Entity) {
  await op.run(async () => {
    selected.value = await api.get(`/incidents/${row.id}`);
  });
}
async function save() {
  if (!selected.value) return;
  await op.run(async () => {
    await api.post(`/incidents/${selected.value!.id}/actions`, {
      revision: selected.value!.revision,
      ...form,
    });
    selected.value = await api.get(`/incidents/${selected.value!.id}`);
    noteDialog.value = false;
    await list.load();
  }, "记录已保存");
}
async function create(data: Record<string, unknown>) {
  if (!selected.value) return;
  await op.run(async () => {
    const row = await api.post<Entity>(
      `/incidents/${selected.value!.id}/work-orders`,
      { ...data, revision: selected.value!.revision },
    );
    creation.value = false;
    selected.value = null;
    work.value = row.id;
    await list.load();
  }, "维修工单已创建");
}
onMounted(list.load);
</script>
<template>
  <MaintenanceWorkOrderDetail
    v-if="work"
    :order-id="work"
    @back="
      work = '';
      list.load();
    "
    @updated="list.load"
  />
  <div v-else class="page-stack">
    <div class="panel toolbar">
      <el-input
        v-model="list.query.value"
        placeholder="预警名称"
        clearable
        @keyup.enter="list.load"
      /><el-button @click="list.load">查询</el-button>
    </div>
    <section class="panel">
      <el-table :data="list.items.value" stripe v-loading="list.loading.value"
        ><el-table-column label="预警" min-width="220"
          ><template #default="{ row }"
            ><el-button link type="primary" @click="open(row)">{{
              row.name
            }}</el-button></template
          ></el-table-column
        ><el-table-column
          prop="device_name"
          label="设备"
          min-width="160"
        /><el-table-column
          prop="occurrences"
          label="观测次数"
          width="110"
        /><el-table-column label="状态" width="130"
          ><template #default="{ row }"
            ><WorkflowStatus :value="row.status" /></template></el-table-column
        ><el-table-column label="发现时间" min-width="180"
          ><template #default="{ row }">{{
            localTime(row.first_observed_at)
          }}</template></el-table-column
        ></el-table
      ><el-pagination
        v-model:current-page="list.page.value"
        :total="list.total.value"
        :page-size="list.size.value"
        layout="total,prev,pager,next"
        @current-change="list.load"
      />
    </section>
    <el-dialog
      :model-value="!!selected"
      :title="selected?.name || '预警'"
      width="760px"
      @close="selected = null"
      ><template v-if="selected"
        ><el-descriptions :column="2" border
          ><el-descriptions-item label="设备">{{
            selected.device_name
          }}</el-descriptions-item
          ><el-descriptions-item label="状态"
            ><WorkflowStatus :value="selected.status" /></el-descriptions-item
          ><el-descriptions-item label="最近观测"
            >{{ (selected.evidence?.latest ?? selected.evidence)?.value }}
            {{
              (selected.evidence?.latest ?? selected.evidence)?.unit
            }}</el-descriptions-item
          ><el-descriptions-item label="观测时间">{{
            localTime(selected.last_observed_at)
          }}</el-descriptions-item></el-descriptions
        >
        <div class="row-actions" style="margin: 18px 0">
          <el-button
            v-if="session.can('m10.write')"
            @click="
              form.action =
                selected?.status === 'open' ? 'acknowledge' : 'comment';
              form.note = '';
              noteDialog = true;
            "
            >{{ selected.status === "open" ? "受理" : "补充记录" }}</el-button
          ><el-button
            v-if="
              session.can('m10.write') &&
              !selected.work_order_id &&
              ['open', 'acknowledged'].includes(selected.status)
            "
            type="primary"
            @click="creation = true"
            >创建维修工单</el-button
          ><el-button
            v-else-if="selected.work_order_id"
            type="primary"
            @click="
              work = selected.work_order_id;
              selected = null;
            "
            >进入维修工单</el-button
          >
        </div>
        <el-timeline
          ><el-timeline-item
            v-for="event in (selected.events ?? []).filter(
              (item: any) => item.action !== 'observation',
            )"
            :key="event.id"
            :timestamp="localTime(event.created_at)"
            ><strong>{{ workflowLabel(event.action) }}</strong>
            <p>{{ event.note }}</p></el-timeline-item
          ></el-timeline
        ></template
      ></el-dialog
    >
    <el-dialog
      v-model="creation"
      title="创建维修工单"
      width="600px"
      destroy-on-close
      ><WorkOrderCreateForm
        v-if="selected"
        :name="selected.name || '设备预警'"
        :busy="op.busy.value"
        :errors="op.fields.value"
        @submit="create"
        @cancel="creation = false" /></el-dialog
    ><el-dialog v-model="noteDialog" title="预警记录" width="560px"
      ><el-form label-position="top" @submit.prevent="save"
        ><el-form-item label="操作记录" :error="op.fields.value.note"
          ><el-input
            v-model="form.note"
            type="textarea"
            :rows="4"
            required
            minlength="3"
            maxlength="3000" /></el-form-item
        ><el-button
          type="primary"
          native-type="submit"
          :disabled="form.note.trim().length < 3"
          :loading="op.busy.value"
          >保存</el-button
        ></el-form
      ></el-dialog
    >
  </div>
</template>
