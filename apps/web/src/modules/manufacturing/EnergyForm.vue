<script setup lang="ts">
import { computed, onMounted, reactive, shallowRef, watch } from "vue";
import type { Entity } from "../../api/client";
import { useOperation } from "../../composables/useOperation";
import { allRecords } from "./workflowOptions";
const props = defineProps<{
  initial?: Record<string, any>;
  busy?: boolean;
  errors?: Record<string, string>;
}>();
const emit = defineEmits<{
  submit: [data: Record<string, unknown>];
  cancel: [];
}>();
const load = useOperation(),
  devices = shallowRef<Entity[]>([]),
  queries = shallowRef<Entity[]>([]);
const form = reactive({
  name: "节能分析",
  device_id: "",
  query_id: "",
  time_field: "timestamp",
  period_field: "period",
  baseline_value: "baseline",
  recent_value: "recent",
  energy_field: "energy",
  energy_mode: "interval",
  energy_unit: "kWh",
  energy_unit_field: "",
  production_field: "production",
  production_unit: "件",
  production_unit_field: "",
  duration_field: "",
  duration_unit: "h",
  price_field: "",
  load_field: "load",
  load_bin_width: "10",
  condition_fields: [] as string[],
  device_field: "",
  device_value: "",
  min_samples: 5,
  minimum_coverage_percent: 80,
  expected_interval_seconds: undefined as number | undefined,
  planned_hours: "",
  ...props.initial,
});
if (props.initial?.minimum_coverage)
  form.minimum_coverage_percent = Number(props.initial.minimum_coverage) * 100;
if (form.expected_interval_seconds === null)
  form.expected_interval_seconds = undefined;
for (const name of [
  "energy_unit_field",
  "production_unit_field",
  "duration_field",
  "price_field",
  "device_field",
  "device_value",
  "planned_hours",
] as const)
  if (form[name] === null) form[name] = "";
const query = computed(() =>
  queries.value.find((row) => row.id === form.query_id),
);
const columns = computed<string[]>(() => query.value?.result?.columns ?? []);
const periods = computed<string[]>(() => [
  ...new Set<string>(
    (query.value?.result?.rows ?? [])
      .map((row: any) => String(row[form.period_field] ?? ""))
      .filter(Boolean),
  ),
]);
const energyUnits = computed(() =>
  form.energy_mode === "power" ? ["kW", "W"] : ["kWh", "Wh", "MWh"],
);
const critical = [
  "time_field",
  "period_field",
  "energy_field",
  "production_field",
  "load_field",
] as const;
const mappings = computed(() => [
  { key: "time_field" as const, label: "观测时间字段" },
  { key: "period_field" as const, label: "对比期字段" },
  {
    key: "energy_field" as const,
    label:
      form.energy_mode === "power"
        ? "平均功率字段"
        : form.energy_mode === "cumulative"
          ? "电表读数字段"
          : "期间电量字段",
  },
  { key: "production_field" as const, label: "同期产量字段" },
  { key: "load_field" as const, label: "负载字段" },
]);
function available(field: string) {
  return columns.value.filter(
    (column) =>
      !critical.some((name) => name !== field && form[name] === column),
  );
}
const ready = computed(
  () =>
    !!form.name.trim() &&
    !!form.device_id &&
    !!form.query_id &&
    critical.every((name) => columns.value.includes(form[name])) &&
    form.baseline_value !== form.recent_value &&
    periods.value.includes(form.baseline_value) &&
    periods.value.includes(form.recent_value) &&
    !!form.production_unit.trim() &&
    (form.energy_mode !== "power" || !!form.duration_field) &&
    (!form.device_field || !!form.device_value),
);
watch(
  () => form.energy_mode,
  () => {
    if (!energyUnits.value.includes(form.energy_unit))
      form.energy_unit = energyUnits.value[0]!;
  },
);
watch(
  () => form.query_id,
  () => {
    for (const field of [
      ...critical,
      "energy_unit_field",
      "production_unit_field",
      "duration_field",
      "price_field",
      "device_field",
    ] as const)
      if (!columns.value.includes(form[field])) form[field] = "";
    form.condition_fields = [];
  },
);
watch(periods, (values) => {
  if (!values.includes(form.baseline_value)) form.baseline_value = "";
  if (!values.includes(form.recent_value)) form.recent_value = "";
});
watch(
  () => form.device_field,
  (value) => {
    if (!value) form.device_value = "";
  },
);
watch(
  () => form.duration_field,
  (value) => {
    if (!value) form.planned_hours = "";
  },
);
function submit() {
  const data: Record<string, unknown> = {
    ...form,
    minimum_coverage: String(form.minimum_coverage_percent / 100),
  };
  delete data.minimum_coverage_percent;
  for (const field of [
    "energy_unit_field",
    "production_unit_field",
    "duration_field",
    "price_field",
    "device_field",
    "device_value",
    "planned_hours",
  ])
    if (!data[field]) data[field] = null;
  emit("submit", data);
}
onMounted(() =>
  load.run(async () => {
    [devices.value, queries.value] = await Promise.all([
      allRecords("/devices"),
      allRecords("/queries"),
    ]);
  }),
);
</script>
<template>
  <el-form
    label-position="top"
    @submit.prevent="submit"
    v-loading="load.busy.value"
    ><el-alert
      v-if="load.error.value"
      type="error"
      :title="load.error.value"
      :closable="false"
    />
    <div class="form-grid">
      <el-form-item label="分析名称" :error="errors?.name"
        ><el-input v-model="form.name" required maxlength="160" /></el-form-item
      ><el-form-item label="设备" :error="errors?.device_id"
        ><el-select v-model="form.device_id" filterable
          ><el-option
            v-for="row in devices"
            :key="row.id"
            :value="row.id"
            :label="row.name" /></el-select></el-form-item
      ><el-form-item label="数据查询" :error="errors?.query_id"
        ><el-select v-model="form.query_id" filterable
          ><el-option
            v-for="row in queries"
            :key="row.id"
            :value="row.id"
            :label="row.name" /></el-select></el-form-item
      ><el-form-item label="计量类型"
        ><el-select v-model="form.energy_mode"
          ><el-option value="interval" label="期间电量" /><el-option
            value="power"
            label="平均功率 × 运行时长" /><el-option
            value="cumulative"
            label="累计电表读数" /></el-select
      ></el-form-item>
    </div>
    <div class="form-grid">
      <el-form-item
        v-for="mapping in mappings"
        :key="mapping.key"
        :label="mapping.label"
        :error="errors?.[mapping.key]"
        ><el-select v-model="form[mapping.key]" filterable
          ><el-option
            v-for="column in available(mapping.key)"
            :key="column"
            :value="column"
            :label="column" /></el-select></el-form-item
      ><el-form-item label="电量 / 功率单位"
        ><el-select v-model="form.energy_unit"
          ><el-option
            v-for="unit in energyUnits"
            :key="unit"
            :value="unit"
            :label="unit" /></el-select></el-form-item
      ><el-form-item label="产量单位" :error="errors?.production_unit"
        ><el-input
          v-model="form.production_unit"
          required
          maxlength="40" /></el-form-item
      ><el-form-item label="基准期标记"
        ><el-select v-model="form.baseline_value"
          ><el-option
            v-for="value in periods.filter(
              (value) => value !== form.recent_value,
            )"
            :key="value"
            :value="value"
            :label="value" /></el-select></el-form-item
      ><el-form-item label="近期标记"
        ><el-select v-model="form.recent_value"
          ><el-option
            v-for="value in periods.filter(
              (value) => value !== form.baseline_value,
            )"
            :key="value"
            :value="value"
            :label="value" /></el-select></el-form-item
      ><el-form-item
        v-if="form.energy_mode === 'power'"
        label="运行时长字段"
        :error="errors?.duration_field"
        ><el-select v-model="form.duration_field"
          ><el-option
            v-for="column in columns"
            :key="column"
            :value="column"
            :label="column" /></el-select
      ></el-form-item>
    </div>
    <el-collapse
      ><el-collapse-item title="比较条件与金额" name="options"
        ><div class="form-grid">
          <el-form-item label="同工况字段"
            ><el-select v-model="form.condition_fields" multiple filterable
              ><el-option
                v-for="column in columns.filter(
                  (column) => !critical.some((key) => form[key] === column),
                )"
                :key="column"
                :value="column"
                :label="column" /></el-select></el-form-item
          ><el-form-item label="负载分箱宽度" :error="errors?.load_bin_width"
            ><el-input v-model="form.load_bin_width" required /></el-form-item
          ><el-form-item label="每期每组最少样本"
            ><el-input-number
              v-model="form.min_samples"
              :min="2"
              :max="1000"
              :precision="0" /></el-form-item
          ><el-form-item label="最低覆盖率（%）"
            ><el-input-number
              v-model="form.minimum_coverage_percent"
              :min="1"
              :max="100"
              :precision="1" /></el-form-item
          ><el-form-item
            v-if="form.energy_mode !== 'power'"
            label="运行时长字段"
            ><el-select v-model="form.duration_field" clearable
              ><el-option
                v-for="column in columns"
                :key="column"
                :value="column"
                :label="column" /></el-select></el-form-item
          ><el-form-item label="时长单位"
            ><el-select v-model="form.duration_unit"
              ><el-option value="h" label="小时" /><el-option
                value="min"
                label="分钟" /><el-option
                value="s"
                label="秒" /></el-select></el-form-item
          ><el-form-item label="电价字段（元/kWh）"
            ><el-select v-model="form.price_field" clearable
              ><el-option
                v-for="column in columns"
                :key="column"
                :value="column"
                :label="column" /></el-select></el-form-item
          ><el-form-item label="计划运行小时" :error="errors?.planned_hours"
            ><el-input
              v-model="form.planned_hours"
              :disabled="!form.duration_field"
              clearable /></el-form-item
          ><el-form-item label="采样间隔（秒）"
            ><el-input-number
              v-model="form.expected_interval_seconds"
              :min="1"
              :precision="0" /></el-form-item
          ><el-form-item label="电量 / 功率单位字段"
            ><el-select v-model="form.energy_unit_field" clearable
              ><el-option
                v-for="column in columns"
                :key="column"
                :value="column"
                :label="column" /></el-select></el-form-item
          ><el-form-item label="产量单位字段"
            ><el-select v-model="form.production_unit_field" clearable
              ><el-option
                v-for="column in columns"
                :key="column"
                :value="column"
                :label="column" /></el-select></el-form-item
          ><el-form-item label="设备标识字段"
            ><el-select v-model="form.device_field" clearable
              ><el-option
                v-for="column in columns"
                :key="column"
                :value="column"
                :label="column" /></el-select></el-form-item
          ><el-form-item v-if="form.device_field" label="设备原始编号"
            ><el-input v-model="form.device_value" required maxlength="200"
          /></el-form-item></div></el-collapse-item
    ></el-collapse>
    <div class="row-actions energy-form-actions">
      <el-button @click="emit('cancel')">取消</el-button
      ><el-button
        type="primary"
        native-type="submit"
        :loading="busy"
        :disabled="!ready"
        >开始分析</el-button
      >
    </div></el-form
  >
</template>
<style scoped>
.energy-form-actions {
  justify-content: flex-end;
  margin-top: 22px;
}
</style>
