<script setup lang="ts">
import {
  computed,
  shallowRef,
  watch,
  onBeforeUnmount,
  useTemplateRef,
} from "vue";
import { use } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { LineChart, BarChart, PieChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  TitleComponent,
} from "echarts/components";
import VChart, { type Exposed } from "vue-echarts";
use([
  CanvasRenderer,
  LineChart,
  BarChart,
  PieChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  TitleComponent,
]);
const props = withDefaults(
  defineProps<{
    rows: Record<string, any>[];
    xField: string;
    yField: string;
    series?: { field: string; name: string }[];
    unit?: string;
    type?: "line" | "bar";
    height?: number;
  }>(),
  { height: 300, type: "line", unit: "" },
);
const emit = defineEmits<{ finished: [] }>();
const chart = useTemplateRef<Exposed>("chart");
const rendering = shallowRef(true);
let disposed = false;
onBeforeUnmount(() => {
  disposed = true;
});
const option = computed(() => ({
  animation: !matchMedia("(prefers-reduced-motion: reduce)").matches,
  tooltip: { trigger: "axis" },
  legend: props.series
    ? { top: 0, textStyle: { color: "#7b8aa6" } }
    : undefined,
  grid: { left: 55, right: 25, top: 35, bottom: 45 },
  xAxis: {
    type: "category",
    data: props.rows.map((x) => x[props.xField]),
    axisLine: { lineStyle: { color: "#e3e9f4" } },
    axisLabel: { color: "#7b8aa6" },
  },
  yAxis: {
    type: "value",
    scale: props.type !== "bar",
    name: props.unit,
    splitLine: { lineStyle: { color: "#eef2fa" } },
    axisLabel: { color: "#7b8aa6" },
  },
  series: (props.series ?? [{ field: props.yField, name: "" }]).map(
    (series, index) => ({
      name: series.name,
      type: props.type,
      data: props.rows.map((x) => x[series.field]),
      showSymbol: props.rows.length < 30,
      smooth: false,
      color: index === 0 ? "#4f5bff" : "#06b6d4",
      lineStyle: { width: 2 },
      areaStyle:
        props.type === "line" ? { color: "rgba(38,102,251,.04)" } : undefined,
    }),
  ),
}));
watch(
  option,
  () => {
    rendering.value = true;
  },
  { flush: "sync" },
);
function finished() {
  if (disposed) return;
  // 旧帧结束事件不能把尚未应用的新数据误标成已完成。
  const applied = chart.value?.getOption() as
    | {
        xAxis?: { data?: unknown[] }[];
        series?: { data?: unknown[] }[];
      }
    | undefined;
  if (
    !applied ||
    JSON.stringify(applied.xAxis?.[0]?.data) !==
      JSON.stringify(option.value.xAxis.data)
  )
    return;
  if (
    JSON.stringify(applied.series?.map((item) => item.data)) !==
    JSON.stringify(option.value.series.map((item) => item.data))
  )
    return;
  rendering.value = false;
  emit("finished");
}
</script>
<template>
  <VChart
    v-if="rows.length"
    ref="chart"
    class="trend-chart"
    role="img"
    :aria-label="series?.map((item) => item.name).join('、') || '趋势图'"
    :aria-busy="rendering"
    :option="option"
    @finished="finished"
    autoresize
    :style="{ height: `${height}px` }"
  />
  <div v-else class="empty-state" :style="{ minHeight: `${height}px` }">
    <div class="glyph"><i class="fa-solid fa-chart-line" /></div>
    <div class="title">暂无趋势数据</div>
  </div>
</template>
