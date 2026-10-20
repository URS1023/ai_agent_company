<script setup lang="ts">
/** 只显示本批来源组件实际使用的字段；数值配置保留Decimal文本交服务端校验。 */
import { computed } from "vue";
import {
  sourceCalendarTypes,
  sourceCardTypes,
  sourcePresentationTypes,
} from "../sourcePresentation";
const widget = defineModel<Record<string, any>>({ required: true });
const props = defineProps<{ columns: string[]; sourceReadonly?: boolean }>();
const type = computed(() => widget.value.component);
const relevant = computed(() => sourcePresentationTypes.includes(type.value));
const calendar = computed(() => sourceCalendarTypes.includes(type.value));
const cards = computed(() => sourceCardTypes.includes(type.value));
const gauge = computed(() =>
  ["JColorGauge", "JAntvGauge"].includes(type.value),
);
const option = computed(() => widget.value.config?.option ?? {});
const mapped = computed(
  () => widget.value.binding?.query_id && !props.sourceReadonly,
);
const fields = computed(() => [
  ...(calendar.value || type.value === "JDevHistory" || cards.value
    ? [{ key: "time_field", label: "时间字段", required: !cards.value }]
    : []),
  ...(calendar.value || type.value === "JDevHistory" || cards.value
    ? [{ key: "description_field", label: "说明字段", required: false }]
    : []),
  ...(cards.value
    ? [{ key: "value_field", label: "数值字段", required: false }]
    : []),
]);
function setField(key: string, value?: string) {
  if (value) widget.value.binding[key] = value;
  else delete widget.value.binding[key];
}
function setOption(key: string, value: unknown) {
  widget.value.config ??= {};
  widget.value.config.option ??= {};
  if (value === "" || value === null || value === undefined)
    delete widget.value.config.option[key];
  else widget.value.config.option[key] = value;
}
function setGroup(group: string, key: string, value: unknown) {
  const current = { ...(option.value[group] ?? {}) };
  if (value === "" || value === null || value === undefined)
    delete current[key];
  else current[key] = value;
  setOption(group, current);
}
function setStop(index: number, key: string, value: string | null) {
  const stops = (option.value.gauge_stops ?? []).map(
    (stop: Record<string, any>) => ({ ...stop }),
  );
  stops[index][key] = value;
  setOption("gauge_stops", stops);
}
function addStop() {
  setOption("gauge_stops", [
    ...(option.value.gauge_stops ?? []),
    { value: option.value.max ?? "100", color: "#38bdf8" },
  ]);
}
function removeStop(index: number) {
  setOption(
    "gauge_stops",
    option.value.gauge_stops.filter(
      (_: unknown, item: number) => item !== index,
    ),
  );
}
</script>

<template>
  <template v-if="relevant">
    <template v-if="mapped"
      ><el-form-item
        v-for="field in fields"
        :key="field.key"
        :label="field.label"
        :required="field.required"
        ><el-select
          :model-value="widget.binding[field.key]"
          :clearable="!field.required"
          filterable
          @change="setField(field.key, $event)"
          ><el-option
            v-for="column in props.columns"
            :key="column"
            :label="column"
            :value="column" /></el-select></el-form-item
    ></template>
    <template v-if="gauge">
      <el-form-item label="仪表下限"
        ><el-input
          :model-value="option.min ?? '0'"
          @update:model-value="setOption('min', $event)"
      /></el-form-item>
      <el-form-item label="仪表上限"
        ><el-input
          :model-value="option.max ?? '100'"
          @update:model-value="setOption('max', $event)"
      /></el-form-item>
      <el-form-item
        v-for="(stop, index) in option.gauge_stops ?? []"
        :key="index"
        :label="`色段 ${Number(index) + 1}`"
        ><div class="stop-editor">
          <el-input
            :model-value="stop.value"
            aria-label="色段上限"
            @update:model-value="setStop(Number(index), 'value', $event)"
          /><el-color-picker
            :model-value="stop.color"
            @update:model-value="setStop(Number(index), 'color', $event)"
          /><el-button
            text
            aria-label="删除色段"
            @click="removeStop(Number(index))"
            >×</el-button
          >
        </div></el-form-item
      >
      <el-button
        v-if="(option.gauge_stops?.length ?? 0) < 8"
        text
        @click="addStop"
        >添加色段</el-button
      >
      <el-button
        v-if="option.gauge_stops"
        text
        @click="setOption('gauge_stops', undefined)"
        >重置色段</el-button
      >
    </template>
    <template v-if="calendar"
      ><el-form-item label="初始月份"
        ><el-date-picker
          :model-value="option.calendar?.month"
          type="month"
          value-format="YYYY-MM"
          placeholder="数据首月"
          clearable
          @update:model-value="
            setGroup('calendar', 'month', $event)
          " /></el-form-item
    ></template>
    <template v-if="cards">
      <el-form-item label="每页卡片"
        ><el-input-number
          :model-value="
            option.carousel?.page_size ?? (type === 'JCardCarousel' ? 1 : 3)
          "
          :min="1"
          :max="8"
          :precision="0"
          @update:model-value="setGroup('carousel', 'page_size', $event)"
      /></el-form-item>
      <el-form-item label="自动播放"
        ><el-switch
          :model-value="option.carousel?.autoplay === true"
          @update:model-value="setGroup('carousel', 'autoplay', $event)"
      /></el-form-item>
      <el-form-item v-if="option.carousel?.autoplay" label="间隔（秒）"
        ><el-input-number
          :model-value="option.carousel?.interval_seconds ?? 5"
          :min="3"
          :max="60"
          :precision="0"
          @update:model-value="
            setGroup('carousel', 'interval_seconds', $event)
          "
      /></el-form-item>
    </template>
  </template>
</template>

<style scoped>
.stop-editor {
  display: flex;
  align-items: center;
  gap: 5px;
  width: 100%;
}
.stop-editor > .el-input {
  flex: 1;
  min-width: 0;
}
</style>
