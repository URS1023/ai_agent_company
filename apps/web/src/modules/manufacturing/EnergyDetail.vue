<script setup lang="ts">
import { computed, shallowRef, watch } from "vue";
import type { Entity } from "../../api/client";
import { useSession } from "../../stores/session";
const props = defineProps<{ analysis: Entity; busy?: boolean }>();
const emit = defineEmits<{
  back: [];
  rerun: [];
  rename: [];
  remove: [];
  export: [];
}>();
const session = useSession();
const result = computed(() => props.analysis.result ?? {}),
  config = computed(() => props.analysis.request_snapshot ?? {});
const page = shallowRef(1);
const bins = computed(() =>
  (result.value.bins ?? []).slice((page.value - 1) * 50, page.value * 50),
);
watch(
  () => props.analysis.id,
  () => {
    page.value = 1;
  },
);
const modeLabels: Record<string, string> = {
  interval: "期间电量",
  power: "平均功率 × 运行时长",
  cumulative: "累计电表差分",
};
const statusLabels: Record<string, string> = {
  insufficient_data: "数据不足",
  opportunity: "存在能耗差额",
  no_observed_gap: "未见增加",
};
const issueLabels: Record<string, string> = {
  other_device: "其他设备记录",
  invalid_time: "时间缺失或无时区",
  unknown_period: "未匹配对比期",
  invalid_energy: "电量无效",
  missing_or_invalid_production: "产量缺失或无效",
  unit_mismatch: "单位不匹配",
  missing_condition: "工况缺失",
  missing_or_invalid_duration: "运行时长无效",
  duplicate_rows: "重复记录",
  conflicting_timestamps: "同时间记录冲突",
  counter_anchors: "累计表起始读数",
  counter_condition_boundaries: "电表区间跨工况",
  counter_resets: "累计表重置",
};
const coverageLabels: Record<string, string> = {
  valid_samples: "有效区间",
  matched_recent_samples: "近期生产样本",
  matched_recent_production: "近期产量",
  time: "时间连续性",
  period_order: "两期时间顺序",
};
const issues = computed(() =>
  Object.entries(result.value.issues ?? {})
    .filter(([, value]) => Number(value) > 0)
    .map(([key, value]) => ({ name: issueLabels[key] ?? key, value })),
);
function numeric(value: unknown, digits = 4) {
  if (value === null || value === undefined) return "未估算";
  const raw = String(value),
    parsed = Number(raw);
  if (
    !Number.isFinite(parsed) ||
    (parsed === 0 && !/^[-+]?0(?:\.0*)?$/.test(raw))
  )
    return scientific(raw);
  if (parsed !== 0 && (Math.abs(parsed) < 0.0001 || Math.abs(parsed) >= 1e9))
    return parsed.toExponential(5);
  return parsed.toLocaleString("zh-CN", { maximumFractionDigits: digits });
}
function scientific(raw: string) {
  const negative = raw.startsWith("-"),
    unsigned = raw.replace(/^[-+]/, "");
  const [whole = "0", fraction = ""] = unsigned.split(".");
  const integer = whole.replace(/^0+/, "");
  const exponent = integer
    ? integer.length - 1
    : -(fraction.search(/[1-9]/) + 1);
  const significant = (integer + fraction).replace(/^0+/, "");
  return significant
    ? `${negative ? "-" : ""}${significant[0]}.${significant.slice(1, 6).padEnd(5, "0")}e${exponent >= 0 ? "+" : ""}${exponent}`
    : raw;
}
function percentage(value: unknown) {
  if (value === null || value === undefined) return "—";
  const raw = String(value),
    sign = raw.startsWith("-") ? "-" : "";
  const [whole = "0", fraction = ""] = raw.replace(/^[-+]/, "").split(".");
  return `${numeric(`${sign}${whole}${fraction.padEnd(2, "0").slice(0, 2)}.${fraction.slice(2) || "0"}`, 2)}%`;
}
function localTime(value?: string) {
  return value ? value.replace("T", " ").replace("+08:00", "") : "—";
}
const periodRows = computed(() =>
  ["baseline", "recent"].map((name) => {
    const period = result.value.periods?.[name];
    return {
      name: name === "baseline" ? "基准期" : "近期",
      first: localTime(period?.interval_start_local ?? period?.first_local),
      last: localTime(period?.last_local),
      raw_first: period?.interval_start_original ?? period?.first_original,
      raw_last: period?.last_original,
      day_start: localTime(period?.containing_days_start_local),
      day_end: localTime(period?.containing_days_end_exclusive_local),
    };
  }),
);
function evidence(suggestion: any) {
  const value = suggestion.evidence ?? {};
  if (suggestion.kind === "unit_energy_gap")
    return `${value.bin_ids.length} 个工况组：${value.bin_ids
      .slice(0, 3)
      .map((id: string) => {
        const bin = result.value.bins.find((row: any) => row.id === id);
        return bin
          ? `${bin.load_min}–${bin.load_max}${Object.values(bin.conditions).length ? ` / ${Object.values(bin.conditions).join(" · ")}` : ""}`
          : id;
      })
      .join("；")}${value.bin_ids.length > 3 ? "…" : ""}`;
  if (suggestion.kind === "zero_output_energy")
    return `${value.sample_count}条观测，${numeric(value.energy_kwh)} kWh`;
  if (suggestion.kind === "price_completion")
    return `${value.priced_samples} / ${value.required_samples}条匹配记录有有效电价`;
  return `匹配覆盖率 ${percentage(value.coverage)}`;
}
</script>
<template>
  <div class="page-stack" v-loading="busy">
    <div class="page-heading">
      <div class="row-actions">
        <el-button @click="emit('back')">返回</el-button>
        <h2>{{ analysis.name }}</h2>
        <el-tag
          :type="
            result.status === 'opportunity'
              ? 'warning'
              : result.status === 'insufficient_data'
                ? 'info'
                : 'success'
          "
          >{{ statusLabels[result.status] ?? result.status }}</el-tag
        >
      </div>
      <div class="row-actions">
        <el-button
          v-if="session.can('m10.write') && session.can('m06.read')"
          @click="emit('rerun')"
          >重新分析</el-button
        ><el-button v-if="session.can('m10.write')" @click="emit('rename')"
          >改名</el-button
        ><el-button @click="emit('export')">导出数据</el-button
        ><el-button
          v-if="session.can('m10.delete')"
          type="danger"
          plain
          @click="emit('remove')"
          >删除</el-button
        >
      </div>
    </div>
    <section class="panel">
      <div class="energy-metrics">
        <div>
          <div class="detail-label">
            基准单位能耗（kWh/{{ result.production_unit }}）
          </div>
          <div class="energy-value" :title="result.baseline_sec">
            {{ numeric(result.baseline_sec) }}
          </div>
        </div>
        <div>
          <div class="detail-label">
            近期单位能耗（kWh/{{ result.production_unit }}）
          </div>
          <div class="energy-value" :title="result.recent_sec">
            {{ numeric(result.recent_sec) }}
          </div>
        </div>
        <div>
          <div class="detail-label">同工况变化</div>
          <div class="energy-value">
            {{ percentage(result.relative_change) }}
          </div>
        </div>
        <div>
          <div class="detail-label">匹配样本电量空间（kWh）</div>
          <div class="energy-value" :title="result.saving_space_kwh">
            {{ numeric(result.saving_space_kwh) }}
          </div>
        </div>
        <div>
          <div class="detail-label">匹配样本金额空间（元）</div>
          <div class="energy-value" :title="result.economics?.saving_space_cny">
            {{ numeric(result.economics?.saving_space_cny, 2) }}
          </div>
        </div>
        <div>
          <div class="detail-label">比较覆盖率</div>
          <div class="energy-value">{{ percentage(result.coverage) }}</div>
        </div>
      </div>
      <el-alert
        v-for="reason in result.reasons ?? []"
        :key="reason"
        :title="reason"
        type="info"
        :closable="false"
        class="energy-reason"
      />
    </section>
    <section v-if="result.suggestions?.length" class="panel">
      <div class="panel-heading"><h3>建议与依据</h3></div>
      <el-table :data="result.suggestions" stripe
        ><el-table-column
          prop="title"
          label="建议"
          min-width="210" /><el-table-column label="计算证据" min-width="240"
          ><template #default="{ row }">{{
            evidence(row)
          }}</template></el-table-column
        ><el-table-column prop="boundary" label="适用范围" min-width="300"
      /></el-table>
    </section>
    <section v-if="result.economics?.projection" class="panel">
      <div class="panel-heading"><h3>计划工时情景</h3></div>
      <el-descriptions :column="3" border
        ><el-descriptions-item label="计划运行"
          >{{
            result.economics.projection.planned_hours
          }}
          小时</el-descriptions-item
        ><el-descriptions-item label="电量空间"
          >{{
            numeric(result.economics.projection.saving_space_kwh)
          }}
          kWh</el-descriptions-item
        ><el-descriptions-item label="金额空间"
          >{{
            numeric(result.economics.projection.saving_space_cny, 2)
          }}
          元</el-descriptions-item
        ><el-descriptions-item label="估算依据" :span="3">{{
          result.economics.projection.basis
        }}</el-descriptions-item></el-descriptions
      >
    </section>
    <section class="panel">
      <el-tabs
        ><el-tab-pane label="同工况明细"
          ><el-table :data="bins" stripe
            ><el-table-column label="负载区间" min-width="125"
              ><template #default="{ row }"
                >{{ row.load_min }}–{{ row.load_max }}</template
              ></el-table-column
            ><el-table-column
              v-for="field in config.condition_fields ?? []"
              :key="field"
              :label="field"
              min-width="100"
              ><template #default="{ row }">{{
                row.conditions[field]
              }}</template></el-table-column
            ><el-table-column label="基准 / 近期样本" min-width="145"
              ><template #default="{ row }"
                >{{ row.baseline_count }} / {{ row.recent_count }}</template
              ></el-table-column
            ><el-table-column label="基准单位能耗" min-width="150"
              ><template #default="{ row }"
                ><span :title="row.baseline_sec">{{
                  numeric(row.baseline_sec)
                }}</span></template
              ></el-table-column
            ><el-table-column label="近期单位能耗" min-width="150"
              ><template #default="{ row }"
                ><span :title="row.recent_sec">{{
                  numeric(row.recent_sec)
                }}</span></template
              ></el-table-column
            ><el-table-column label="近期产量" min-width="140"
              ><template #default="{ row }"
                ><span :title="row.recent_production"
                  >{{ numeric(row.recent_production) }}
                  {{ result.production_unit }}</span
                ></template
              ></el-table-column
            ><el-table-column label="电量差额（kWh）" min-width="160"
              ><template #default="{ row }"
                ><span :title="row.energy_gap_kwh">{{
                  row.energy_gap_kwh === null
                    ? "—"
                    : numeric(row.energy_gap_kwh)
                }}</span></template
              ></el-table-column
            ><el-table-column label="可比较" width="95"
              ><template #default="{ row }"
                ><el-tag
                  :type="row.eligible ? 'success' : 'info'"
                  size="small"
                  >{{ row.eligible ? "是" : "否" }}</el-tag
                ></template
              ></el-table-column
            ></el-table
          ><el-pagination
            v-model:current-page="page"
            :total="result.bins?.length ?? 0"
            :page-size="50"
            layout="total,prev,pager,next"
          /> </el-tab-pane
        ><el-tab-pane label="数据覆盖"
          ><el-descriptions :column="3" border
            ><el-descriptions-item label="来源 / 本设备"
              >{{ result.source_rows }} /
              {{ result.scoped_rows }}</el-descriptions-item
            ><el-descriptions-item label="有效区间"
              >{{ result.valid_count }} /
              {{ result.candidate_intervals }}</el-descriptions-item
            ><el-descriptions-item label="近期匹配样本">{{
              result.matched_recent_count
            }}</el-descriptions-item
            ><el-descriptions-item
              v-for="(value, key) in result.coverage_detail ?? {}"
              :key="key"
              :label="coverageLabels[String(key)] ?? key"
              >{{ percentage(value) }}</el-descriptions-item
            ><el-descriptions-item label="匹配时段电价"
              >{{ result.economics?.priced_samples }} /
              {{
                result.economics?.required_price_samples
              }}</el-descriptions-item
            ><el-descriptions-item label="运行小时">{{
              numeric(result.economics?.runtime_hours)
            }}</el-descriptions-item></el-descriptions
          ><el-table :data="issues" stripe class="energy-issues"
            ><el-table-column prop="name" label="数据情况" /><el-table-column
              prop="value"
              label="记录数"
              width="130" /></el-table></el-tab-pane
        ><el-tab-pane label="来源与时间"
          ><el-descriptions :column="2" border
            ><el-descriptions-item label="设备">{{
              analysis.device_name
            }}</el-descriptions-item
            ><el-descriptions-item label="数据查询">{{
              analysis.provenance?.source_query_name || "已授权查询"
            }}</el-descriptions-item
            ><el-descriptions-item label="计量类型">{{
              modeLabels[config.energy_mode]
            }}</el-descriptions-item
            ><el-descriptions-item label="时区">{{
              result.timezone
            }}</el-descriptions-item
            ><el-descriptions-item label="产量字段">{{
              config.production_field
            }}</el-descriptions-item
            ><el-descriptions-item label="电量 / 功率字段"
              >{{ config.energy_field }}（{{
                config.energy_unit
              }}）</el-descriptions-item
            ></el-descriptions
          ><el-table :data="periodRows" stripe class="energy-issues"
            ><el-table-column
              prop="name"
              label="对比期"
              width="90" /><el-table-column
              prop="first"
              label="观测起点（上海）"
              min-width="190" /><el-table-column
              prop="last"
              label="观测终点（上海）"
              min-width="190" /><el-table-column
              prop="raw_first"
              label="原始起点"
              min-width="200" /><el-table-column
              prop="raw_last"
              label="原始终点"
              min-width="200" /></el-table
          ><el-table :data="periodRows" stripe class="energy-issues"
            ><el-table-column
              prop="name"
              label="对比期"
              width="90" /><el-table-column
              prop="day_start"
              label="所处自然日起点（含）"
              min-width="210" /><el-table-column
              prop="day_end"
              label="所处自然日终点（不含）"
              min-width="210"
          /></el-table>
          <p class="time-basis">{{ result.time_basis }}</p></el-tab-pane
        ></el-tabs
      >
    </section>
  </div>
</template>
<style scoped>
.energy-metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 26px 24px;
}
.energy-value {
  font-size: 26px;
  font-weight: 600;
  color: #1e293b;
  overflow-wrap: anywhere;
  margin-top: 8px;
}
.energy-reason,
.energy-issues {
  margin-top: 18px;
}
.time-basis {
  line-height: 1.7;
  margin: 16px 0 0;
  color: #475569;
}
@media (max-width: 900px) {
  .energy-metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
