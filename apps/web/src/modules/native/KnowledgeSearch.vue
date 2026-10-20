<script setup lang="ts">
import { computed, ref } from "vue";
import { api, type Entity } from "../../api/client";
import { useOperation } from "../../composables/useOperation";
import KnowledgeCitations from "./KnowledgeCitations.vue";
import {
  citationFromRecord,
  scoreLabel,
  type KnowledgeCitation,
} from "./knowledge";
const props = defineProps<{ space: Entity }>();
const op = useOperation(),
  citationOp = useOperation();
const query = ref(""),
  topK = ref(5),
  threshold = ref(0);
const results = ref<KnowledgeCitation[]>([]),
  searched = ref(false);
const selected = ref<KnowledgeCitation>(),
  citationOpen = ref(false);
const ready = computed(
  () =>
    !!query.value.trim() &&
    Number.isInteger(topK.value) &&
    topK.value >= 1 &&
    topK.value <= 20 &&
    threshold.value !== null &&
    Number.isFinite(threshold.value) &&
    threshold.value >= 0 &&
    threshold.value <= 1,
);
async function search() {
  if (!ready.value || op.busy.value) return;
  results.value = [];
  searched.value = false;
  const result = await op.run(() =>
    api.post<{ records: Record<string, any>[] }>("/knowledge-searches", {
      space_ids: [props.space.id],
      query: query.value.trim(),
      top_k: topK.value,
      score_threshold: threshold.value,
    }),
  );
  if (result) {
    results.value = result.records.map(citationFromRecord);
    searched.value = true;
  }
}
async function show(citation: KnowledgeCitation) {
  if (!citation.id) return;
  selected.value = undefined;
  citationOpen.value = true;
  await citationOp.run(async () => {
    selected.value = await api.get<KnowledgeCitation>(
      `/knowledge-citations/${citation.id}`,
    );
  });
}
</script>
<template>
  <div class="knowledge-search">
    <el-form @submit.prevent="search"
      ><div class="search-bar">
        <el-input
          v-model="query"
          placeholder="输入要查找的内容"
          aria-label="知识检索内容"
          maxlength="8000"
          clearable
          :disabled="op.busy.value"
        /><el-button
          type="primary"
          native-type="submit"
          :loading="op.busy.value"
          :disabled="!ready"
          >检索</el-button
        >
      </div>
      <el-collapse
        ><el-collapse-item title="检索设置" name="options"
          ><div class="search-options">
            <el-form-item label="返回条数"
              ><el-input-number
                v-model="topK"
                :min="1"
                :max="20"
                :precision="0" /></el-form-item
            ><el-form-item label="最低相关度"
              ><el-input-number
                v-model="threshold"
                :min="0"
                :max="1"
                :step="0.05"
                :precision="2"
            /></el-form-item></div></el-collapse-item
      ></el-collapse>
    </el-form>
    <el-alert
      v-if="op.error.value"
      type="error"
      :title="op.error.value"
      :closable="false"
    />
    <div v-loading="op.busy.value" class="search-results">
      <article
        v-for="(result, index) in results"
        :key="result.id ?? index"
        class="search-result"
      >
        <div class="result-heading">
          <strong>{{ result.document_name || "文档片段" }}</strong
          ><span>相关度 {{ scoreLabel(result.score) }}</span
          ><el-button v-if="result.id" link type="primary" @click="show(result)"
            >引用详情</el-button
          >
        </div>
        <p>{{ result.content }}</p>
      </article>
      <el-empty
        v-if="searched && !results.length"
        description="未找到匹配内容"
      />
    </div>
    <el-dialog
      v-model="citationOpen"
      title="引用详情"
      width="min(760px,95vw)"
      append-to-body
      ><el-alert
        v-if="citationOp.error.value"
        type="error"
        :title="citationOp.error.value"
        :closable="false" />
      <div v-loading="citationOp.busy.value">
        <KnowledgeCitations v-if="selected" :items="[selected]" /></div
    ></el-dialog>
  </div>
</template>
<style scoped>
.search-bar {
  display: flex;
  gap: 10px;
  margin-bottom: 16px;
}
.search-options {
  display: flex;
  gap: 24px;
  flex-wrap: wrap;
}
.search-results {
  display: grid;
  gap: 14px;
  margin-top: 20px;
  min-height: 100px;
}
.search-result {
  padding: 18px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 6px;
}
.result-heading {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.result-heading strong {
  flex: 1;
}
.result-heading span {
  color: var(--el-text-color-secondary);
}
p {
  white-space: pre-wrap;
  line-height: 1.8;
  overflow-wrap: anywhere;
  margin: 12px 0 0;
}
</style>
