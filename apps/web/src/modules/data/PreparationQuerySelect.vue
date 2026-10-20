<script setup lang="ts">
import { queryLabel } from "./dataPreparation";
import type { usePreparationQueries } from "./usePreparationQueries";
const props = defineProps<{
  catalog: ReturnType<typeof usePreparationQueries>;
  disabled?: boolean;
}>();
async function search(value: string) {
  try {
    await props.catalog.search(value);
  } catch {}
}
async function page(value: number) {
  try {
    await props.catalog.changePage(value);
  } catch {}
}
</script>
<template>
  <div class="query-picker">
    <el-select
      v-model="catalog.queryId.value"
      filterable
      remote
      :remote-method="search"
      :loading="catalog.loading.value || catalog.selecting.value"
      :disabled="disabled"
      clearable
      placeholder="搜索查询名称"
      aria-label="数据查询"
    >
      <el-option
        v-for="query in catalog.options.value"
        :key="query.id"
        :value="query.id"
        :label="queryLabel(query)"
      />
      <template #footer
        ><el-pagination
          :current-page="catalog.page.value"
          :total="catalog.total.value"
          :page-size="catalog.size.value"
          layout="total,prev,pager,next"
          small
          @current-change="page"
          @click.stop
      /></template>
    </el-select>
    <span v-if="catalog.error.value" class="query-picker-error" role="alert">{{
      catalog.error.value
    }}</span>
  </div>
</template>
<style scoped>
.query-picker {
  width: 100%;
}
.query-picker-error {
  display: block;
  color: var(--el-color-danger);
  line-height: 1.5;
  margin-top: 6px;
  font-size: var(--fs-3);
}
</style>
