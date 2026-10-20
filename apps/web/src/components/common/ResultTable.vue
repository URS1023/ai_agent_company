<script setup lang="ts">
import { computed } from "vue";
const props = defineProps<{
  rows: Record<string, any>[];
  columns?: string[];
  labels?: Record<string, string>;
  loading?: boolean;
  height?: number | string;
}>();
const fields = computed(
  () => props.columns ?? Object.keys(props.rows[0] ?? {}),
);
function cell(value: unknown) {
  return value === null || value === undefined
    ? "—"
    : typeof value === "object"
      ? JSON.stringify(value)
      : String(value);
}
</script>
<template>
  <el-table
    :data="rows"
    stripe
    v-loading="loading"
    :height="height"
    style="width: 100%"
    ><el-table-column
      v-for="field in fields"
      :key="field"
      :prop="field"
      :label="labels?.[field] ?? field"
      min-width="150"
      show-overflow-tooltip
      ><template #default="{ row }">{{
        cell(row[field])
      }}</template></el-table-column
    ><template #empty
      ><el-empty description="暂无数据" :image-size="70" /></template
  ></el-table>
</template>
