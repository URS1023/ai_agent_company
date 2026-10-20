<script setup lang="ts">
/** 按原图裁片呈现参考示例，编辑后在固定数据区域内重绘，装饰皮肤由底图独立保留。 */
import { computed, useId } from "vue";
import JimuChart from "./JimuChart.vue";
import { scopedAssetUrl } from "@/utils/safeContent";
import { inferSeriesSchema } from "@/utils/dashboardData";
import {
  isReferenceSample,
  referenceChartOption,
} from "@/utils/referenceScreen";
import { fitSnapshotFont } from "@/utils/snapshotWidgetLayout";

const props = defineProps<{
  widget: Record<string, any>;
  data: any;
  remote?: boolean;
  error?: string;
  loading?: boolean;
}>();
const emit = defineEmits<{ "data-click": [point: any] }>();
const maskId = `reference-mask-${useId().replace(/[^a-z0-9-]/gi, "")}`;
const skin = computed(() => props.widget.referenceSkin || {});
const backdrop = computed(() => skin.value.kind === "backdrop");
const asset = computed(() =>
  scopedAssetUrl(skin.value.asset, import.meta.env.BASE_URL),
);
const sample = computed(() =>
  isReferenceSample(props.widget, props.data, props.remote),
);
const rows = computed<any[]>(() =>
  Array.isArray(props.data) ? props.data : [],
);
const empty = computed(
  () =>
    !rows.value.length &&
    !(skin.value.kind === "text" && props.widget.config?.option?.body?.text),
);
const bounds = computed(
  () => skin.value.rect || [0, 0, props.widget.w, props.widget.h],
);
const imageViewBox = computed(() => bounds.value.join(" "));
const style = computed(() => ({
  background:
    sample.value || backdrop.value
      ? "transparent"
      : skin.value.surface || skin.value.background,
  color: skin.value.textColor || "#eaf8ff",
  "--reference-color": skin.value.color,
  "--reference-font-size": `${skin.value.fontSize || 10}px`,
  "--reference-value-size": `${skin.value.valueFontSize || 20}px`,
  "--reference-align": skin.value.align || "center",
}));
const first = computed(() => rows.value[0] || {});
const schema = computed(() =>
  inferSeriesSchema(
    rows.value,
    props.widget.config?.snapshotBinding || props.widget.config?.dataMapping,
  ),
);
const metricValue = computed(
  () =>
    first.value.value ??
    first.value[schema.value.numericKeys[0]] ??
    (Array.isArray(first.value) ? first.value[1] : ""),
);
const textValue = computed(() =>
  typeof first.value === "string" || typeof first.value === "number"
    ? first.value
    : (first.value.value ??
      first.value.text ??
      first.value.content ??
      props.widget.config?.option?.body?.text ??
      ""),
);
const unit = computed(() => first.value.unit || first.value.suffix || "");
const valueStyle = computed(() => ({
  fontSize: `${fitSnapshotFont(
    `${metricValue.value}${unit.value}`,
    props.widget.w - 2,
    props.widget.h - (skin.value.label ? (skin.value.fontSize || 10) + 2 : 2),
    skin.value.valueFontSize || 20,
    1.15,
  )}px`,
}));
const headers = computed(() =>
  Array.isArray(first.value)
    ? first.value.map((_: any, index: number) => `列${index + 1}`)
    : Object.keys(first.value),
);
const cells = computed(() =>
  rows.value.map((row) =>
    Array.isArray(row) ? row : headers.value.map((key: string) => row[key]),
  ),
);
const chartWidget = computed(() => ({
  ...props.widget,
  visualMode: "ai",
  panelVariant: "",
  frameMode: "none",
  chromeMode: "none",
  config: {
    ...props.widget.config,
    option: referenceChartOption(props.widget, props.data),
  },
}));
</script>

<template>
  <div
    class="reference-widget"
    :style="style"
    :data-reference-state="
      backdrop
        ? 'decoration'
        : error
          ? 'error'
          : loading
            ? 'loading'
            : sample
              ? 'sample'
              : empty
                ? 'empty'
                : 'data'
    "
  >
    <svg
      v-if="backdrop || (sample && !error && !loading)"
      class="reference-image"
      :viewBox="imageViewBox"
      preserveAspectRatio="none"
      role="img"
      :aria-label="`${widget.componentName}${backdrop ? '' : '（参考图示例）'}`"
    >
      <defs v-if="backdrop">
        <mask
          :id="maskId"
          maskUnits="userSpaceOnUse"
          :x="0"
          :y="0"
          :width="skin.sourceWidth"
          :height="skin.sourceHeight"
          style="mask-type: luminance"
        >
          <rect
            :width="skin.sourceWidth"
            :height="skin.sourceHeight"
            fill="white"
          />
          <rect
            v-for="(rect, index) in skin.holes"
            :key="index"
            :x="rect[0]"
            :y="rect[1]"
            :width="rect[2]"
            :height="rect[3]"
            fill="black"
          />
        </mask>
      </defs>
      <image
        :href="asset"
        :width="skin.sourceWidth"
        :height="skin.sourceHeight"
        :mask="backdrop ? `url(#${maskId})` : undefined"
      />
    </svg>
    <div
      v-else-if="error || loading || empty"
      class="reference-state"
      :role="error ? 'alert' : 'status'"
    >
      {{
        error
          ? `数据读取失败：${error}`
          : loading
            ? "正在查询数据…"
            : widget.snapshotContext
              ? "本次未绑定"
              : "暂无数据"
      }}
    </div>
    <div v-else-if="skin.kind === 'text'" class="reference-text">
      {{ textValue }}
    </div>
    <div
      v-else-if="skin.kind === 'metric'"
      class="reference-metric"
      @click="emit('data-click', { data: first })"
    >
      <span v-if="skin.label" class="reference-metric-label">{{
        first.name || widget.componentName
      }}</span>
      <strong :style="valueStyle"
        >{{ metricValue }}<small v-if="unit">{{ unit }}</small></strong
      >
    </div>
    <div v-else-if="skin.kind === 'table'" class="reference-table-wrap">
      <table class="reference-table">
        <thead>
          <tr>
            <th v-for="header in headers" :key="header">{{ header }}</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="(row, index) in cells"
            :key="index"
            @click="emit('data-click', { dataIndex: index, data: rows[index] })"
          >
            <td
              v-for="(cell, column) in row"
              :key="column"
              :title="String(cell ?? '')"
            >
              {{ cell }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <JimuChart
      v-else
      :widget="chartWidget"
      :data="data"
      @data-click="emit('data-click', $event)"
    />
  </div>
</template>

<style scoped>
.reference-widget {
  width: 100%;
  height: 100%;
  overflow: hidden;
  position: relative;
  font-family: "Microsoft YaHei", sans-serif;
  font-size: var(--reference-font-size);
}
.reference-image {
  display: block;
  width: 100%;
  height: 100%;
  overflow: hidden;
}
.reference-state {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  padding: 3px;
  box-sizing: border-box;
  font-size: min(12px, var(--reference-font-size));
  overflow-wrap: anywhere;
}
.reference-text,
.reference-metric {
  display: flex;
  justify-content: var(--reference-align);
  align-items: center;
  width: 100%;
  height: 100%;
  line-height: 1.15;
  text-align: var(--reference-align);
  overflow-wrap: anywhere;
}
.reference-text {
  font-weight: 600;
  font-size: var(--reference-font-size);
}
.reference-metric {
  flex-direction: column;
  font-variant-numeric: tabular-nums;
}
.reference-metric strong {
  color: var(--reference-color);
  font-size: var(--reference-value-size);
  font-weight: 600;
  max-width: 100%;
  white-space: nowrap;
}
.reference-metric small {
  font-size: 0.45em;
  margin-left: 0.3em;
}
.reference-metric-label {
  font-size: var(--reference-font-size);
}
.reference-table-wrap {
  height: 100%;
  overflow: auto;
  scrollbar-width: thin;
}
.reference-table {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
  font-size: var(--reference-font-size);
}
.reference-table th {
  background: color-mix(in srgb, var(--reference-color) 20%, transparent);
  text-align: left;
  font-weight: 500;
}
.reference-table td,
.reference-table th {
  padding: 0.7em 0.45em;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  border-bottom: 1px solid var(--surface)0b;
}
.reference-table tr:nth-child(even) {
  background: var(--surface)05;
}
</style>
