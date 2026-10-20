<script setup lang="ts">
import { onMounted, ref } from "vue";
import { ElMessageBox } from "element-plus";
import { api } from "../../api/client";
import { useOperation } from "../../composables/useOperation";
import { dateLabel, resourcePath, type SupportResource } from "./support";
const props = defineProps<{ record: SupportResource; editable: boolean }>();
const emit = defineEmits<{ close: []; updated: [record: SupportResource] }>();
const op = useOperation();
const rows = ref<
  { number: number; created_at: string; snapshot: SupportResource }[]
>([]);
const selected = ref<SupportResource>();
function selectVersion(row: { snapshot: SupportResource }) {
  selected.value = row.snapshot;
}
onMounted(() =>
  op.run(async () => {
    rows.value = (
      await api.get(
        `${resourcePath(props.record.kind, props.record.id)}/revisions`,
      )
    ).items;
  }),
);
async function restore(number: number) {
  const result = await op.run(async () => {
    await ElMessageBox.confirm(
      `将「${props.record.name}」恢复到版本 ${number}？`,
      "恢复版本",
      { type: "warning", confirmButtonText: "恢复", cancelButtonText: "取消" },
    );
    return api.post<SupportResource>(
      `${resourcePath(props.record.kind, props.record.id)}/restorations`,
      { revision: props.record.revision, target_revision: number },
    );
  }, "已恢复");
  if (result) emit("updated", result);
}
</script>
<template>
  <el-dialog
    :model-value="true"
    :title="`${record.name} · 历史版本`"
    width="min(820px, 95vw)"
    @close="emit('close')"
  >
    <el-table v-loading="op.busy.value" :data="rows" @row-click="selectVersion">
      <el-table-column label="版本" width="80"
        ><template #default="{ row }"
          >v{{ row.number }}</template
        ></el-table-column
      >
      <el-table-column prop="snapshot.name" label="名称" min-width="160" />
      <el-table-column label="状态" width="90"
        ><template #default="{ row }"
          ><el-tag :type="row.snapshot.enabled ? 'success' : 'info'">{{
            row.snapshot.enabled ? "启用" : "停用"
          }}</el-tag></template
        ></el-table-column
      >
      <el-table-column label="时间" min-width="170"
        ><template #default="{ row }">{{
          dateLabel(row.created_at)
        }}</template></el-table-column
      >
      <el-table-column label="操作" width="150"
        ><template #default="{ row }"
          ><el-button link @click.stop="selected = row.snapshot">查看</el-button
          ><el-button
            v-if="
              editable &&
              row.number !== record.revision &&
              !row.snapshot.deleted
            "
            link
            type="primary"
            :disabled="op.busy.value"
            @click.stop="restore(row.number)"
            >恢复</el-button
          ></template
        ></el-table-column
      >
    </el-table>
    <pre v-if="selected" class="snapshot">{{
      JSON.stringify(selected.definition, null, 2)
    }}</pre>
  </el-dialog>
</template>
<style scoped>
.snapshot {
  max-height: 350px;
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  background: var(--el-fill-color-lighter);
  border-radius: 6px;
  padding: 16px;
}
</style>
