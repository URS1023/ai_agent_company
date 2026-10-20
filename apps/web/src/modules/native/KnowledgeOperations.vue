<script setup lang="ts">
/** 知识日常操作聚合现有API，完整原生编辑仍保留独立入口。 */
import { ref } from "vue";
import type { Entity } from "../../api/client";
import { useSession } from "../../stores/session";
import KnowledgeDocuments from "./KnowledgeDocuments.vue";
import KnowledgeSearch from "./KnowledgeSearch.vue";
defineProps<{ space: Entity; connection?: Entity }>();
const emit = defineEmits<{
  close: [];
  native: [];
  configure: [];
  connection: [];
}>();
const session = useSession();
const tab = ref("documents");
</script>
<template>
  <el-drawer
    :model-value="true"
    :title="space.name"
    size="min(1120px,96vw)"
    @close="emit('close')"
  >
    <div class="knowledge-actions">
      <el-button @click="emit('native')">原生管理</el-button
      ><el-button
        v-if="connection && session.can('m15.write')"
        @click="emit('connection')"
        >连接设置</el-button
      ><el-button v-if="session.can('m08.write')" @click="emit('configure')"
        >设置</el-button
      >
    </div>
    <el-result v-if="!space.enabled" icon="info" title="知识库已停用" />
    <template v-else
      ><el-tabs v-model="tab"
        ><el-tab-pane label="文档" name="documents" /><el-tab-pane
          label="检索"
          name="search" /></el-tabs
      ><KnowledgeDocuments
        v-if="tab === 'documents'"
        :space="space" /><KnowledgeSearch v-else :space="space"
    /></template>
  </el-drawer>
</template>
<style scoped>
.knowledge-actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
  margin-bottom: 14px;
}
</style>
