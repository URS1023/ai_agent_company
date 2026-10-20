<script setup lang="ts">
/** 文档状态完全来自Dify；手动刷新，不轮询或将提交结果伪装成索引完成。 */
import { onBeforeUnmount, onMounted, ref } from "vue";
import { ElMessageBox } from "element-plus";
import { api, type Entity } from "../../api/client";
import { useOperation } from "../../composables/useOperation";
import { useSession } from "../../stores/session";
import KnowledgeImportForm from "./KnowledgeImportForm.vue";
import KnowledgeCitations from "./KnowledgeCitations.vue";
import {
  documentStatus,
  type KnowledgeDocument,
  type DocumentSource,
  type KnowledgeCitation,
} from "./knowledge";
const props = defineProps<{ space: Entity }>();
const session = useSession();
const loading = useOperation(),
  op = useOperation(),
  citationOp = useOperation();
const documents = ref<KnowledgeDocument[]>([]),
  sources = ref<Record<string, DocumentSource>>({});
const page = ref(1),
  total = ref(0),
  importing = ref(false);
const citedDocument = ref(""),
  citations = ref<KnowledgeCitation[]>([]),
  citationOpen = ref(false);
let active = true,
  queued = false;
async function load() {
  if (!active) return;
  if (loading.busy.value) {
    queued = true;
    return;
  }
  const requestedPage = page.value;
  documents.value = [];
  sources.value = {};
  await loading.run(async () => {
    const result = await api.get<{
      data: KnowledgeDocument[];
      source_mappings: Record<string, DocumentSource>;
      total: number;
    }>(
      `/knowledge-spaces/${props.space.id}/documents?page=${requestedPage}&limit=20`,
    );
    if (!active || requestedPage !== page.value) return;
    documents.value = result.data;
    sources.value = result.source_mappings;
    total.value = result.total;
  });
  if (queued) {
    queued = false;
    await load();
  }
}
async function importDocument(payload: { file_id: string; name?: string }) {
  const saved = await op.run(
    () =>
      api.post(`/knowledge-spaces/${props.space.id}/document-imports`, payload),
    "导入请求已提交",
  );
  if (saved) {
    importing.value = false;
    page.value = 1;
    await load();
  }
}
async function rebuild(document: KnowledgeDocument) {
  const source = sources.value[document.id];
  if (!source) return;
  const submitted = await op.run(async () => {
    await ElMessageBox.confirm(`重新处理「${document.name}」？`, "重建索引", {
      confirmButtonText: "提交",
      cancelButtonText: "取消",
      type: "warning",
    });
    return api.post(`/knowledge-documents/${source.id}/reindex-jobs`, {
      process_rule: { mode: "automatic" },
    });
  }, "重建请求已提交");
  if (submitted) await load();
}
async function showCitations(document: KnowledgeDocument) {
  citedDocument.value = document.name;
  citations.value = [];
  citationOpen.value = true;
  await citationOp.run(async () => {
    const result = await api.get<{ items: KnowledgeCitation[] }>(
      `/knowledge-documents/${sources.value[document.id]!.id}/citations`,
    );
    citations.value = result.items;
  });
}
async function download(document: KnowledgeDocument) {
  await op.run(async () => {
    const result = await api.post<{ url: string }>(
      `/files/${sources.value[document.id]!.file_id}/download-links`,
      {},
    );
    const url = new URL(result.url, window.location.origin);
    if (
      url.origin !== window.location.origin ||
      !url.pathname.startsWith("/api/v1/files/")
    )
      throw new Error("文件下载地址不可用");
    const link = window.document.createElement("a");
    link.href = url.href;
    link.download = document.name;
    link.click();
  });
}
function date(value?: number) {
  return value
    ? new Date(value * 1000).toLocaleString("zh-CN", { hour12: false })
    : "—";
}
onMounted(load);
onBeforeUnmount(() => {
  active = false;
});
</script>
<template>
  <div class="knowledge-documents">
    <div class="document-toolbar">
      <el-button :loading="loading.busy.value" @click="load">刷新状态</el-button
      ><el-button
        v-if="
          session.can('m08.write') &&
          (session.can('m17.read') || session.can('m17.write'))
        "
        type="primary"
        @click="importing = true"
        >导入文件</el-button
      >
    </div>
    <el-alert
      v-if="loading.error.value"
      :title="loading.error.value"
      type="error"
      :closable="false"
    />
    <el-table
      v-loading="loading.busy.value"
      :data="documents"
      stripe
      empty-text="暂无文档"
    >
      <el-table-column
        prop="name"
        label="文档"
        min-width="220"
        show-overflow-tooltip
      />
      <el-table-column label="状态" width="120"
        ><template #default="{ row }"
          ><el-tag :type="documentStatus(row).type">{{
            documentStatus(row).label
          }}</el-tag></template
        ></el-table-column
      >
      <el-table-column label="字数" width="95"
        ><template #default="{ row }">{{
          row.word_count ?? "—"
        }}</template></el-table-column
      >
      <el-table-column label="导入时间" min-width="175"
        ><template #default="{ row }">{{
          date(row.created_at)
        }}</template></el-table-column
      >
      <el-table-column label="操作" width="250"
        ><template #default="{ row }">
          <el-popover v-if="row.error" trigger="click" :width="360"
            ><template #reference
              ><el-button link type="danger">失败原因</el-button></template
            >
            <p class="index-error">{{ row.error }}</p></el-popover
          >
          <template v-if="sources[row.id]">
            <el-button link type="primary" @click="showCitations(row)"
              >引用</el-button
            >
            <el-button
              v-if="session.can('m17.read')"
              link
              :disabled="op.busy.value"
              @click="download(row)"
              >源文件</el-button
            >
            <el-button
              v-if="session.can('m08.write')"
              link
              :disabled="op.busy.value || documentStatus(row).processing"
              @click="rebuild(row)"
              >重建索引</el-button
            >
          </template>
        </template></el-table-column
      >
    </el-table>
    <el-pagination
      v-if="total > 20"
      v-model:current-page="page"
      :page-size="20"
      :total="total"
      layout="prev,pager,next,total"
      @current-change="load"
    />
    <el-dialog
      v-model="importing"
      title="导入文档"
      width="min(640px,95vw)"
      append-to-body
      destroy-on-close
      :close-on-click-modal="false"
      ><KnowledgeImportForm
        :busy="op.busy.value"
        :errors="op.fields.value"
        @submit="importDocument"
        @cancel="importing = false"
    /></el-dialog>
    <el-dialog
      v-model="citationOpen"
      :title="`${citedDocument} · 引用`"
      width="min(760px,95vw)"
      append-to-body
      ><el-alert
        v-if="citationOp.error.value"
        :title="citationOp.error.value"
        type="error"
        :closable="false" />
      <div v-loading="citationOp.busy.value">
        <KnowledgeCitations :items="citations" /></div
    ></el-dialog>
  </div>
</template>
<style scoped>
.document-toolbar {
  display: flex;
  justify-content: space-between;
  margin-bottom: 18px;
}
.index-error {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  line-height: 1.6;
}
</style>
