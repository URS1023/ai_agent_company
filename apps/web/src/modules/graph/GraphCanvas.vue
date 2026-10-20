<script setup lang="ts">
import { computed } from "vue";
import { use } from "echarts/core";
import { GraphChart } from "echarts/charts";
import { CanvasRenderer } from "echarts/renderers";
import { TooltipComponent, LegendComponent } from "echarts/components";
import VChart from "vue-echarts";
use([GraphChart, CanvasRenderer, TooltipComponent, LegendComponent]);
const props = defineProps<{
  nodes: Record<string, any>[];
  edges: Record<string, any>[];
  selectedId?: string;
  types: string[];
}>();
const emit = defineEmits<{ select: [node: Record<string, any>] }>();
const option = computed(() => ({
  tooltip: {
    renderMode: "richText",
    formatter: (p: any) => p.data.name ?? p.data.type,
  },
  legend: { data: props.types, textStyle: { color: "#cadfff" }, bottom: 15 },
  series: [
    {
      type: "graph",
      layout: "force",
      roam: true,
      draggable: true,
      force: { repulsion: 600, edgeLength: [80, 180] },
      categories: props.types.map((name) => ({ name })),
      data: props.nodes.map((node) => ({
        ...node,
        value: node.name,
        category: props.types.indexOf(node.type),
        symbolSize: node.id === props.selectedId ? 32 : 20,
        label: {
          show: true,
          color: "#d7e7ff",
          fontSize: 14,
          position: "bottom",
        },
        itemStyle:
          node.id === props.selectedId
            ? { color: "#58dfff", borderColor: "#e5ffff", borderWidth: 2 }
            : undefined,
      })),
      links: props.edges.map((edge) => ({
        ...edge,
        source: edge.source,
        target: edge.target,
        label: {
          show: true,
          formatter: edge.type,
          color: "#80aee4",
          fontSize: 11,
        },
      })),
      edgeSymbol: ["none", "arrow"],
      edgeSymbolSize: 7,
      lineStyle: { color: "#89b8ff", opacity: 0.65, curveness: 0.08 },
      emphasis: { focus: "adjacency", lineStyle: { width: 3 } },
    },
  ],
}));
function click(params: any) {
  if (params.dataType === "node") emit("select", params.data);
}
</script>
<template>
  <VChart
    v-if="nodes.length"
    :option="option"
    autoresize
    class="graph-chart"
    @click="click"
  />
  <div v-else class="graph-empty">暂无实体</div>
</template>
<style scoped>
.graph-chart {
  width: 100%;
  height: 100%;
}
.graph-empty {
  height: 100%;
  display: grid;
  place-items: center;
  color: #7798c5;
  font-size: 15px;
}
</style>
