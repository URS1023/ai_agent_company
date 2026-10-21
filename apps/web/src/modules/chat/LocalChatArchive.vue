<script setup lang="ts">
import { computed, ref } from "vue";
import { useSession } from "../../stores/session";
import { useScopedChatSessions } from "./useScopedChatSessions";
import { searchSessions } from "./chatStore";
const account = useSession();
const records = useScopedChatSessions(() =>
  account.value
    ? { userId: account.value.user.id, workspaceId: account.value.workspace.id }
    : null,
);
const keyword = ref("");
const items = computed(() =>
  searchSessions(records.sessions.value, keyword.value),
);
</script>
<template>
  <section class="panel page-stack">
    <div class="toolbar">
      <strong>本机旧记录</strong
      ><el-input
        v-model="keyword"
        placeholder="搜索旧记录"
        style="max-width: 300px"
      /><span class="spacer" /><router-link to="/history">对话历史</router-link>
    </div>
    <el-alert
      v-if="records.storageError.value"
      :title="records.storageError.value"
      type="error"
      :closable="false"
    /><el-table :data="items" @row-click="(row: any) => records.select(row.id)"
      ><el-table-column prop="title" label="对话" /><el-table-column
        label="消息"
        width="90"
        ><template #default="{ row }">{{
          row.turns.length
        }}</template></el-table-column
      ></el-table
    >
    <div v-if="records.active.value" class="page-stack">
      <strong>{{ records.active.value.title }}</strong>
      <div
        v-for="(turn, index) in records.turns.value"
        :key="index"
        class="local-archive-message"
      >
        <strong>{{ turn.role === "user" ? "我" : "助手" }}</strong>
        <p>{{ turn.content }}</p>
        <details v-if="turn.reasoning">
          <summary>思考过程</summary>
          <p>{{ turn.reasoning }}</p>
        </details>
      </div>
    </div>
  </section>
</template>
<style scoped>
.local-archive-message {
  border-bottom: 1px solid var(--border);
  padding: 12px 0;
}
.local-archive-message p {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
