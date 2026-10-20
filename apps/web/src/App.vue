<script setup lang="ts">
import { onMounted, shallowRef } from "vue";
import { ApiError } from "./api/client";
import { useSession } from "./stores/session";
import AppShell from "./components/layout/AppShell.vue";
import AuthWorkspace from "./modules/auth/AuthWorkspace.vue";
const session = useSession();
const failure = shallowRef("");
async function load() {
  failure.value = "";
  try {
    await session.bootstrap();
  } catch (e) {
    if (!(e instanceof ApiError && e.status === 401))
      failure.value = e instanceof Error ? e.message : "连接失败";
  }
}
onMounted(load);
</script>
<template>
  <div v-if="!session.ready" class="startup">
    <el-icon class="is-loading"
      ><svg viewBox="0 0 24 24">
        <circle
          cx="12"
          cy="12"
          r="9"
          stroke="currentColor"
          stroke-width="2"
          fill="none"
          stroke-dasharray="40 20"
        /></svg
    ></el-icon>
  </div>
  <div v-else-if="failure" class="startup">
    <el-result icon="error" :title="failure"
      ><template #extra
        ><el-button type="primary" @click="load">重新连接</el-button></template
      ></el-result
    >
  </div>
  <AuthWorkspace v-else-if="!session.authenticated" />
  <AppShell v-else />
</template>
