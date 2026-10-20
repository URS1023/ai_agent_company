<script setup lang="ts">
import { computed } from "vue";
import { useSession } from "../../stores/session";
import WorkflowLibrary from "./WorkflowLibrary.vue";
const account = useSession();
// 用户、工作区或权限变化时销毁旧运行意图、输入、记录和请求。
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
  <div v-if="!scope || !account.can('m04.read')" class="panel">
    <el-result icon="warning" title="暂无访问权限" />
  </div>
  <WorkflowLibrary
    v-else
    :key="scope"
    :can-write="account.can('m04.write')"
    :can-delete="account.can('m04.delete')"
    :can-knowledge="account.can('m08.read')"
  />
</template>
