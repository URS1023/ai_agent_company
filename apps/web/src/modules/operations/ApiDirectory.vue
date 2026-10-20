<script setup lang="ts">
/** 直接展示当前服务生成的契约，不嵌入第三方Swagger站点或提供任意执行器。 */
import { computed, onMounted, ref, shallowRef } from "vue";
import { api } from "../../api/client";
import { useOperation } from "../../composables/useOperation";
import { download } from "./support";
const op = useOperation();
const contract = shallowRef<Record<string, any>>();
const search = ref("");
const selected = shallowRef<Record<string, any>>();
const items = computed(() =>
  Object.entries(contract.value?.paths ?? {}).flatMap(
    ([path, entries]: [string, any]) =>
      Object.entries(entries)
        .filter(([method]) =>
          ["get", "post", "patch", "put", "delete", "head", "options"].includes(
            method,
          ),
        )
        .map(([method, operation]: [string, any]) => ({
          path,
          method: method.toUpperCase(),
          summary: operation.summary || operation.operationId || "",
          tags: operation.tags?.join(" / ") ?? "",
          operation,
        })),
  ),
);
const filtered = computed(() =>
  items.value.filter((item) =>
    `${item.path} ${item.method} ${item.summary} ${item.tags}`
      .toLowerCase()
      .includes(search.value.toLowerCase()),
  ),
);
onMounted(() =>
  op.run(async () => {
    contract.value = await api.get("/operations/developer-tools/openapi");
  }),
);
</script>
<template>
  <div class="api-directory">
    <div class="toolbar">
      <el-input
        v-model="search"
        clearable
        placeholder="搜索接口名称或路径"
        aria-label="搜索接口"
        style="max-width: 360px"
      /><el-button
        :disabled="!contract"
        @click="
          download(JSON.stringify(contract, null, 2), 'platform-openapi.json')
        "
        >导出 OpenAPI</el-button
      >
    </div>
    <el-table
      v-loading="op.busy.value"
      :data="filtered"
      stripe
      max-height="600"
      @row-click="selected = $event"
    >
      <el-table-column prop="method" label="方法" width="90" /><el-table-column
        prop="path"
        label="路径"
        min-width="300"
      /><el-table-column
        prop="summary"
        label="名称"
        min-width="200"
      /><el-table-column prop="tags" label="模块" min-width="140" />
      <el-table-column label="操作" width="80"
        ><template #default="{ row }"
          ><el-button link type="primary" @click="selected = row"
            >详情</el-button
          ></template
        ></el-table-column
      >
    </el-table>
    <el-drawer
      :model-value="!!selected"
      :title="selected?.summary || '接口详情'"
      size="min(740px, 95vw)"
      @close="selected = undefined"
    >
      <pre>{{ JSON.stringify(selected?.operation, null, 2) }}</pre>
    </el-drawer>
  </div>
</template>
<style scoped>
.toolbar {
  display: flex;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}
pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  margin: 0;
}
</style>
