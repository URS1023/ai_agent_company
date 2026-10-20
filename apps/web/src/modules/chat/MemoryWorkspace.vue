<script setup lang="ts">
import { ElMessageBox } from "element-plus";
import { useSession } from "../../stores/session";
import { useMemories, type MemoryRecord } from "./useMemories";
import MemoryList from "./MemoryList.vue";
import MemoryEditor from "./MemoryEditor.vue";

const account = useSession();
const memories = useMemories(
  () =>
    account.value
      ? {
          userId: account.value.user.id,
          workspaceId: account.value.workspace.id,
        }
      : null,
  account.can,
);
const {
  items,
  total,
  query,
  type,
  page,
  size,
  loading,
  reading,
  saving,
  error,
  editorError,
  fields,
  visible,
  editing,
  conflict,
  uncertain,
  draft,
  canRead,
  canWrite,
  canDelete,
} = memories;
async function remove(record: MemoryRecord) {
  const ticket = memories.capture();
  try {
    await ElMessageBox.confirm("删除这条记忆？", "删除确认", {
      type: "warning",
    });
  } catch {
    return;
  }
  if (memories.isCurrent(ticket)) await memories.remove(record, ticket);
}
async function latest() {
  const ticket = memories.capture(),
    id = editing.value?.id;
  if (!id) return;
  try {
    await ElMessageBox.confirm(
      "重新读取会替换当前编辑内容，是否继续？",
      "读取最新",
      { type: "warning" },
    );
  } catch {
    return;
  }
  if (memories.isCurrent(ticket)) await memories.edit(id);
}
</script>
<template>
  <div v-if="!canRead" class="panel">
    <el-result icon="warning" title="暂无访问权限" />
  </div>
  <div v-else class="page-stack">
    <MemoryList
      v-model:query="query"
      v-model:type="type"
      :items="items"
      :total="total"
      :page="page"
      :size="size"
      :busy="loading || reading || saving"
      :can-write="canWrite"
      :can-delete="canDelete"
      :error="error"
      @search="memories.search"
      @page="memories.changePage"
      @refresh="memories.load"
      @create="memories.create"
      @edit="memories.edit"
      @remove="remove"
    />
    <MemoryEditor
      v-model:content="draft.content"
      v-model:type="draft.type"
      :visible="visible"
      :editing="Boolean(editing)"
      :readonly="!canWrite"
      :saving="saving"
      :error="editorError"
      :fields="fields"
      :conflict="conflict"
      :uncertain="uncertain"
      @close="memories.close"
      @save="memories.save"
      @latest="latest"
      @check="memories.checkList"
    />
  </div>
</template>
