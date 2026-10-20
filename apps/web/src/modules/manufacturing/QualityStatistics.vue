<script setup lang="ts">
import { computed, reactive, shallowRef, watch } from "vue";
import { api } from "../../api/client";
import { useOperation } from "../../composables/useOperation";
import { workflowLabel } from "./workflowLabels";
const props = defineProps<{ targetType?: string }>();
const op = useOperation(),
  result = shallowRef<any>(null);
const form = reactive({
  target_type: props.targetType ?? "",
  range: [] as string[],
});
const cards = computed(() =>
  result.value
    ? [
        { title: "首检", data: result.value.first_inspection },
        { title: "复检", data: result.value.reinspection },
      ]
    : [],
);
const labels = computed<Record<string, string>>(() =>
  result.value?.target_type === "device"
    ? { numeric: "性能", component: "部件", appearance: "外观" }
    : result.value?.target_type === "product"
      ? { numeric: "数值", component: "成分", appearance: "外观" }
      : {
          numeric: "数值 / 性能",
          component: "成分 / 部件",
          appearance: "外观",
        },
);
async function load() {
  await op.run(async () => {
    const query = new URLSearchParams();
    if (form.target_type) query.set("target_type", form.target_type);
    if (form.range?.[0])
      query.set("start_at", new Date(form.range[0]).toISOString());
    if (form.range?.[1])
      query.set("end_at", new Date(form.range[1]).toISOString());
    result.value = await api.get(`/quality-statistics?${query}`);
  });
}
watch(
  () => props.targetType,
  (value) => {
    form.target_type = value ?? "";
    void load();
  },
  { immediate: true },
);
</script>
<template>
  <div class="page-stack">
    <div class="panel toolbar">
      <el-select
        v-if="!targetType"
        v-model="form.target_type"
        clearable
        placeholder="全部检测"
        style="max-width: 160px"
        ><el-option value="device" label="设备质检" /><el-option
          value="product"
          label="产品质检" /></el-select
      ><el-date-picker
        v-model="form.range"
        type="datetimerange"
        start-placeholder="开始时间"
        end-placeholder="结束时间"
        format="YYYY-MM-DD HH:mm"
      /><el-button type="primary" @click="load" :loading="op.busy.value"
        >统计</el-button
      >
    </div>
    <div v-if="result" class="page-stack">
      <div class="two-columns">
        <section v-for="card in cards" :key="card.title" class="panel">
          <div class="panel-heading">
            <h3>{{ card.title }}</h3>
            <span>{{ card.data.task_count }} 个任务</span>
          </div>
          <div class="detail-grid">
            <div>
              <div class="detail-label">{{ card.title }}样本合格率</div>
              <div class="metric-value">
                {{
                  card.data.pass_rate === null ? "—" : `${card.data.pass_rate}%`
                }}
              </div>
            </div>
            <div>
              <div class="detail-label">计划 / 实际样本</div>
              <div class="detail-value">
                {{ card.data.denominator }} / {{ card.data.actual_samples }}
              </div>
            </div>
            <div>
              <div class="detail-label">合格 / 不合格</div>
              <div class="detail-value">
                {{ card.data.passed }} / {{ card.data.failed }}
              </div>
            </div>
            <div>
              <div class="detail-label">待判断 / 缺失</div>
              <div class="detail-value">
                {{ card.data.review + card.data.incomplete }} /
                {{ card.data.missing_samples }}
              </div>
            </div>
          </div>
        </section>
      </div>
      <section class="panel">
        <div class="panel-heading">
          <h3>首检指标</h3>
          <span>未检测任务 {{ result.pending_tasks }}</span>
        </div>
        <el-table :data="result.dimensions" stripe
          ><el-table-column label="维度"
            ><template #default="{ row }">{{
              labels[row.category] ?? row.category
            }}</template></el-table-column
          ><el-table-column
            prop="denominator"
            label="应检项次"
          /><el-table-column prop="passed" label="合格" /><el-table-column
            prop="failed"
            label="不合格"
          /><el-table-column prop="review" label="待复核" /><el-table-column
            prop="incomplete"
            label="缺失"
          /><el-table-column label="合格率"
            ><template #default="{ row }">{{
              row.pass_rate === null ? "—" : `${row.pass_rate}%`
            }}</template></el-table-column
          ></el-table
        >
      </section>
      <section class="panel">
        <div class="panel-heading"><h3>异常处置</h3></div>
        <el-table :data="result.dispositions"
          ><el-table-column label="处置方式"
            ><template #default="{ row }">{{
              row.name === "open" ? "处理中" : workflowLabel(row.name)
            }}</template></el-table-column
          ><el-table-column prop="value" label="处置单数"
        /></el-table>
      </section>
    </div>
  </div>
</template>
