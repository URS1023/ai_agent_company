<script setup lang="ts">
import { computed, onMounted, reactive, ref, shallowRef, watch } from "vue";
import { api, type Entity, type ListResult } from "../../api/client";
import { useOperation } from "../../composables/useOperation";
import MaintenancePresetPicker from "./MaintenancePresetPicker.vue";
import PreparationQuerySelect from "../data/PreparationQuerySelect.vue";
import { usePreparationQueries } from "../data/usePreparationQueries";
const props = defineProps<{ busy: boolean; errors: Record<string, string> }>();
const emit = defineEmits<{
  submit: [data: Record<string, unknown>];
  cancel: [];
}>();
const op = useOperation();
const devices = ref<Entity[]>([]);
const catalog = usePreparationQueries();
const sourceMode = shallowRef("device");
const expanded = ref<string[]>([]);
const presetParameters = ref<Record<string, unknown>>({});
const form = reactive({
  name: "",
  device_id: "",
  datasource_id: "",
  sql: "",
  value_field: "",
  time_field: "",
  load_field: "",
  period_field: "",
  bin_width: 10,
  min_samples: 5,
  minimum_coverage: 0.8,
  change_threshold: 0.15,
  condition_fields: [] as string[],
});
const mappings = [
  { key: "value_field", label: "监测值" },
  { key: "time_field", label: "采样时间" },
  { key: "load_field", label: "工况负载" },
  { key: "period_field", label: "对比阶段" },
] as const;
const columns = computed(() =>
  sourceMode.value === "device"
    ? [
        ...new Set(
          [
            form.value_field,
            form.time_field,
            form.load_field,
            form.period_field,
            ...form.condition_fields,
          ].filter(Boolean),
        ),
      ]
    : catalog.columns.value,
);
const ready = computed(
  () =>
    Boolean(
      form.device_id &&
      form.datasource_id &&
      form.sql &&
      form.value_field &&
      form.time_field &&
      form.load_field,
    ) &&
    (sourceMode.value !== "query" ||
      (Boolean(catalog.queryId.value) &&
        !catalog.selecting.value &&
        !catalog.loading.value)),
);
function selectPreset(value: Record<string, unknown>) {
  // 方案只装配既有字段和参数，不能覆盖当前设备、任务名称或触发业务取数。
  for (const key of [
    "datasource_id",
    "sql",
    "value_field",
    "time_field",
    "load_field",
    "period_field",
    "bin_width",
    "min_samples",
    "minimum_coverage",
    "change_threshold",
    "condition_fields",
  ] as const) {
    if (key in value) (form as Record<string, unknown>)[key] = value[key];
  }
  presetParameters.value = (value.parameters ?? {}) as Record<string, unknown>;
  form.condition_fields = Array.isArray(value.condition_fields)
    ? value.condition_fields.filter(
        (field): field is string => typeof field === "string",
      )
    : [];
  if (!form.name)
    form.name = `${devices.value.find((device) => device.id === form.device_id)?.name ?? "设备"}维护分析`;
  expanded.value = ready.value ? [] : ["mapping"];
}
watch(
  [() => form.device_id, sourceMode],
  () => {
    form.datasource_id = "";
    form.sql = "";
    catalog.queryId.value = "";
    presetParameters.value = {};
    form.condition_fields = [];
    for (const field of mappings) form[field.key] = "";
    expanded.value = sourceMode.value === "query" ? ["mapping"] : [];
  },
  { flush: "sync" },
);
watch(sourceMode, (mode) => {
  if (mode === "query") void op.run(catalog.load);
});
watch(catalog.query, (query) => {
  if (sourceMode.value !== "query") return;
  form.datasource_id = query?.datasource_id ?? "";
  form.sql = query?.sql ?? "";
});
watch(
  () => props.errors,
  (errors) => {
    if (
      mappings.some((field) => errors[field.key]) &&
      !expanded.value.includes("mapping")
    )
      expanded.value.push("mapping");
  },
  { deep: true },
);
onMounted(() =>
  op.run(async () => {
    devices.value = (
      await api.get<ListResult<Entity>>("/devices?limit=200")
    ).items;
  }),
);
function submit() {
  if (!ready.value) return;
  emit("submit", {
    ...form,
    parameters:
      sourceMode.value === "device"
        ? presetParameters.value
        : (catalog.query.value?.parameters ?? {}),
  });
}
</script>
<template>
  <el-form
    label-position="top"
    @submit.prevent="submit"
    v-loading="op.busy.value"
  >
    <div class="form-grid">
      <el-form-item label="分析名称" :error="errors.name"
        ><el-input v-model="form.name" required maxlength="160"
      /></el-form-item>
      <el-form-item label="设备" :error="errors.device_id"
        ><el-select v-model="form.device_id" filterable
          ><el-option
            v-for="device in devices"
            :key="device.id"
            :value="device.id"
            :label="device.name" /></el-select
      ></el-form-item>
      <el-form-item label="数据来源" class="full-width"
        ><el-radio-group v-model="sourceMode"
          ><el-radio-button value="device">设备监测</el-radio-button
          ><el-radio-button value="query"
            >历史查询</el-radio-button
          ></el-radio-group
        ></el-form-item
      >
      <MaintenancePresetPicker
        v-if="sourceMode === 'device'"
        :device-id="form.device_id"
        @select="selectPreset"
      />
      <el-form-item
        v-else
        label="数据查询"
        class="full-width"
        :error="errors.datasource_id ?? errors.sql"
        ><PreparationQuerySelect :catalog="catalog"
      /></el-form-item>
    </div>
    <el-collapse v-model="expanded">
      <el-collapse-item title="字段映射" name="mapping"
        ><div class="form-grid">
          <el-form-item
            v-for="field in mappings"
            :key="field.key"
            :label="field.label"
            :error="errors[field.key]"
            ><el-select
              v-model="form[field.key]"
              filterable
              allow-create
              clearable
              ><el-option
                v-for="column in columns"
                :key="column"
                :value="column"
                :label="column" /></el-select
          ></el-form-item></div
      ></el-collapse-item>
      <el-collapse-item title="分析参数" name="parameters"
        ><div class="form-grid">
          <el-form-item label="负载分组间隔"
            ><el-input-number v-model="form.bin_width" :min="0.01" :step="0.01"
          /></el-form-item>
          <el-form-item label="每组最少样本"
            ><el-input-number v-model="form.min_samples" :min="2" :max="1000"
          /></el-form-item>
          <el-form-item label="最低工况覆盖率"
            ><el-input-number
              v-model="form.minimum_coverage"
              :min="0.01"
              :max="1"
              :step="0.01"
          /></el-form-item>
          <el-form-item label="变化阈值"
            ><el-input-number
              v-model="form.change_threshold"
              :min="0"
              :max="10"
              :step="0.01"
          /></el-form-item></div
      ></el-collapse-item>
    </el-collapse>
    <div
      class="row-actions"
      style="justify-content: flex-end; margin-top: 20px"
    >
      <el-button @click="emit('cancel')">取消</el-button
      ><el-button
        type="primary"
        native-type="submit"
        :loading="busy"
        :disabled="!ready"
        >发起分析</el-button
      >
    </div>
  </el-form>
</template>
