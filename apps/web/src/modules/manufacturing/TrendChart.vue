<script setup lang="ts">
import { computed } from "vue";
import { use } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { LineChart, BarChart, PieChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  TitleComponent,
} from "echarts/components";
import VChart from "vue-echarts";
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
</script>
<template>
  <VChart
    v-if="rows.length"
    :option="option"
    autoresize
    :style="{ height: `${height}px` }"
  />
  <div v-else class="empty-state" :style="{ minHeight: `${height}px` }">
    <div class="glyph"><i class="fa-solid fa-chart-line" /></div>
    <div class="title">暂无趋势数据</div>
  </div>
</template>
