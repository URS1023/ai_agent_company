<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { api, type Entity, type ListResult } from "../../api/client";
import { useOperation } from "../../composables/useOperation";
const router = useRouter();
const op = useOperation();
const packages = ref<Entity[]>([]);
async function install(item: Entity) {
  await op.run(async () => {
    await api.post("/industry-package-installations", {
      package_id: item.id,
      version: item.version,
    });
    router.push("/applications");
  }, "行业方案已安装");
}
onMounted(() =>
  op.run(async () => {
    packages.value = (await api.get<ListResult>("/industry-packages"))
      .items as Entity[];
  }),
);
</script>
<template>
  <div class="page-stack">
    <div class="page-heading"><h2>行业方案</h2></div>
    <div class="card-grid">
      <article v-for="item in packages" :key="item.id" class="app-card">
        <el-tag>{{ item.industry }}</el-tag>
        <h3 style="margin-top: 18px">{{ item.name }}</h3>
        <div
          v-for="application in item.applications ?? []"
          :key="application.name"
          style="padding: 9px 0; color: #666"
        >
          {{ application.name }}
        </div>
        <div class="row-actions" style="margin-top: 18px">
          <span class="muted">v{{ item.version }}</span
          ><span style="flex: 1"></span
          ><el-button
            type="primary"
            :loading="op.busy.value"
            @click="install(item)"
            >安装方案</el-button
          >
        </div>
      </article>
    </div>
  </div>
</template>
