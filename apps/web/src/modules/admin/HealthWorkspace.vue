<script setup lang="ts">
import { onMounted, ref } from "vue";
import { api } from "../../api/client";
import { useOperation } from "../../composables/useOperation";
import StatusTag from "../../components/common/StatusTag.vue";
import SupportWorkspace from "../operations/SupportWorkspace.vue";
const op = useOperation();
const data = ref<any>(null);
const tab = ref("status");
async function load() {
  await op.run(async () => {
    data.value = await api.get("/operations/overview");
  });
}
onMounted(load);
</script>
<template>
  <div class="page-stack">
    <div class="page-heading">
      <el-tabs v-model="tab" aria-label="系统管理"
        ><el-tab-pane label="系统状态" name="status" /><el-tab-pane
          label="系统配置"
          name="support"
      /></el-tabs>
      <el-button v-if="tab === 'status'" :loading="op.busy.value" @click="load"
        >检查状态</el-button
      >
    </div>
    <SupportWorkspace v-if="tab === 'support'" class="panel" />
    <section v-else-if="data" class="panel">
      <el-descriptions :column="2" border
        ><el-descriptions-item
          v-for="(value, key) in data"
          :key="key"
          :label="String(key)"
          ><StatusTag v-if="key === 'status'" :value="String(value)" /><span
            v-else
            >{{
              typeof value === "object" ? JSON.stringify(value) : value
            }}</span
          ></el-descriptions-item
        ></el-descriptions
      >
    </section>
  </div>
</template>
