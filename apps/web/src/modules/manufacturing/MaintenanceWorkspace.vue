<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useSession } from "../../stores/session";
import { api, type Entity } from "../../api/client";
import { useCollection } from "../../composables/useCollection";
import { useOperation } from "../../composables/useOperation";
import MaintenanceForm from "./MaintenanceForm.vue";
import StatusTag from "../../components/common/StatusTag.vue";
import ResultTable from "../../components/common/ResultTable.vue";
import TrendChart from "./TrendChart.vue";
const session = useSession();
const list = useCollection(() => "/maintenance-analyses");
const op = useOperation();
const dialog = ref(false);
const detail = ref<Entity | null>(null);
const result = computed(() => detail.value?.result ?? {});
const bins = computed(() =>
  (result.value.bins ?? []).map((row: any) => ({
    ...row,
    range: `${row.load_min}–${row.load_max}`,
  })),
);
async function create(data: Record<string, unknown>) {
  await op.run(async () => {
    detail.value = await api.post<Entity>("/maintenance-analyses", data);
    dialog.value = false;
    await list.load();
  }, "分析已完成");
}
async function open(row: Entity) {
  await op.run(async () => {
    detail.value = await api.get(`/maintenance-analyses/${row.id}`);
  });
}
function time(value: unknown) {
  return typeof value === "number"
    ? new Date(value * 1000).toLocaleString("zh-CN", { hour12: false })
    : "—";
}
function number(value: unknown, suffix = "") {
  return typeof value === "number" ? `${value.toFixed(2)}${suffix}` : "—";
}
onMounted(list.load);
</script>
<template>
  <div class="page-stack">
    <div class="page-heading">
      <h2>预测性维护</h2>
      <div>
        <el-button @click="list.load">刷新</el-button
        ><el-button
          v-if="session.can('m10.write')"
          type="primary"
          @click="dialog = true"
          >发起分析</el-button
        >
      </div>
    </div>
    <template v-if="detail"
      ><section class="panel">
        <div class="panel-heading">
          <h3>{{ detail.name }}</h3>
          <StatusTag :value="result.status ?? detail.status" />
        </div>
        <div class="detail-grid">
          <div>
            <div class="detail-label">基准期均值</div>
            <div class="metric-value">{{ number(result.baseline_mean) }}</div>
          </div>
          <div>
            <div class="detail-label">近期均值</div>
            <div class="metric-value">{{ number(result.recent_mean) }}</div>
          </div>
          <div>
            <div class="detail-label">同工况变化</div>
            <div class="metric-value">
              {{
                result.relative_change === undefined
                  ? "—"
                  : number(result.relative_change * 100, "%")
              }}
            </div>
          </div>
        </div>
      </section>
      <div class="two-columns">
        <section class="panel">
          <div class="panel-heading"><h3>工况对比</h3></div>
          <TrendChart
            :rows="bins"
            x-field="range"
            y-field="recent_mean"
            :series="[
              { field: 'baseline_mean', name: '基准期' },
              { field: 'recent_mean', name: '近期' },
            ]"
            type="bar"
          />
        </section>
        <section class="panel">
          <div class="panel-heading"><h3>分析结论</h3></div>
          <p
            v-for="reason in result.reasons ?? []"
            :key="String(reason)"
            style="line-height: 1.9"
          >
            {{
              typeof reason === "string"
                ? reason
                : (reason.message ?? reason.reason)
            }}
          </p>
          <el-descriptions :column="1" border
            ><el-descriptions-item label="工况覆盖率">{{
              result.coverage === undefined
                ? "—"
                : number(result.coverage * 100, "%")
            }}</el-descriptions-item
            ><el-descriptions-item label="数据时间"
              >{{ time(result.time_range?.[0]) }} —
              {{ time(result.time_range?.[1]) }}</el-descriptions-item
            ><el-descriptions-item label="数据来源">{{
              detail.provenance?.type === "query"
                ? "数据库查询"
                : detail.provenance?.type === "imported_observations"
                  ? "导入观测"
                  : "—"
            }}</el-descriptions-item></el-descriptions
          >
        </section>
      </div>
      <section class="panel">
        <el-tabs
          ><el-tab-pane label="分组明细"
            ><ResultTable
              :rows="bins"
              :columns="[
                'range',
                'baseline_count',
                'recent_count',
                'baseline_mean',
                'recent_mean',
                'eligible',
              ]"
              :labels="{
                range: '工况负载',
                baseline_count: '基准样本',
                recent_count: '近期样本',
                baseline_mean: '基准均值',
                recent_mean: '近期均值',
                eligible: '可比较',
              }" /></el-tab-pane
          ><el-tab-pane label="分析证据">
            <pre class="json-preview">{{
              JSON.stringify(
                result.coverage_detail ?? detail.provenance ?? {},
                null,
                2,
              )
            }}</pre>
          </el-tab-pane></el-tabs
        >
      </section></template
    >
    <section class="panel">
      <div class="panel-heading"><h3>分析记录</h3></div>
      <el-table :data="list.items.value" stripe v-loading="list.loading.value"
        ><el-table-column
          prop="name"
          label="分析名称"
          min-width="240"
        /><el-table-column label="分析状态" width="140"
          ><template #default="{ row }"
            ><StatusTag
              :value="
                row.result?.status ?? row.status
              " /></template></el-table-column
        ><el-table-column
          prop="created_at"
          label="分析时间"
          width="190"
        /><el-table-column width="90"
          ><template #default="{ row }"
            ><el-button link type="primary" @click="open(row)"
              >查看</el-button
            ></template
          ></el-table-column
        ></el-table
      >
    </section>
    <el-dialog
      v-model="dialog"
      title="发起维护分析"
      width="760px"
      destroy-on-close
      ><MaintenanceForm
        :busy="op.busy.value"
        :errors="op.fields.value"
        @submit="create"
        @cancel="dialog = false"
    /></el-dialog>
  </div>
</template>
