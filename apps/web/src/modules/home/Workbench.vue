<script setup lang="ts">
import { onMounted, ref, computed } from "vue";
import { useRouter } from "vue-router";
import { api } from "../../api/client";
import { useOperation } from "../../composables/useOperation";
import StatusTag from "../../components/common/StatusTag.vue";
import TrendChart from "../manufacturing/TrendChart.vue";
import Announcements from "./Announcements.vue";
import { useSession } from "../../stores/session";
const op = useOperation();
const router = useRouter();
const session = useSession();
const data = ref<any>(null);
const activities = computed(() =>
  (data.value?.activities ?? []).filter(
    (event: { message?: string }) =>
      typeof event.message === "string" && event.message.trim(),
  ),
);
const activityFormatter = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "long",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});
function activityTime(value: string) {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? "" : activityFormatter.format(parsed);
}
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
      <button
        v-if="session.can('m11.read')"
        @click="router.push('/device-check')"
      >
        设备质检 →</button
      ><button
        v-if="session.can('m10.read')"
        @click="router.push('/maintenance')"
      >
        预测性维护 →</button
      ><button
        v-if="session.can('m11.read')"
        @click="router.push('/product-check')"
      >
        产品质检 →
      </button>
    </div>
    <Announcements />
    <div
      class="two-columns"
      :class="{ 'without-activities': !activities.length }"
    >
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
      <section v-if="activities.length" class="panel">
        <div class="panel-heading"><h3>近期动态</h3></div>
        <el-timeline
          ><el-timeline-item
            v-for="event in activities"
            :key="event.id"
            :timestamp="activityTime(event.created_at)"
            >{{ event.message }}</el-timeline-item
          ></el-timeline
        >
      </section>
    </div>
    <section v-if="session.can('m11.read')" class="panel">
      <div class="panel-heading"><h3>近30天首检样本合格率</h3></div>
      <el-empty
        v-if="data?.quality_trend_status === 'scope_too_large'"
        description="请按时间或类别查看质量统计"
      >
        <el-button @click="router.push('/product-check')"
          >查看质量统计</el-button
        >
      </el-empty>
      <TrendChart
        v-else
        :rows="data?.quality_trend ?? []"
        x-field="date"
        y-field="pass_rate"
        unit="%"
      />
    </section>
  </div>
</template>

<style scoped>
.without-activities {
  grid-template-columns: minmax(0, 1fr);
}
</style>
