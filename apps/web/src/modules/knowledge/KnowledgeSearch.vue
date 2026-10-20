<script setup lang="ts">
import { onScopeDispose, ref, shallowRef } from "vue";
import { sourceLocation, type KnowledgeHit } from "./knowledge";
import { useKnowledgeRequests } from "./useKnowledgeRequests";
const props = defineProps<{ spaceId: string }>();
const emit = defineEmits<{ open: [id: string] }>();
const transport = useKnowledgeRequests();
const query = shallowRef(""),
  hits = ref<KnowledgeHit[]>([]),
  searched = shallowRef(false),
  busy = shallowRef(false),
  error = shallowRef("");
let sequence = 0;
async function search() {
  if (!query.value.trim() || busy.value) return;
  const current = ++sequence;
  busy.value = true;
  error.value = "";
  hits.value = [];
  searched.value = false;
  try {
    const result = await transport.send<{ items: KnowledgeHit[] }>(
      "/kb/search",
      {
        method: "POST",
        body: JSON.stringify({
          space_ids: [props.spaceId],
          query: query.value.trim(),
          top_k: 5,
        }),
      },
    );
    if (!transport.active() || current !== sequence) return;
    hits.value = result.items;
    searched.value = true;
  } catch (failure) {
    if (transport.active() && current === sequence)
      error.value = failure instanceof Error ? failure.message : "检索失败";
  } finally {
    if (transport.active() && current === sequence) busy.value = false;
  }
}
function clear() {
  sequence++;
  query.value = "";
  hits.value = [];
  searched.value = false;
  busy.value = false;
  error.value = "";
}
onScopeDispose(clear);
</script>
<template>
  <form class="panel kb-search" @submit.prevent="search">
    <el-input
      v-model="query"
      placeholder="检索知识内容"
      aria-label="检索知识"
      :maxlength="2000"
      clearable
      :disabled="busy"
      @clear="clear"
    /><el-button
      type="primary"
      native-type="submit"
      :loading="busy"
      :disabled="!query.trim()"
      >检索</el-button
    >
  </form>
  <el-alert
    v-if="error"
    :title="error"
    type="error"
    show-icon
    :closable="false"
  />
  <section v-if="searched" class="panel kb-results">
    <div class="section-title">
      检索结果<span class="tail">{{ hits.length }} 段命中</span>
    </div>
    <el-empty v-if="!hits.length" description="没有命中内容" :image-size="64" />
    <article v-for="hit in hits" :key="hit.chunk_id" class="kb-hit">
      <div class="kb-hit-head">
        <el-button link type="primary" @click="emit('open', hit.document_id)">{{
          hit.document_name
        }}</el-button
        ><span class="badge outline">第 {{ hit.ordinal + 1 }} 段</span
        ><span v-if="sourceLocation(hit.source_location)" class="muted">{{
          sourceLocation(hit.source_location)
        }}</span
        ><span class="kb-score">相似度 {{ hit.score.toFixed(4) }}</span>
      </div>
      <p class="kb-hit-body">{{ hit.content }}</p>
    </article>
  </section>
</template>
