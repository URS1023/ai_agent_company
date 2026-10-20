<script setup lang="ts">
/** 组合已核对的Test5来源组件；无可信模型时不回退到通用图或样例。 */
import { computed } from "vue";
import {
  sourceCalendarTypes,
  sourceCardTypes,
  sourceChartTypes,
  sourceEmptyMessage,
  sourceModel,
  type SourcePresentationRow,
} from "../sourcePresentation";
import SourceChartWidget from "./SourceChartWidget.vue";
import SourceCalendarWidget from "./SourceCalendarWidget.vue";
import SourceHistoryWidget from "./SourceHistoryWidget.vue";
import SourceCardsWidget from "./SourceCardsWidget.vue";
const props = defineProps<{ widget: Record<string, any>; preview?: boolean }>();
const emit = defineEmits<{ "data-click": [row: SourcePresentationRow] }>();
const model = computed(() => sourceModel(props.widget));
const ready = computed(
  () => model.value?.status === "ready" && model.value.rows.length > 0,
);
</script>

<template>
  <div
    class="source-widget"
    :data-source-component="widget.source_component_id"
  >
    <template v-if="ready && model">
      <SourceChartWidget
        v-if="sourceChartTypes.includes(widget.component)"
        :widget="widget"
        :model="model"
        @data-click="emit('data-click', $event)"
      />
      <SourceCalendarWidget
        v-else-if="sourceCalendarTypes.includes(widget.component)"
        :widget="widget"
        :model="model"
        @data-click="emit('data-click', $event)"
      />
      <SourceCardsWidget
        v-else-if="sourceCardTypes.includes(widget.component)"
        :widget="widget"
        :model="model"
        :preview="preview"
        @data-click="emit('data-click', $event)"
      />
      <SourceHistoryWidget
        v-else
        :model="model"
        @data-click="emit('data-click', $event)"
      />
    </template>
    <div v-else class="source-empty" role="status">
      {{ sourceEmptyMessage(widget) }}
    </div>
  </div>
</template>

<style scoped>
.source-widget {
  width: 100%;
  height: 100%;
  min-height: 0;
  color: #dcecff;
  font:
    14px "Microsoft YaHei",
    Arial,
    sans-serif;
}
.source-empty {
  display: grid;
  place-items: center;
  height: 100%;
  color: #8faabd;
}
</style>
