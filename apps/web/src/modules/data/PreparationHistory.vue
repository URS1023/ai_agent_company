<script setup lang="ts">
import { usePreparationHistory } from "./usePreparationHistory";
import PreparationResult from "./PreparationResult.vue";
import StatusTag from "../../components/common/StatusTag.vue";
import { computed } from "vue";
const props = defineProps<{
  history: ReturnType<typeof usePreparationHistory>;
}>();
const isMetric = computed(() =>
  Boolean(
    props.history.detail.value?.operations?.some(
      (x: Record<string, unknown>) => x.type === "metric",
    ),
  ),
);
function time(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? "—"
    : date.toLocaleString("zh-CN", { hour12: false });
}
</script>
<template>
  <el-drawer
    :model-value="history.visible.value"
    :title="history.metricId.value ? '指标运行历史' : '运行历史'"
    size="min(1180px, 92vw)"
    @close="history.close"
  >
    <div class="page-stack">
      <div class="toolbar">
        <el-input
          v-model="history.query.value"
          placeholder="搜索任务名称"
          clearable
          @keyup.enter="history.search"
        /><el-button :loading="history.loading.value" @click="history.search"
          >查询</el-button
        ><span class="spacer" /><el-button
          :disabled="history.loading.value"
          @click="history.load"
          >刷新</el-button
        >
      </div>
      <el-alert
        v-if="history.error.value"
        :title="history.error.value"
        type="error"
        :closable="false"
        show-icon
      />
      <el-table
        :data="history.items.value"
        stripe
        v-loading="history.loading.value"
        ><el-table-column
          prop="name"
          label="任务"
          min-width="180"
          show-overflow-tooltip
        /><el-table-column label="类型" width="105"
          ><template #default="{ row }">{{
            row.result?.metric ? "指标计算" : "数据处理"
          }}</template></el-table-column
        ><el-table-column label="口径版本" width="100"
          ><template #default="{ row }">{{
            row.result?.metric?.revision ?? "—"
          }}</template></el-table-column
        ><el-table-column label="状态" width="100"
          ><template #default="{ row }"
            ><StatusTag :value="row.status" /></template></el-table-column
        ><el-table-column label="结果行数" width="100"
          ><template #default="{ row }">{{
            row.result?.row_count ?? "—"
          }}</template></el-table-column
        ><el-table-column label="时间" min-width="175"
          ><template #default="{ row }">{{
            time(row.created_at)
          }}</template></el-table-column
        ><el-table-column label="操作" width="90"
          ><template #default="{ row }"
            ><el-button
              link
              type="primary"
              :disabled="history.reading.value"
              @click="history.read(row.id)"
              >查看</el-button
            ></template
          ></el-table-column
        ></el-table
      >
      <el-pagination
        :current-page="history.page.value"
        :total="history.total.value"
        :page-size="history.size"
        layout="total,prev,pager,next"
        @current-change="history.changePage"
      />
      <div v-loading="history.reading.value">
        <PreparationResult
          v-if="history.detail.value"
          :key="history.detail.value.id"
          :job="history.detail.value"
          :metric="isMetric"
          @permission-denied="history.deny"
        />
      </div>
    </div>
  </el-drawer>
</template>
