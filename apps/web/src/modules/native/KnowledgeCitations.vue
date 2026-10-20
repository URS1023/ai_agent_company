<script setup lang="ts">
import { scoreLabel, type KnowledgeCitation } from "./knowledge";
defineProps<{ items: KnowledgeCitation[] }>();
</script>
<template>
  <div class="citation-list">
    <article
      v-for="(item, index) in items"
      :key="item.id ?? index"
      class="citation"
    >
      <div class="citation-heading">
        <strong>{{ item.document_name || "文档片段" }}</strong
        ><span v-if="item.position !== undefined">第{{ item.position }}段</span
        ><span v-if="item.score !== undefined"
          >相关度 {{ scoreLabel(item.score) }}</span
        >
      </div>
      <p>{{ item.content || "该引用未返回片段正文" }}</p>
    </article>
    <el-empty v-if="!items.length" description="暂无检索引用" />
  </div>
</template>
<style scoped>
.citation-list {
  display: grid;
  gap: 14px;
}
.citation {
  border: 1px solid var(--el-border-color-light);
  border-radius: 6px;
  padding: 16px;
}
.citation-heading {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
}
.citation-heading span {
  color: var(--el-text-color-secondary);
}
p {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  line-height: 1.8;
  margin: 12px 0 0;
}
</style>
