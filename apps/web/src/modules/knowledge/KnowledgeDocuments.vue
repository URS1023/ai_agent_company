<script setup lang="ts">
import { onMounted, watch } from "vue";
import { ElMessageBox } from "element-plus";
import { useKnowledgeDocuments } from "./useKnowledgeDocuments";
import { documentStatus, type KnowledgeDocument } from "./knowledge";
import KnowledgeImportDialog from "./KnowledgeImportDialog.vue";
import KnowledgeDocumentDetail from "./KnowledgeDocumentDetail.vue";
const props = defineProps<{
  spaceId: string;
  canWrite: boolean;
  canDelete: boolean;
  canFiles: boolean;
  openRequest: { id: string; sequence: number };
}>();
const emit = defineEmits<{ changed: []; invalidate: [] }>();
const documents = useKnowledgeDocuments(props.spaceId, () =>
  emit("invalidate"),
);
const {
  items,
  total,
  page,
  query,
  size,
  loading,
  busy,
  downloading,
  error,
  fields,
  importing,
  importError,
  uncertain,
  detail,
  detailOpen,
  detailLoading,
  detailError,
  indexUncertain,
  chunks,
  chunkPage,
  chunkTotal,
  chunkLoading,
} = documents;
async function remove(row: KnowledgeDocument) {
  try {
    await ElMessageBox.confirm(`删除“${row.name}”？`, "删除确认", {
      type: "warning",
    });
  } catch {
    return;
  }
  if (await documents.remove(row)) emit("changed");
}
async function rebuild() {
  if (!detail.value) return;
  try {
    await ElMessageBox.confirm(
      `重建“${detail.value.name}”的索引？`,
      "重建索引",
      { type: "warning" },
    );
  } catch {
    return;
  }
  if (detail.value) await documents.rebuild(detail.value);
}
async function submit(input: Parameters<typeof documents.importDocument>[0]) {
  if (await documents.importDocument(input)) emit("changed");
}
watch(
  () => props.openRequest,
  (request) => {
    if (request.id) void documents.open(request.id);
  },
);
onMounted(documents.load);
</script>
<template>
  <section class="panel kb-docs">
    <form
      class="toolbar document-list-toolbar"
      @submit.prevent="documents.search"
    >
      <el-input
        v-model="query"
        placeholder="搜索文档"
        aria-label="搜索文档"
        :maxlength="200"
        clearable
        :disabled="loading || busy"
        @clear="documents.search"
      /><el-button native-type="submit" :disabled="loading || busy"
        >搜索</el-button
      ><span class="spacer" /><el-button
        :disabled="loading || busy"
        @click="documents.load"
        >刷新状态</el-button
      ><el-button
        v-if="canWrite"
        type="primary"
        :disabled="busy"
        @click="documents.beginImport"
        >导入文档</el-button
      >
    </form>
    <el-alert
      v-if="error"
      :title="error"
      type="error"
      show-icon
      :closable="false"
    />
    <el-table
      v-loading="loading || busy"
      :data="items"
      empty-text="暂无匹配的文档"
    >
      <el-table-column
        prop="name"
        label="名称"
        min-width="220"
        show-overflow-tooltip
      />
      <el-table-column label="字数" width="100"
        ><template #default="{ row }">{{
          row.char_count
        }}</template></el-table-column
      >
      <el-table-column label="分块" width="80"
        ><template #default="{ row }">{{
          row.chunk_count
        }}</template></el-table-column
      >
      <el-table-column label="状态" width="120"
        ><template #default="{ row }"
          ><el-tag :type="documentStatus(row).type">{{
            documentStatus(row).label
          }}</el-tag></template
        ></el-table-column
      >
      <el-table-column label="操作" width="150"
        ><template #default="{ row }"
          ><el-button
            link
            type="primary"
            :disabled="busy"
            @click="documents.open(row.id)"
            >详情</el-button
          ><el-button
            v-if="canDelete"
            link
            type="danger"
            :disabled="busy"
            @click="remove(row)"
            >删除</el-button
          ></template
        ></el-table-column
      >
    </el-table>
    <el-pagination
      class="document-pagination"
      :current-page="page"
      :page-size="size"
      :total="total"
      :disabled="loading || busy || Boolean(error)"
      layout="total, prev, pager, next"
      @current-change="documents.changePage"
    />
    <el-dialog
      :model-value="importing"
      title="导入文档"
      width="640px"
      destroy-on-close
      :close-on-click-modal="false"
      :close-on-press-escape="!busy"
      :show-close="!busy"
      @update:model-value="!busy && (importing = $event)"
      ><KnowledgeImportDialog
        :busy="busy"
        :fields="fields"
        :error="importError"
        :uncertain="uncertain"
        :can-files="canFiles"
        @submit="submit"
        @cancel="importing = false"
        @check="
          importing = false;
          documents.load();
        "
    /></el-dialog>
    <el-drawer
      :model-value="detailOpen"
      :title="detail?.name ?? '文档详情'"
      size="min(840px, 95vw)"
      @update:model-value="!$event && documents.clearDetail()"
      ><KnowledgeDocumentDetail
        :document="detail"
        :loading="detailLoading"
        :chunks="chunks"
        :chunk-page="chunkPage"
        :chunk-total="chunkTotal"
        :chunk-loading="chunkLoading"
        :error="detailError"
        :busy="busy"
        :downloading="downloading"
        :can-write="canWrite"
        :index-uncertain="indexUncertain"
        @refresh="detail && documents.open(detail.id)"
        @download="detail && documents.download(detail)"
        @rebuild="rebuild"
        @page="documents.changeChunkPage"
    /></el-drawer>
  </section>
</template>
<style scoped>
.document-list-toolbar {
  margin-bottom: var(--sp-4);
}
.document-list-toolbar > .el-input {
  max-width: 360px;
}
.document-pagination {
  margin-top: var(--sp-4);
  justify-content: flex-end;
}
</style>
