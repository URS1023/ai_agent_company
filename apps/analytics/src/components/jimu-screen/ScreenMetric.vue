<!--
  ScreenMetric.vue —— 大屏指标卡（单值展示）。

  职责：展示大屏上的单个关键指标（数值 + 单位 + 可选脉冲动效）。
  约束：只展示调用方传入的真实数值；无数据时由父组件决定占位文案，本组件不自行编造。
-->
<template>
  <article class="screen-metric" :class="[`tone-${tone}`, { pulse: pulse }]">
    <div class="screen-metric__halo"></div>
    <div class="screen-metric__icon">
      <i :class="icon"></i>
    </div>
    <div class="screen-metric__content">
      <div class="screen-metric__value">
        <span>{{ value }}</span>
        <small v-if="unit">{{ unit }}</small>
      </div>
      <div class="screen-metric__label">{{ label }}</div>
    </div>
    <div class="screen-metric__trend" :class="trend >= 0 ? 'up' : 'down'">
      <i :class="trend >= 0 ? 'fa-solid fa-arrow-trend-up' : 'fa-solid fa-arrow-trend-down'"></i>
      <span>{{ Math.abs(trend) }}%</span>
    </div>
  </article>
</template>

<script setup>
/**
 * ScreenMetric —— 大屏风格的指标卡（发光数字 + 图标 + 单位 + 标签）。
 *
 * 使用场景：JimuWidgetRenderer 的指标类组件（JStatsSummary/JCountTo/JNumber 等）与 AI 生成大屏的
 * 指标展示，由调用方传入数据而非自行取数。
 * 关键约束：纯展示组件，无接口依赖、无副作用；tone 只影响配色（cyan/violet/... 由样式表定义），
 * pulse 仅作视觉强调，均不代表业务状态。
 */
defineProps({
  label: { type: String, required: true },
  value: { type: [String, Number], required: true },
  unit: { type: String, default: '' },
  icon: { type: String, default: 'fa-solid fa-chart-simple' },
  tone: { type: String, default: 'cyan' },
  trend: { type: Number, default: 0 },
  pulse: { type: Boolean, default: false }
})
</script>

<style scoped>
.screen-metric {
  position: relative;
  display: grid;
  grid-template-columns: 48px minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
  min-width: 0;
  min-height: 88px;
  padding: 16px;
  border: 1px solid rgba(77, 213, 255, 0.16);
  border-radius: 8px;
  background: linear-gradient(135deg, rgba(8, 27, 57, 0.94), rgba(4, 14, 32, 0.82));
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.04), 0 12px 30px rgba(0, 0, 0, 0.28);
  overflow: hidden;
}

.screen-metric::before {
  content: "";
  position: absolute;
  inset: 0;
  pointer-events: none;
  background-image: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.06), transparent);
  transform: translateX(-100%);
  animation: metricSweep 5.5s ease-in-out infinite;
}

.screen-metric__halo {
  position: absolute;
  width: 110px;
  height: 110px;
  right: -42px;
  bottom: -48px;
  border-radius: 50%;
  filter: blur(14px);
  opacity: 0.28;
}

.screen-metric__icon {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
  border-radius: 8px;
  color: #e8fbff;
  font-size: 21px;
  box-shadow: 0 0 24px rgba(77, 213, 255, 0.22);
}

.screen-metric__value {
  display: flex;
  align-items: baseline;
  gap: 4px;
  min-width: 0;
  color: #edf9ff;
  font-family: Consolas, Monaco, monospace;
  font-size: 30px;
  font-weight: 800;
  line-height: 1;
  text-shadow: 0 0 20px currentColor;
}

.screen-metric__value small {
  color: #7d8aa7;
  font-size: 12px;
  font-weight: 600;
  text-shadow: none;
}

.screen-metric__label {
  margin-top: 7px;
  color: #8fa2c2;
  font-size: 12px;
  line-height: 1.2;
}

.screen-metric__trend {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  align-self: start;
  padding: 4px 8px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 700;
}

.screen-metric__trend.up {
  color: #4ade80;
  background: rgba(74, 222, 128, 0.12);
}

.screen-metric__trend.down {
  color: #fb7185;
  background: rgba(251, 113, 133, 0.12);
}

.tone-cyan .screen-metric__icon,
.tone-cyan .screen-metric__halo {
  background: linear-gradient(135deg, #0ea5e9, #22d3ee);
}

.tone-violet .screen-metric__icon,
.tone-violet .screen-metric__halo {
  background: linear-gradient(135deg, #7c3aed, #c084fc);
}

.tone-amber .screen-metric__icon,
.tone-amber .screen-metric__halo {
  background: linear-gradient(135deg, #f59e0b, #facc15);
}

.tone-green .screen-metric__icon,
.tone-green .screen-metric__halo {
  background: linear-gradient(135deg, #10b981, #5eead4);
}

.pulse {
  animation: metricPulse 1.8s ease-in-out infinite;
}

@keyframes metricSweep {
  0%, 72% { transform: translateX(-100%); }
  100% { transform: translateX(100%); }
}

@keyframes metricPulse {
  0%, 100% { box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.04), 0 12px 30px rgba(0, 0, 0, 0.28); }
  50% { box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.04), 0 0 28px rgba(77, 213, 255, 0.24); }
}
</style>
