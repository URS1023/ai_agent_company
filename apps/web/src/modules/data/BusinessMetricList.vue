<script setup lang="ts">
import type { Entity } from "../../api/client";
defineProps<{
  items: Entity[];
  loading: boolean;
  canWrite: boolean;
  canDelete: boolean;
  canExecute: boolean;
}>();
const emit = defineEmits<{
  edit: [row: Entity];
  remove: [row: Entity];
  execute: [row: Entity];
}>();
function configured(row: Entity) {
  return (
    row.definition?.version === 1 &&
    ["aggregate", "ratio"].includes(row.definition?.kind)
  );
}
</script>
<template>
  <el-table :data="items" stripe v-loading="loading"
    ><el-table-column
      prop="name"
      label="指标"
      min-width="180"
    /><el-table-column label="口径" min-width="150"
      ><template #default="{ row }">{{
        configured(row)
          ? row.definition.kind === "ratio"
            ? "比率指标"
            : "汇总指标"
          : "草稿"
      }}</template></el-table-column
    ><el-table-column label="单位" min-width="90"
      ><template #default="{ row }">{{
        row.definition?.unit || "—"
      }}</template></el-table-column
    ><el-table-column label="分组" min-width="160"
      ><template #default="{ row }">{{
        row.definition?.group_by?.join("、") || "全部记录"
      }}</template></el-table-column
    ><el-table-column prop="revision" label="版本" width="70" /><el-table-column
      label="操作"
      width="190"
      ><template #default="{ row }"
        ><el-button link type="primary" @click="emit('edit', row)">{{
          canWrite ? "编辑" : "查看"
        }}</el-button
        ><el-button
          v-if="canExecute"
          link
          type="primary"
          :disabled="!configured(row)"
          @click="emit('execute', row)"
          >执行</el-button
        ><el-button
          v-if="canDelete"
          link
          type="danger"
          @click="emit('remove', row)"
          >删除</el-button
        ></template
      ></el-table-column
    ></el-table
  >
</template>
