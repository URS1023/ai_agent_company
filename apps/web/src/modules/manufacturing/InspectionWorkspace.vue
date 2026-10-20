<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { ElMessageBox } from "element-plus";
import { useSession } from "../../stores/session";
import { api, type Entity } from "../../api/client";
import { useCollection } from "../../composables/useCollection";
import { useOperation } from "../../composables/useOperation";
import StatusTag from "../../components/common/StatusTag.vue";
import InspectionCreateForm from "./InspectionCreateForm.vue";
import InspectionDetail from "./InspectionDetail.vue";
import QualityCasePanel from "./QualityCasePanel.vue";
import QualityStatistics from "./QualityStatistics.vue";
import QualityCategoryWorkspace from "./QualityCategoryWorkspace.vue";
const session = useSession();
const props = defineProps<{ config?: string }>();
const target = computed(() =>
  ["device", "product"].includes(props.config ?? "") ? props.config : undefined,
);
const list = useCollection(
  () => `/inspections${target.value ? `?target_type=${target.value}` : ""}`,
);
const op = useOperation();
const dialog = ref(false);
const selected = ref<string | null>(null);
const active = ref("tasks");
async function create(data: Record<string, unknown>) {
  await op.run(async () => {
    const record = await api.post<Entity>("/inspections", data);
    dialog.value = false;
    selected.value = record.id;
    await list.load();
  }, "任务已创建");
}
async function remove(row: Entity) {
  try {
    await ElMessageBox.confirm(`删除检测任务“${row.name}”？`, "删除确认", {
      type: "warning",
    });
    await op.run(async () => {
      await api.delete(`/inspections/${row.id}`, row.revision);
      await list.load();
    }, "已删除");
  } catch {}
}
onMounted(list.load);
</script>
<template>
  <InspectionDetail
    v-if="selected"
    :key="selected"
    :inspection-id="selected"
    @updated="list.load"
    @navigate="selected = $event"
    @back="
      selected = null;
      list.load();
    "
  />
  <div v-else class="page-stack">
    <section class="panel" style="padding: 0 18px">
      <el-tabs v-model="active"
        ><el-tab-pane label="检测任务" name="tasks" /><el-tab-pane
          label="异常处置"
          name="cases" /><el-tab-pane
          label="质量统计"
          name="statistics" /><el-tab-pane label="类别资料" name="categories"
      /></el-tabs>
    </section>
    <section v-if="active === 'cases'" class="panel">
      <QualityCasePanel
        :target-type="target"
        @open-inspection="selected = $event"
      />
    </section>
    <QualityStatistics
      v-else-if="active === 'statistics'"
      :target-type="target"
    />
    <QualityCategoryWorkspace
      v-else-if="active === 'categories'"
      :target-type="target"
    />
    <template v-else>
      <div class="panel toolbar">
        <el-input
          v-model="list.query.value"
          placeholder="任务名称"
          clearable
          @keyup.enter="list.load"
        /><el-button @click="list.load">查询</el-button
        ><span class="spacer"></span
        ><el-button
          v-if="session.can('m11.write')"
          type="primary"
          @click="dialog = true"
          >创建检测任务</el-button
        >
      </div>
      <section class="panel">
        <el-table :data="list.items.value" stripe v-loading="list.loading.value"
          ><el-table-column label="任务名称" min-width="240"
            ><template #default="{ row }"
              ><el-button link type="primary" @click="selected = row.id">{{
                row.name
              }}</el-button></template
            ></el-table-column
          ><el-table-column label="检测类型" width="120"
            ><template #default="{ row }">{{
              row.target_type === "device" ? "设备质检" : "产品质检"
            }}</template></el-table-column
          ><el-table-column label="状态" width="120"
            ><template #default="{ row }"
              ><StatusTag :value="row.status" /></template></el-table-column
          ><el-table-column label="检测结论" width="130"
            ><template #default="{ row }"
              ><StatusTag :value="row.outcome" /></template></el-table-column
          ><el-table-column
            prop="updated_at"
            label="更新时间"
            width="185"
          /><el-table-column label="操作" width="150"
            ><template #default="{ row }"
              ><el-button link type="primary" @click="selected = row.id"
                >进入</el-button
              ><el-button
                v-if="session.can('m11.delete')"
                link
                type="danger"
                @click="remove(row)"
                >删除</el-button
              ></template
            ></el-table-column
          ></el-table
        ><el-pagination
          v-model:current-page="list.page.value"
          :total="list.total.value"
          :page-size="list.size.value"
          layout="total,prev,pager,next"
          @current-change="list.load"
        />
      </section>
    </template>
    <el-dialog
      v-model="dialog"
      title="创建检测任务"
      width="720px"
      destroy-on-close
      ><InspectionCreateForm
        :target-type="target"
        :busy="op.busy.value"
        :errors="op.fields.value"
        @submit="create"
        @cancel="dialog = false"
    /></el-dialog>
  </div>
</template>
