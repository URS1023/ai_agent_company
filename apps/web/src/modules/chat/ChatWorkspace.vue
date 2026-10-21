<script setup lang="ts">
/** 持久聊天使用原生内核；个人记忆与本机旧记录保持各自归属，不混写消息来源。 */
import { computed } from "vue";
import { useRoute } from "vue-router";
import { useSession } from "../../stores/session";
import NativeChatWorkspace from "./NativeChatWorkspace.vue";
import MemoryWorkspace from "./MemoryWorkspace.vue";
import LocalChatArchive from "./LocalChatArchive.vue";
defineProps<{ config?: string }>();
const account = useSession(),
  route = useRoute();
const scope = computed(() =>
  account.value
    ? JSON.stringify([
        account.value.user.id,
        account.value.workspace.id,
        [...account.value.permissions].sort(),
      ])
    : "",
);
</script>
<template>
  <el-result
    v-if="!scope || !account.can('m02.read')"
    icon="warning"
    title="暂无访问权限"
  /><MemoryWorkspace
    v-else-if="route.path === '/memory'"
    :key="scope"
  /><LocalChatArchive
    v-else-if="route.path === '/history' && route.query.source === 'local'"
    :key="scope"
  /><NativeChatWorkspace
    v-else
    :key="`${scope}:${route.path}`"
    :can-write="account.can('m02.write')"
    :can-delete="account.can('m02.delete')"
    :history-mode="route.path === '/history'"
    :display-name="account.value?.user.display_name ?? ''"
  />
</template>
