<script setup lang="ts">
/**
 * LynxTemplate19 大屏的指标卡（图标 + 数值 + 单位 + 色调）。
 * 路由：无独立路由，由 LynxTemplate19Dashboard 按 useTemplate19Dashboard 计算的指标列表渲染。
 * 依赖接口：无请求；metric 由 composable（useTemplate19Dashboard）派生，本组件只做展示与图标映射。
 * 约束：与工作流/大屏域重叠的模板组件，本文件仅补充注释，不改动模板结构；icon 取值必须在上表映射内，否则无图标。
 */
import { computed } from 'vue'
import type { Template19Metric } from '@/composables/useTemplate19Dashboard'

const props = defineProps<{
  metric: Template19Metric
}>()

const iconClass = computed(() => ({
  device: 'fa-solid fa-server',
  alert: 'fa-solid fa-triangle-exclamation',
  money: 'fa-solid fa-yen-sign',
  order: 'fa-solid fa-receipt',
  status: 'fa-solid fa-power-off',
})[props.metric.icon])
</script>

<template>
  <article class="lynx-t19-metric" :class="`tone-${metric.tone}`" :aria-label="`${metric.label} ${metric.display} ${metric.unit}`">
    <span class="metric-emblem" aria-hidden="true">
      <i :class="iconClass"></i>
    </span>
    <span class="metric-copy">
      <small>{{ metric.label }}</small>
      <strong>{{ metric.display }} <em>{{ metric.unit }}</em></strong>
    </span>
  </article>
</template>

<style scoped>
.lynx-t19-metric {
  --metric-color: #35d9ff;
  display: flex;
  min-width: 0;
  height: 100%;
  align-items: center;
  gap: 17px;
  color: #effaff;
}
.lynx-t19-metric.tone-blue { --metric-color: #3695ff; }
.lynx-t19-metric.tone-amber { --metric-color: #ffc95c; }
.metric-emblem {
  position: relative;
  display: grid;
  width: 66px;
  height: 58px;
  flex: 0 0 auto;
  place-items: center;
  color: var(--metric-color);
  background:
    linear-gradient(90deg, transparent 8%, color-mix(in srgb, var(--metric-color) 34%, transparent) 50%, transparent 92%),
    rgba(5, 31, 74, .82);
  filter: drop-shadow(0 0 13px color-mix(in srgb, var(--metric-color) 44%, transparent));
  clip-path: polygon(25% 0, 75% 0, 100% 50%, 75% 100%, 25% 100%, 0 50%);
}
.metric-emblem::before,
.metric-emblem::after {
  position: absolute;
  inset: 4px;
  border: 1px solid color-mix(in srgb, var(--metric-color) 76%, transparent);
  clip-path: inherit;
  content: '';
}
.metric-emblem::after {
  inset: 9px;
  border-color: color-mix(in srgb, var(--metric-color) 26%, transparent);
}
.metric-emblem i {
  position: relative;
  z-index: 1;
  font-size: 22px;
  text-shadow: 0 0 13px currentColor;
}
.metric-copy {
  display: grid;
  min-width: 0;
  gap: 6px;
}
.metric-copy small {
  overflow: hidden;
  color: #a8c9df;
  font: 500 14px/1.1 'Microsoft YaHei UI', sans-serif;
  letter-spacing: .04em;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.metric-copy strong {
  color: var(--metric-color);
  font: 700 28px/1 Bahnschrift, 'DIN Alternate', 'Microsoft YaHei UI', sans-serif;
  letter-spacing: .015em;
  text-shadow: 0 0 12px color-mix(in srgb, var(--metric-color) 38%, transparent);
  white-space: nowrap;
}
.metric-copy em {
  margin-left: 2px;
  color: #d7edf8;
  font: 500 13px 'Microsoft YaHei UI', sans-serif;
  font-style: normal;
  text-shadow: none;
}
</style>
