<script setup lang="ts">
/** 复用Test5画布与图表；数据仅来自服务端已刷新绑定，不在组件内发起任意URL请求。 */
import { computed } from "vue";
import JimuChart from "./JimuChart.vue";
import JimuReferenceWidget from "./JimuReferenceWidget.vue";
import ScrollingBoard from "./ScrollingBoard.vue";
import MaterialWidget from "./MaterialWidget.vue";
import JimuBusinessWidget from "./JimuBusinessWidget.vue";
import { isBusinessWidgetType } from "./businessWidgetTypes";
import { safeAssetUrl } from "../../../utils/safeContent";
import DataPresentationWidget from "./DataPresentationWidget.vue";
import { presentationTypes, specialChartTypes } from "../presentation";
import SourcePresentationWidget from "../source/SourcePresentationWidget.vue";
import { sourcePresentationTypes } from "../sourcePresentation";
const props = defineProps<{
  widget: Record<string, any>;
  preview?: boolean;
  interactive?: boolean;
}>();
const emit = defineEmits<{
  "data-click": [point: any];
  drilldown: [point: any];
}>();
const aliases: Record<string, string> = {
  text: "JText",
  metric: "JStatsSummary",
  line: "JLine",
  bar: "JBar",
  table: "JScrollBoard",
};
const canonical = computed(
  () =>
    aliases[props.widget.component ?? props.widget.type] ??
    props.widget.component ??
    props.widget.type,
);
const chartWidget = computed(() => ({
  ...props.widget,
  component: canonical.value,
  config: {
    ...props.widget.config,
    ...(props.widget.binding
      ? {
          dataMapping: {
            labelField: props.widget.binding.label_field,
            valueField: props.widget.binding.value_field,
          },
          snapshotBinding: undefined,
        }
      : {}),
  },
}));
const rows = computed(() =>
  canonical.value === "JCarousel" &&
  Array.isArray(props.widget.config?.images) &&
  props.widget.config.images.length
    ? props.widget.config.images.map((url: string) => ({ url }))
    : Array.isArray(props.widget.data)
      ? props.widget.data
      : Array.isArray(props.widget.data?.rows)
        ? props.widget.data.rows
        : (props.widget.config?.chartDataParsed ?? []),
);
const first = computed(() => rows.value[0] ?? {});
const text = computed(
  () =>
    props.widget.config?.option?.body?.text ??
    props.widget.config?.text ??
    props.widget.text ??
    props.widget.title ??
    "",
);
const textStyle = computed(() => ({
  color:
    props.widget.config?.option?.body?.color ??
    props.widget.config?.color ??
    "#eaf8ff",
  fontSize: `${props.widget.config?.option?.body?.fontSize ?? props.widget.config?.fontSize ?? 24}px`,
  textAlign: props.widget.config?.option?.body?.textAlign ?? "center",
}));
const fields = computed(() => Object.keys(first.value));
// Canvas沿用interactive，列表缩略图沿用preview，两种入口都传递真实预览态。
const sourceInteractive = computed(
  () => !!(props.preview || props.interactive),
);
const value = computed(() => {
  const field =
    props.widget.binding?.value_field ??
    Object.keys(first.value).find((k) => typeof first.value[k] === "number");
  return field ? first.value[field] : "—";
});
function sourceClick(row: Record<string, any>) {
  emit("data-click", row);
  if (sourceInteractive.value) emit("drilldown", row);
}
</script>
<template>
  <SourcePresentationWidget
    v-if="sourcePresentationTypes.includes(canonical)"
    :widget="chartWidget"
    :preview="sourceInteractive"
    @data-click="sourceClick"
  />
  <MaterialWidget
    v-else-if="['JDragBorder', 'JDragDecoration'].includes(canonical)"
    :widget="chartWidget"
  />
  <DataPresentationWidget
    v-else-if="
      presentationTypes.includes(canonical) &&
      !specialChartTypes.includes(canonical)
    "
    :widget="chartWidget"
    :preview="preview"
    @data-click="emit('data-click', $event)"
  />
  <JimuBusinessWidget
    v-else-if="isBusinessWidgetType(canonical)"
    :widget="chartWidget"
    :data="rows"
    :interactive="false"
  />
  <JimuReferenceWidget
    v-else-if="widget.referenceSkin"
    :widget="chartWidget"
    :data="rows"
    :remote="!!widget.binding"
    @data-click="emit('data-click', $event)"
  />
  <div v-else-if="canonical === 'JText'" class="widget-text" :style="textStyle">
    {{ text }}
  </div>
  <img
    v-else-if="canonical === 'JDragImg'"
    class="widget-image"
    :src="
      safeAssetUrl(widget.config?.url ?? widget.config?.src ?? widget.image)
    "
    alt=""
  />
  <div v-else-if="canonical === 'JStatsSummary'" class="widget-stat">
    <span>{{ widget.title ?? widget.componentName }}</span
    ><strong>{{ value }}</strong>
  </div>
  <ScrollingBoard
    v-else-if="canonical === 'JScrollBoard'"
    :headers="fields"
    :fields="fields"
    :rows="rows"
    :animated="false"
  /><JimuChart
    v-else
    :widget="chartWidget"
    :data="rows"
    @data-click="emit('data-click', $event)"
  />
</template>
<style scoped>
.widget-text {
  height: 100%;
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow-wrap: anywhere;
}
.widget-image {
  width: 100%;
  height: 100%;
  object-fit: contain;
}
.widget-stat {
  display: flex;
  flex-direction: column;
  justify-content: center;
  height: 100%;
  text-align: center;
  gap: 15px;
  color: #cbdff5;
}
.widget-stat strong {
  font-size: 36px;
  color: #6cd6ff;
}
</style>
