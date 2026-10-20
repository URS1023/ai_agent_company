<script setup lang="ts">
import { onMounted, shallowRef } from "vue";
import { useCollection } from "../../composables/useCollection";
import WorkflowStatus from "./WorkflowStatus.vue";
import MaintenanceWorkOrderDetail from "./MaintenanceWorkOrderDetail.vue";
import { localTime, workflowLabel } from "./workflowLabels";
const props = defineProps<{ initialId?: string }>();
const selected = shallowRef(props.initialId ?? ""),
  status = shallowRef("");
const list = useCollection(
  () =>
    `/maintenance-work-orders${status.value ? `?status=${status.value}` : ""}`,
);
onMounted(list.load);
</script>
<template>
  <MaintenanceWorkOrderDetail
    v-if="selected"
    :order-id="selected"
    @back="
      selected = '';
      list.load();
    "
    @updated="list.load"
  />
  <div v-else class="page-stack">
    <div class="panel toolbar">
      <el-select
        v-model="status"
        clearable
        placeholder="全部状态"
        style="max-width: 200px"
        @change="
          list.page.value = 1;
          list.load();
        "
        ><el-option
          v-for="value in [
            'open',
            'assigned',
            'in_progress',
            'awaiting_review',
            'resolved',
          ]"
          :key="value"
          :value="value"
          :label="workflowLabel(value)" /></el-select
      ><el-input
        v-model="list.query.value"
        clearable
        placeholder="工单名称"
        style="max-width: 280px"
        @keyup.enter="list.load"
      /><el-button @click="list.load">查询</el-button>
    </div>
    <section class="panel">
      <el-table :data="list.items.value" v-loading="list.loading.value" stripe
        ><el-table-column label="工单" min-width="240"
          ><template #default="{ row }"
            ><el-button link type="primary" @click="selected = row.id">{{
              row.name
            }}</el-button></template
          ></el-table-column
        ><el-table-column
          prop="device_name"
          label="设备"
          min-width="160"
        /><el-table-column label="状态" width="130"
          ><template #default="{ row }"
            ><WorkflowStatus :value="row.status" /></template></el-table-column
        ><el-table-column
          prop="assignee_name"
          label="责任人"
          width="120"
        /><el-table-column label="计划完成" min-width="170"
          ><template #default="{ row }">{{
            localTime(row.due_at)
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
  </div>
</template>
