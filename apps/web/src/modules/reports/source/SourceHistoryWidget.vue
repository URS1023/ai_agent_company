<script setup lang="ts">
/** 来源Test5/apps/analytics/src/components/jimu-screen/JimuBusinessWidget.vue的发展历程；时间与排序使用服务端投影。 */
import {
  type SourcePresentationModel,
  type SourcePresentationRow,
} from "../sourcePresentation";
defineProps<{ model: SourcePresentationModel }>();
const emit = defineEmits<{ "data-click": [row: SourcePresentationRow] }>();
</script>

<template>
  <div class="history-widget">
    <button
      v-for="(row, index) in model.rows"
      :key="`${row.time}-${index}`"
      class="history-item"
      @pointerdown.stop
      @click.stop="emit('data-click', row)"
    >
      <time class="history-time" :datetime="row.time">{{
        row.time_display ?? row.time
      }}</time
      ><i class="history-marker" />
      <span class="history-content"
        ><strong class="history-name">{{ row.name }}</strong
        ><span v-if="row.description" class="history-description">{{
          row.description
        }}</span
        ><b v-if="row.value !== null" class="history-value"
          >{{ row.value }}{{ row.unit }}</b
        ></span
      >
    </button>
  </div>
</template>

<style scoped>
.history-widget {
  height: 100%;
  overflow: auto;
  padding: 14px 12px;
  background: #071b2d;
  box-sizing: border-box;
}
.history-item {
  display: grid;
  grid-template-columns: minmax(80px, 0.4fr) 16px minmax(0, 1fr);
  gap: 12px;
  position: relative;
  width: 100%;
  padding: 0 0 20px;
  border: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.history-marker::after {
  content: "";
  position: absolute;
  top: 15px;
  bottom: -20px;
  left: 7px;
  width: 1px;
  background: #26506d;
}
.history-item:last-child .history-marker::after {
  display: none;
}
.history-time {
  color: #8faabd;
  overflow-wrap: anywhere;
}
.history-marker {
  position: relative;
  z-index: 1;
  display: block;
  align-self: stretch;
}
.history-marker::before {
  content: "";
  position: absolute;
  top: 4px;
  left: 2px;
  width: 9px;
  height: 9px;
  border: 2px solid #42bbf1;
  border-radius: 50%;
  background: #071b2d;
}
.history-content {
  display: grid;
  gap: 5px;
  min-width: 0;
}
.history-name {
  color: #eaf8ff;
  font-weight: 600;
  overflow-wrap: anywhere;
}
.history-description {
  color: #91aabd;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.history-value {
  color: #50c7e8;
  font-weight: 500;
}
.history-item:focus-visible {
  outline: 2px solid #42bbf1;
  outline-offset: 2px;
}
</style>
