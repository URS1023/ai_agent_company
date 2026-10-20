<script setup lang="ts">
/** 来源Test5/apps/analytics/src/components/jimu-screen/JimuChart.vue，精提胶囊/背景柱和仪表结构；只替换可信投影。 */
import { computed } from "vue";
import VChart from "vue-echarts";
import { use } from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { BarChart, GaugeChart } from "echarts/charts";
import { GridComponent, TooltipComponent } from "echarts/components";
import {
  sourceChartOption,
  type SourcePresentationModel,
  type SourcePresentationRow,
} from "../sourcePresentation";
use([SVGRenderer, BarChart, GaugeChart, GridComponent, TooltipComponent]);
const props = defineProps<{
  widget: Record<string, any>;
  model: SourcePresentationModel;
}>();
const emit = defineEmits<{ "data-click": [row: SourcePresentationRow] }>();
const option = computed(() => sourceChartOption(props.widget, props.model));
function select(point: { dataIndex?: number }) {
  const row = props.model.rows[point.dataIndex ?? 0];
  if (row) emit("data-click", row);
}
</script>

<template>
  <VChart
    class="source-chart"
    :option="option"
    :init-options="{ renderer: 'svg' }"
    autoresize
    @click="select"
  />
</template>

<style scoped>
.source-chart {
  width: 100%;
  height: 100%;
}
</style>
