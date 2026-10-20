<script setup lang="ts">
import {
  documentStatus,
  canRebuild,
  sourceLocation,
  fileSize,
  type KnowledgeDocument,
  type KnowledgeChunk,
} from "./knowledge";
defineProps<{
  document: KnowledgeDocument | null;
  loading: boolean;
  chunks: KnowledgeChunk[];
  chunkPage: number;
  chunkTotal: number;
  chunkLoading: boolean;
  error: string;
  busy: boolean;
  downloading: boolean;
  canWrite: boolean;
  indexUncertain: boolean;
}>();
const emit = defineEmits<{
  refresh: [];
  download: [];
  rebuild: [];
  page: [value: number];
}>();
</script>
<template>
  <div v-loading="loading" class="document-detail">
    <el-alert
      v-if="error"
      :title="error"
      type="error"
      :closable="false"
      show-icon
    />
    <template v-if="document">
      <div class="toolbar detail-toolbar">
        <el-tag :type="documentStatus(document).type">{{
          documentStatus(document).label
        }}</el-tag
        ><span
          >{{ document.char_count }} 字 ·
          {{ document.chunk_count }} 个分块</span
        ><span class="spacer" /><el-button
          :disabled="busy || loading"
          @click="emit('refresh')"
          >刷新状态</el-button
        >
      </div>
      <el-alert
        v-if="document.error"
        :title="document.error"
        type="error"
        :closable="false"
        show-icon
      />
      <el-alert
        v-for="(warning, index) in document.parser?.warnings ?? []"
        :key="index"
        :title="warning.message"
        type="warning"
        :closable="false"
      />
      <div v-if="document.source_file" class="source-file">
        <div class="source-name">
          <i class="fa-solid fa-file-lines" /><span>{{
            document.source_file.name
          }}</span
          ><span class="muted">{{ fileSize(document.source_file.size) }}</span>
        </div>
        <el-button :loading="downloading" @click="emit('download')"
          >下载原件</el-button
        >
      </div>
      <div class="row-actions detail-actions">
        <el-button
          v-if="canWrite && document.chunk_count > 0"
          :disabled="busy || indexUncertain || !canRebuild(document)"
          @click="emit('rebuild')"
          >重建索引</el-button
        >
      </div>
      <div v-loading="chunkLoading" class="document-chunks">
        <article v-for="chunk in chunks" :key="chunk.id" class="document-chunk">
          <div class="chunk-head">
            <strong>第 {{ chunk.ordinal + 1 }} 段</strong
            ><span v-if="sourceLocation(chunk.source_location)" class="muted">{{
              sourceLocation(chunk.source_location)
            }}</span>
          </div>
          <p class="chunk-content">{{ chunk.content }}</p>
        </article>
        <el-empty
          v-if="!loading && !chunkLoading && !chunks.length && !document.error"
          description="暂无可查看的正文"
          :image-size="64"
        />
      </div>
      <el-pagination
        v-if="chunkTotal > 20"
        :current-page="chunkPage"
        :page-size="20"
        :total="chunkTotal"
        :disabled="chunkLoading"
        layout="total, prev, pager, next"
        @current-change="emit('page', $event)"
      />
    </template>
  </div>
</template>
<style scoped>
.document-detail {
  min-height: 160px;
  display: grid;
  gap: var(--sp-4);
}
.detail-toolbar {
  margin-bottom: 0;
}
.source-file,
.source-name,
.chunk-head {
  display: flex;
  align-items: center;
  gap: 12px;
}
.source-file {
  padding: var(--sp-4);
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  justify-content: space-between;
}
.source-name {
  min-width: 0;
  overflow-wrap: anywhere;
}
.detail-actions {
  justify-content: flex-end;
}
.document-chunk {
  padding: var(--sp-4) 0;
  border-bottom: 1px solid var(--line);
}
.chunk-content {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  line-height: 1.8;
  margin: 12px 0 0;
}
</style>
