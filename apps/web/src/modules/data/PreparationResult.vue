<script setup lang="ts">
import { computed, ref } from "vue";
import { api } from "../../api/client";
import { useOperation } from "../../composables/useOperation";
import ResultTable from "../../components/common/ResultTable.vue";
import {
  operationTypes,
  outcomeLabels,
  displayScalar,
} from "./dataPreparation";
const props = defineProps<{ job: Record<string, any>; metric?: boolean }>();
const rows = computed<Record<string, unknown>[]>(
  () => props.job.result?.rows ?? [],
);
const columns = computed<string[]>(() => props.job.result?.columns ?? []);
const displayRows = computed(() =>
  props.metric
    ? rows.value.map((row) => ({
        ...row,
        status: row.status
          ? (outcomeLabels[String(row.status)] ?? String(row.status))
          : row.status,
      }))
    : rows.value,
);
const sourceRows = ref<number[] | null>(null);
const steps = computed(() => props.job.result?.steps ?? []);
const hasStepDetails = computed(() =>
  steps.value.some(
    (step: Record<string, any>) =>
      step.aggregation_stats?.length || step.coerced_null_count !== undefined,
  ),
);
const op = useOperation();
async function download() {
  await op.run(async () => {
    const blob = await api.postBlob(
      `/data-processing-jobs/${props.job.id}/exports`,
      {},
    );
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${(props.job.name || "计算结果").replace(/[\\/:*?"<>|]/g, "_")}.csv`;
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
}
function stepName(value: string) {
  return operationTypes.find((x) => x.value === value)?.label ?? value;
}
</script>
<template>
  <section class="panel">
    <div class="panel-heading">
      <h3>{{ job.name || "计算结果" }}</h3>
      <div class="row-actions">
        <span>{{ job.result?.row_count ?? rows.length }} 行</span
        ><el-button :loading="op.busy.value" @click="download"
          >导出 CSV</el-button
        >
      </div>
    </div>
    <el-table
      v-if="!metric && job.result?.source_row_numbers"
      :data="rows"
      stripe
      max-height="480"
    >
      <el-table-column type="index" label="序号" width="65" />
      <el-table-column
        v-for="column in columns"
        :key="column"
        :prop="column"
        :label="column"
        min-width="145"
        show-overflow-tooltip
        ><template #default="{ row }">{{
          displayScalar(row[column])
        }}</template></el-table-column
      >
      <el-table-column label="来源行" width="100"
        ><template #default="{ $index }"
          ><el-button
            link
            type="primary"
            @click="sourceRows = job.result.source_row_numbers[$index]"
            >查看
            {{ job.result.source_row_numbers[$index]?.length ?? 0 }}
            行</el-button
          ></template
        ></el-table-column
      >
    </el-table>
    <ResultTable
      v-else
      :rows="displayRows"
      :columns="columns"
      :labels="
        metric
          ? {
              value: '指标值',
              unit: '单位',
              metric_unit: '指标单位',
              status: '状态',
              numerator: '分子',
              denominator: '分母',
            }
          : undefined
      "
    />
    <el-table
      v-if="steps.length"
      :data="steps"
      size="small"
      style="margin-top: 15px"
      ><el-table-column v-if="hasStepDetails" type="expand"
        ><template #default="{ row }"
          ><ResultTable
            v-if="row.aggregation_stats?.length"
            :rows="row.aggregation_stats"
            :columns="['name', 'input_count', 'value_count', 'missing_count']"
            :labels="{
              name: '汇总项',
              input_count: '输入行数',
              value_count: '有效值数',
              missing_count: '缺失值数',
            }"
          />
          <div v-if="row.coerced_null_count !== undefined" class="row-actions">
            <span>转为空值 {{ row.coerced_null_count }} 行</span
            ><el-button
              v-if="row.coerced_null_row_numbers?.length"
              link
              type="primary"
              @click="sourceRows = row.coerced_null_row_numbers"
              >查看行号</el-button
            >
          </div>
        </template></el-table-column
      ><el-table-column type="index" label="步骤" width="65" /><el-table-column
        label="处理"
        ><template #default="{ row }">{{
          stepName(row.type)
        }}</template></el-table-column
      ><el-table-column prop="input_rows" label="输入行数" /><el-table-column
        prop="output_rows"
        label="输出行数"
    /></el-table>
    <el-dialog
      :model-value="sourceRows !== null"
      title="来源行号"
      width="600px"
      @close="sourceRows = null"
      ><div class="source-rows">
        {{ sourceRows?.join("、") || "—" }}
      </div></el-dialog
    >
  </section>
</template>
<style scoped>
.source-rows {
  line-height: 1.8;
  max-height: 420px;
  overflow: auto;
  overflow-wrap: anywhere;
}
</style>
