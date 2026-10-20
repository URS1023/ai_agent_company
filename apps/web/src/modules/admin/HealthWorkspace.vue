<script setup lang="ts">
import { onMounted, ref } from "vue";
import { api } from "../../api/client";
import { useOperation } from "../../composables/useOperation";
import StatusTag from "../../components/common/StatusTag.vue";
const op = useOperation();
const data = ref<any>(null);
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
      <h2>系统状态</h2>
      <el-button :loading="op.busy.value" @click="load">检查状态</el-button>
    </div>
    <section v-if="data" class="panel">
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
