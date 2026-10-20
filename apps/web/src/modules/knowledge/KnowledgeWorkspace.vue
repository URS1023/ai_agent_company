<script setup lang="ts">
import { computed } from "vue";
import { useSession } from "../../stores/session";
import KnowledgeLibrary from "./KnowledgeLibrary.vue";
const account = useSession();
// 工作区、用户或权限改变时卸载旧视图，连同其上传、检索与原件缓存一并隔离。
const scopeKey = computed(() =>
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
  <div v-if="!scopeKey || !account.can('m08.read')" class="panel">
    <el-result icon="warning" title="暂无访问权限" />
  </div>
  <KnowledgeLibrary
    v-else
    :key="scopeKey"
    :can-write="account.can('m08.write')"
    :can-delete="account.can('m08.delete')"
    :can-files="account.can('m17.read')"
  />
</template>
