<script setup lang="ts">
import { onMounted, shallowRef } from "vue";
import { ElMessageBox } from "element-plus";
import { api, type Entity } from "../../api/client";
import { useCollection } from "../../composables/useCollection";
import { useOperation } from "../../composables/useOperation";
import { useSession } from "../../stores/session";
import EnergyForm from "./EnergyForm.vue";
import EnergyDetail from "./EnergyDetail.vue";
import { localTime } from "./workflowLabels";
const session = useSession(),
  op = useOperation(),
  list = useCollection(() => "/energy-analyses");
const selected = shallowRef<Entity | null>(null),
  dialog = shallowRef(false),
  initial = shallowRef<Record<string, any> | undefined>();
const statusLabels: Record<string, string> = {
  insufficient_data: "数据不足",
  opportunity: "存在能耗差额",
  no_observed_gap: "未见增加",
};
function begin() {
  initial.value = selected.value
    ? {
        ...selected.value.request_snapshot,
        name: `${selected.value.name} · 复算`.slice(0, 160),
      }
    : undefined;
  dialog.value = true;
}
async function create(data: Record<string, unknown>) {
  await op.run(async () => {
    selected.value = await api.post("/energy-analyses", data);
    dialog.value = false;
    await list.load();
  }, "分析已完成");
}
async function open(row: Entity) {
  await op.run(async () => {
    selected.value = await api.get(`/energy-analyses/${row.id}`);
  });
}
async function rename() {
  if (!selected.value) return;
  try {
    const result = await ElMessageBox.prompt("", "修改分析名称", {
      inputValue: selected.value.name,
      inputValidator: (value) => !!value?.trim() && value.trim().length <= 160,
    });
    await op.run(async () => {
      selected.value = await api.patch(
        `/energy-analyses/${selected.value!.id}`,
        { revision: selected.value!.revision, name: result.value.trim() },
      );
      await list.load();
    }, "名称已保存");
  } catch {}
}
async function remove() {
  if (!selected.value) return;
  try {
    await ElMessageBox.confirm(
      `删除分析“${selected.value.name}”？`,
      "删除确认",
      { type: "warning" },
    );
    await op.run(async () => {
      await api.delete(
        `/energy-analyses/${selected.value!.id}`,
        selected.value!.revision,
      );
      selected.value = null;
      await list.load();
    }, "已删除");
  } catch {}
}
async function exportData() {
  if (!selected.value) return;
  await op.run(async () => {
    const blob = await api.postBlob(
      `/energy-analyses/${selected.value!.id}/exports`,
      {},
    );
    const url = URL.createObjectURL(blob),
      link = document.createElement("a");
    link.href = url;
    link.download = `${selected.value!.name || "节能分析"}.csv`;
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
}
onMounted(list.load);
</script>
<template>
  <div class="page-stack">
    <EnergyDetail
      v-if="selected"
      :analysis="selected"
      :busy="op.busy.value"
      @back="
        selected = null;
        list.load();
      "
      @rerun="begin"
      @rename="rename"
      @remove="remove"
      @export="exportData"
    /><template v-else
      ><div class="panel toolbar">
        <el-input
          v-model="list.query.value"
          placeholder="分析名称"
          clearable
          @keyup.enter="list.load"
        /><el-button @click="list.load">查询</el-button
        ><span class="spacer"></span
        ><el-button
          v-if="session.can('m10.write') && session.can('m06.read')"
          type="primary"
          @click="begin"
          >发起节能分析</el-button
        >
      </div>
      <section class="panel">
        <el-table :data="list.items.value" stripe v-loading="list.loading.value"
          ><el-table-column label="分析" min-width="240"
            ><template #default="{ row }"
              ><el-button link type="primary" @click="open(row)">{{
                row.name
              }}</el-button></template
            ></el-table-column
          ><el-table-column
            prop="device_name"
            label="设备"
            min-width="180"
          /><el-table-column label="结果" width="160"
            ><template #default="{ row }"
              ><el-tag
                :type="
                  row.result.status === 'opportunity'
                    ? 'warning'
                    : row.result.status === 'insufficient_data'
                      ? 'info'
                      : 'success'
                "
                >{{ statusLabels[row.result.status] }}</el-tag
              ></template
            ></el-table-column
          ><el-table-column label="分析时间" min-width="180"
            ><template #default="{ row }">{{
              localTime(row.created_at)
            }}</template></el-table-column
          ></el-table
        ><el-pagination
          v-model:current-page="list.page.value"
          :total="list.total.value"
          :page-size="list.size.value"
          layout="total,prev,pager,next"
          @current-change="list.load"
        /></section></template
    ><el-dialog v-model="dialog" title="节能分析" width="960px" destroy-on-close
      ><EnergyForm
        :initial="initial"
        :busy="op.busy.value"
        :errors="op.fields.value"
        @submit="create"
        @cancel="dialog = false"
    /></el-dialog>
  </div>
</template>
