<script setup lang="ts">
import { onMounted, ref, computed } from "vue";
import { useRouter } from "vue-router";
import { api } from "../../api/client";
import { useOperation } from "../../composables/useOperation";
import StatusTag from "../../components/common/StatusTag.vue";
import TrendChart from "../manufacturing/TrendChart.vue";
const op = useOperation();
const router = useRouter();
const data = ref<any>(null);
const metrics = computed(() => [
  { name: "设备总数", value: data.value?.counts.devices ?? 0 },
  { name: "检测任务", value: data.value?.counts.inspections ?? 0 },
  { name: "待复核", value: data.value?.counts.pending_reviews ?? 0 },
  { name: "待办任务", value: data.value?.counts.tasks ?? 0 },
]);
async function load() {
  await op.run(async () => {
    data.value = await api.get("/workbench");
  });
}
onMounted(load);
</script>
<template>
  <div class="page-stack" v-loading="op.busy.value">
    <div class="page-heading">
      <h2>工作概览</h2>
      <el-button @click="load">刷新</el-button>
    </div>
    <div class="metric-grid">
      <article class="metric" v-for="metric in metrics" :key="metric.name">
        <div class="metric-label">{{ metric.name }}</div>
        <div class="metric-value">{{ metric.value }}</div>
      </article>
    </div>
    <div class="entry-strip">
      <button @click="router.push('/device-check')">设备质检 →</button
      ><button @click="router.push('/maintenance')">预测性维护 →</button
      ><button @click="router.push('/product-check')">产品质检 →</button>
    </div>
    <div class="two-columns">
      <section class="panel">
        <div class="panel-heading">
          <h3>我的待办</h3>
          <RouterLink to="/tasks">全部任务 →</RouterLink>
        </div>
        <el-table :data="data?.tasks ?? []" stripe
          ><el-table-column prop="title" label="任务" /><el-table-column
            prop="status"
            label="状态"
            width="100"
            ><template #default="{ row }"
              ><StatusTag :value="row.status" /></template></el-table-column
          ><el-table-column
            prop="due_at"
            label="截止时间"
            width="165"
          /><el-table-column width="70"
            ><template #default="{ row }"
              ><el-button
                link
                type="primary"
                @click="router.push({ path: '/tasks', query: { id: row.id } })"
                >处理</el-button
              ></template
            ></el-table-column
          ></el-table
        >
      </section>
      <section class="panel">
        <div class="panel-heading"><h3>近期动态</h3></div>
        <el-timeline v-if="data?.activities?.length"
          ><el-timeline-item
            v-for="event in data.activities"
            :key="event.id"
            :timestamp="event.created_at"
            >{{ event.message ?? event.action }}</el-timeline-item
          ></el-timeline
        ><el-empty v-else description="暂无动态" :image-size="80" />
      </section>
    </div>
    <section class="panel">
      <div class="panel-heading"><h3>质量趋势</h3></div>
      <TrendChart
        :rows="data?.quality_trend ?? []"
        x-field="date"
        y-field="pass_rate"
        unit="%"
      />
    </section>
  </div>
</template>
