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
    ? { top: 0, textStyle: { color: "#7d8a9c" } }
    : undefined,
  grid: { left: 55, right: 25, top: 35, bottom: 45 },
  xAxis: {
    type: "category",
    data: props.rows.map((x) => x[props.xField]),
    axisLine: { lineStyle: { color: "#dce2ec" } },
    axisLabel: { color: "#7d8a9c" },
  },
  yAxis: {
    type: "value",
    scale: props.type !== "bar",
    name: props.unit,
    splitLine: { lineStyle: { color: "#eef1f6" } },
    axisLabel: { color: "#7d8a9c" },
  },
  series: (props.series ?? [{ field: props.yField, name: "" }]).map(
    (series, index) => ({
      name: series.name,
      type: props.type,
      data: props.rows.map((x) => x[series.field]),
      showSymbol: props.rows.length < 30,
      smooth: false,
      color: index === 0 ? "#2666fb" : "#16b9a4",
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
  /><el-empty
    v-else
    description="暂无趋势数据"
    :image-size="80"
    :style="{ height: `${height}px` }"
  />
</template>
