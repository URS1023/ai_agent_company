<script setup lang="ts">
/** 精提Test5原JimuWidgetRenderer边框/装饰展示分支，保留变体几何、颜色与动画。 */
import { computed } from "vue";
const props = defineProps<{ widget: Record<string, any> }>();
const componentType = computed(() => props.widget.component);
const option = computed(() => props.widget.config?.option ?? {});
const borderType = computed(() => String(option.value.type || "7"));
const decorationStyle = computed(() => ({
  "--main-color": option.value.mainColor || "#05f8d6",
  "--sub-color": option.value.subColor || "#0082fc",
}));
const borderGeometry = computed(() => {
  const width = Math.max(30, props.widget.w);
  const height = Math.max(30, props.widget.h);
  return {
    viewBox: `0 0 ${width} ${height}`,
    width,
    height,
    fullOuter: `8,5 ${width - 5},5 ${width - 5},${height - 100} ${width - 100},${height - 5} 8,${height - 5} 8,5`,
    fullInner: `3,0 ${width},0 ${width},${height - 78} ${width - 74},${height - 5} 3,${height - 5} 3,5`,
    fullTop: `50,13 ${width - 35},13`,
    fullTop2: `15,20 ${width - 35},20`,
    fullBottom: `15,${height - 20} ${width - 110},${height - 20}`,
    fullBottom2: `15,${height - 13} ${width - 110},${height - 13}`,
  };
});
</script>
<template>
  <template v-if="componentType === 'JDragBorder'">
    <svg
      v-if="borderType === '5'"
      class="jimu-border border-5"
      :style="decorationStyle"
      :viewBox="borderGeometry.viewBox"
      preserveAspectRatio="none"
    >
      <polyline class="b5-line-1" :points="borderGeometry.fullOuter" />
      <polyline class="b5-line-2" :points="borderGeometry.fullInner" />
      <polyline class="b5-line-3" :points="borderGeometry.fullTop" />
      <polyline class="b5-line-4" :points="borderGeometry.fullTop2" />
      <polyline class="b5-line-5" :points="borderGeometry.fullBottom" />
      <polyline class="b5-line-6" :points="borderGeometry.fullBottom2" />
    </svg>
    <svg
      v-else-if="borderType === '12'"
      class="jimu-border border-12"
      :style="decorationStyle"
      :viewBox="borderGeometry.viewBox"
      preserveAspectRatio="none"
    >
      <rect
        x="2"
        y="2"
        :width="borderGeometry.width - 4"
        :height="borderGeometry.height - 4"
        rx="12"
      />
      <rect
        class="runner"
        x="9"
        y="9"
        :width="borderGeometry.width - 18"
        :height="borderGeometry.height - 18"
        rx="8"
      />
    </svg>
    <svg
      v-else
      class="jimu-border border-generic"
      :class="`border-${borderType}`"
      :style="decorationStyle"
      :viewBox="borderGeometry.viewBox"
      preserveAspectRatio="none"
    >
      <rect
        v-if="!['1', '4', '6', '9'].includes(borderType)"
        class="border-base"
        x="3"
        y="3"
        :width="borderGeometry.width - 6"
        :height="borderGeometry.height - 6"
      />
      <rect
        v-if="['3', '7', '10', '11'].includes(borderType)"
        class="border-inner"
        x="10"
        y="10"
        :width="borderGeometry.width - 20"
        :height="borderGeometry.height - 20"
      />
      <polygon
        v-if="borderType === '4'"
        class="border-base"
        :points="`18,3 ${borderGeometry.width - 18},3 ${borderGeometry.width - 3},18 ${borderGeometry.width - 3},${borderGeometry.height - 18} ${borderGeometry.width - 18},${borderGeometry.height - 3} 18,${borderGeometry.height - 3} 3,${borderGeometry.height - 18} 3,18`"
      />
      <template v-if="['1', '9'].includes(borderType)">
        <polyline :points="`3,32 3,3 32,3`" />
        <polyline
          :points="`${borderGeometry.width - 32},3 ${borderGeometry.width - 3},3 ${borderGeometry.width - 3},32`"
        />
        <polyline
          :points="`${borderGeometry.width - 32},${borderGeometry.height - 3} ${borderGeometry.width - 3},${borderGeometry.height - 3} ${borderGeometry.width - 3},${borderGeometry.height - 32}`"
        />
        <polyline
          :points="`3,${borderGeometry.height - 32} 3,${borderGeometry.height - 3} 32,${borderGeometry.height - 3}`"
        />
      </template>
      <template v-if="borderType === '6'">
        <polyline :points="`3,70 3,3 95,3`" />
        <polyline
          :points="`${borderGeometry.width - 95},${borderGeometry.height - 3} ${borderGeometry.width - 3},${borderGeometry.height - 3} ${borderGeometry.width - 3},${borderGeometry.height - 70}`"
        />
        <line x1="18" y1="13" :x2="borderGeometry.width - 45" y2="13" />
      </template>
      <path
        v-if="borderType === '11'"
        class="border-tab"
        :d="`M${borderGeometry.width * 0.35},10 L${borderGeometry.width * 0.41},2 H${borderGeometry.width * 0.59} L${borderGeometry.width * 0.65},10`"
      />
    </svg>
  </template>

  <template v-else-if="componentType === 'JDragDecoration'">
    <div
      class="jimu-decoration"
      :class="`decoration-${borderType}`"
      :style="decorationStyle"
    >
      <span></span><i></i><span></span>
    </div>
  </template>
</template>
<style scoped>
.jimu-border {
  width: 100%;
  height: 100%;
  overflow: visible;
  fill: none;
}

.border-5 {
  filter: drop-shadow(0 0 5px rgba(0, 130, 252, 0.6));
}
.border-5 polyline {
  fill: none;
}
.b5-line-1 {
  stroke: var(--main-color, #05f8d6);
  stroke-width: 3;
}
.b5-line-2,
.b5-line-3,
.b5-line-4,
.b5-line-5,
.b5-line-6 {
  stroke: var(--sub-color, #0082fc);
  stroke-width: 2;
}

.border-generic {
  filter: drop-shadow(0 0 3px rgba(5, 248, 214, 0.45));
}
.border-generic .border-base,
.border-generic .border-inner,
.border-generic polyline,
.border-generic line,
.border-generic .border-tab {
  fill: none;
  stroke: var(--main-color, #05f8d6);
  stroke-width: 2;
}
.border-generic .border-inner {
  stroke: var(--sub-color, #0082fc);
  stroke-width: 1;
}
.border-1 polyline {
  stroke-width: 4;
}
.border-2 .border-base {
  stroke: var(--main-color, #05bfcf);
  stroke-width: 1;
}
.border-3 .border-base {
  stroke-width: 3;
}
.border-4 .border-base {
  stroke: var(--main-color, #0799c7);
  stroke-width: 2;
}
.border-6 polyline {
  stroke: var(--sub-color, #0082fc);
  stroke-width: 3;
}
.border-6 line {
  stroke: var(--main-color, #05f8d6);
  stroke-width: 1;
}
.border-7 .border-base {
  stroke-width: 3;
}
.border-7 .border-inner {
  stroke-width: 2;
  filter: drop-shadow(0 0 8px #05f8d6);
}
.border-8 {
  filter: none;
}
.border-8 .border-base {
  stroke: #174768;
  stroke-width: 1;
}
.border-9 polyline {
  stroke-dasharray: 14 7;
  stroke-width: 3;
}
.border-10 .border-base {
  stroke-width: 4;
  filter: drop-shadow(0 0 10px #05f8d6);
}
.border-11 .border-tab {
  fill: #063e51;
  stroke-width: 2;
}
.border-13 .border-base {
  stroke: var(--sub-color, #0082fc);
  stroke-dasharray: 22 8;
  stroke-width: 2;
}

.border-12 rect:first-child {
  fill: rgba(0, 23, 54, 0.16);
  stroke: var(--main-color, #05f8d6);
  stroke-width: 2;
  filter: drop-shadow(0 0 8px rgba(0, 130, 252, 0.8));
}

.border-12 .runner {
  stroke: var(--sub-color, #0082fc);
  stroke-width: 2;
  stroke-dasharray: 90 150;
  animation: border-run 3s linear infinite;
}

.jimu-decoration {
  position: relative;
  display: flex;
  align-items: center;
  width: 100%;
  height: 100%;
}

.jimu-decoration span {
  flex: 1;
  height: 3px;
  background: var(--main-color);
  box-shadow: 0 0 9px var(--main-color);
}

.jimu-decoration span:first-child {
  transform: skewX(38deg) translateY(-5px);
}
.jimu-decoration span:last-child {
  transform: skewX(-38deg) translateY(-5px);
}
.jimu-decoration i {
  width: 42px;
  height: 11px;
  border-bottom: 3px solid var(--sub-color);
  transform: translateY(3px);
}

.decoration-1 span {
  height: 8px;
  background: repeating-linear-gradient(
    90deg,
    var(--main-color) 0 3px,
    transparent 3px 9px
  );
  box-shadow: none;
}
.decoration-2 span:first-child {
  flex: 0.18;
}
.decoration-2 span:last-child {
  flex: 1.5;
  height: 1px;
}
.decoration-3 span {
  height: 7px;
  background: repeating-linear-gradient(
    90deg,
    var(--sub-color) 0 7px,
    var(--main-color) 7px 13px
  );
}
.decoration-4 {
  transform: rotate(90deg);
}
.decoration-4 span {
  height: 2px;
}
.decoration-6 span {
  height: 4px;
  background: repeating-linear-gradient(
    90deg,
    var(--main-color) 0 2px,
    var(--sub-color) 2px 5px,
    transparent 5px 8px
  );
}
.decoration-7 span:first-child {
  transform: skewX(-38deg);
}
.decoration-7 span:last-child {
  transform: skewX(38deg);
}
.decoration-8 span {
  height: 2px;
}
.decoration-8 i {
  width: 90px;
  border-top: 2px solid var(--main-color);
  border-bottom-color: #f7c948;
  transform: skewX(35deg);
}
.decoration-9 span {
  height: 6px;
  border-radius: 50%;
  background: repeating-radial-gradient(
    circle,
    var(--main-color) 0 3px,
    transparent 4px 10px
  );
}
.decoration-10 span {
  height: 1px;
  background: #f7c948;
  box-shadow: 0 0 6px #f7c948;
}
.decoration-11 i {
  width: 120px;
  height: 30px;
  border: 2px solid var(--main-color);
  background: rgba(5, 248, 214, 0.12);
  transform: skewX(-22deg);
}
.decoration-12 span {
  height: 1px;
}
.decoration-12 i {
  width: 58px;
  height: 58px;
  border: 1px solid var(--main-color);
  border-radius: 50%;
  transform: rotate(45deg);
}

@keyframes border-run {
  to {
    stroke-dashoffset: -480;
  }
}
@media (prefers-reduced-motion: reduce) {
  .border-12 .runner {
    animation: none;
  }
}
</style>
