<script setup lang="ts">
import { computed, shallowRef } from "vue";
import type { ChatSession } from "./chatStore";
import { searchSessions } from "./chatStore";
const props = defineProps<{ sessions: ChatSession[] }>();
const emit = defineEmits<{
  open: [id: string];
  remove: [id: string];
  create: [];
}>();
const search = shallowRef("");
const filtered = computed(() => searchSessions(props.sessions, search.value));
function time(value: number) {
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}
</script>
<template>
  <section class="panel">
    <div class="toolbar">
      <el-input
        v-model="search"
        placeholder="搜索对话"
        clearable
        aria-label="搜索对话"
      /><el-tag type="info">本机记录</el-tag><span class="spacer" /><el-button
        type="primary"
        @click="emit('create')"
        >新建对话</el-button
      >
    </div>
    <el-table :data="filtered" stripe empty-text="暂无匹配的对话"
      ><el-table-column
        prop="title"
        label="对话"
        min-width="220"
        show-overflow-tooltip
      /><el-table-column label="消息" width="90"
        ><template #default="{ row }">{{
          row.turns.length
        }}</template></el-table-column
      ><el-table-column label="最近更新" min-width="180"
        ><template #default="{ row }">{{
          time(row.updated)
        }}</template></el-table-column
      ><el-table-column label="操作" width="150"
        ><template #default="{ row }"
          ><el-button link type="primary" @click="emit('open', row.id)"
            >打开</el-button
          ><el-button link type="danger" @click="emit('remove', row.id)"
            >删除</el-button
          ></template
        ></el-table-column
      ></el-table
    >
  </section>
</template>
<style scoped>
.toolbar {
  margin-bottom: var(--sp-4);
}
.toolbar > .el-input {
  max-width: 360px;
}
</style>
