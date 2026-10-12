<script setup>
/**
 * AiVectorCore —— AI 生成过程的矢量核心动效（进度环 + 状态/事件/遥测文字）。
 *
 * 使用场景：AiGenerationConsole 的主视觉，向用户展示生成进度与当前阶段。
 * 关键约束：
 * - 纯展示：percent 只影响动画角度与能量强度，真实进度以父级传入的值为准，组件不自行取数；
 * - percent 夹取在 0~100 再换算角度，避免越界值让 CSS 变量失效；
 * - telemetry 仅作透传展示，不在此处做脱敏，敏感字段由父级过滤后再传入。
 */
import { computed } from 'vue'

const props = defineProps({
  percent: { type: Number, default: 0 },
  activeColor: { type: String, default: '#27d7ff' },
  isPaused: { type: Boolean, default: false },
  state: { type: String, default: 'running' },
  event: { type: String, default: '' },
  telemetry: { type: Object, default: () => ({}) }
})

const coreStyle = computed(() => ({
  '--core-color': props.activeColor,
  '--core-progress': `${Math.max(0, Math.min(100, props.percent)) * 3.6}deg`,
  '--core-energy': Math.max(.25, Math.min(1, props.percent / 75))
}))

const coreLabel = computed(() => {
  if (props.state === 'success') return 'SYNTHESIS COMPLETE'
  if (props.state === 'error') return 'COMPUTE INTERRUPTED'
  if (props.isPaused) return 'CALIBRATING PATH'
  return 'NEURAL ORCHESTRATION'
})

const orbitTokens = ['VECTOR', 'SCHEMA', 'RAG', 'SQL', 'ECHARTS', 'REVIEW']
</script>

<template>
  <div
    class="vector-core"
    :class="[`state-${state}`, { paused: isPaused }]"
    :style="coreStyle"
    role="img"
    :aria-label="`AI 编排模拟进度 ${percent}%：${event}`"
  >
    <div class="core-aura"></div>
    <div class="scan-sector"></div>
    <div class="orbit orbit-a"><i v-for="index in 8" :key="`a-${index}`" :style="{ '--node': index }"></i></div>
    <div class="orbit orbit-b"><i v-for="index in 6" :key="`b-${index}`" :style="{ '--node': index }"></i></div>
    <div class="orbit orbit-c"></div>

    <span
      v-for="(token, index) in orbitTokens"
      :key="token"
      class="orbit-token"
      :style="{ '--token-index': index }"
    >{{ token }}</span>

    <div class="progress-halo"></div>
    <div class="core-shell">
      <div class="core-grid"></div>
      <small>{{ coreLabel }}</small>
      <strong>{{ Math.round(percent) }}<sup>%</sup></strong>
      <span><i></i>{{ isPaused ? '推理路径随机驻留' : '多智能体并行计算' }}</span>
    </div>

    <div class="pulse-wave wave-a"></div>
    <div class="pulse-wave wave-b"></div>

    <div class="core-event">
      <span></span>
      <p>{{ event || '正在初始化 AI 编排矩阵' }}</p>
      <b>{{ telemetry.throughput || 0 }}<small> MB/s</small></b>
    </div>
  </div>
</template>

<style scoped>
.vector-core {
  --core-color: #27d7ff;
  --core-progress: 0deg;
  --core-energy: .5;
  position: relative;
  display: grid;
  place-items: center;
  width: min(34vw, 360px);
  aspect-ratio: 1;
  margin: 2px auto 0;
  color: #eafcff;
  filter: drop-shadow(0 0 26px color-mix(in srgb, var(--core-color) 18%, transparent));
  isolation: isolate;
}

.core-aura {
  position: absolute;
  inset: 8%;
  z-index: -2;
  border-radius: 50%;
  background: radial-gradient(circle, color-mix(in srgb, var(--core-color) 24%, transparent), transparent 67%);
  filter: blur(18px);
  opacity: calc(.45 + var(--core-energy) * .5);
  animation: aura-breathe 2.8s ease-in-out infinite;
}

.scan-sector {
  position: absolute;
  inset: 4%;
  z-index: -1;
  border-radius: 50%;
  background: conic-gradient(from 0deg, transparent 0 73%, color-mix(in srgb, var(--core-color) 5%, transparent) 76%, color-mix(in srgb, var(--core-color) 42%, transparent) 82%, transparent 86%);
  -webkit-mask: radial-gradient(circle, transparent 0 45%, #000 46% 47%, transparent 48% 100%);
  mask: radial-gradient(circle, transparent 0 45%, #000 46% 47%, transparent 48% 100%);
  animation: core-spin 4.8s linear infinite;
}

.orbit {
  position: absolute;
  border: 1px solid color-mix(in srgb, var(--core-color) 34%, transparent);
  border-radius: 50%;
  box-shadow: inset 0 0 20px rgba(39, 215, 255, .035), 0 0 16px rgba(39, 215, 255, .05);
}
.orbit::before,.orbit::after { content: ''; position: absolute; border-radius: 50%; background: var(--core-color); box-shadow: 0 0 12px var(--core-color); }
.orbit-a { inset: 4%; border-style: dashed; animation: core-spin 17s linear infinite; }
.orbit-a::before { width: 7px; height: 7px; left: 8%; top: 24%; }.orbit-a::after { width: 4px; height: 4px; right: 16%; bottom: 13%; }
.orbit-b { inset: 15%; border-color: color-mix(in srgb, #8b7cff 48%, transparent); animation: core-spin-reverse 11s linear infinite; }
.orbit-b::before { width: 5px; height: 5px; right: 7%; top: 44%; background: #8b7cff; box-shadow: 0 0 12px #8b7cff; }.orbit-b::after { width: 3px; height: 3px; left: 28%; bottom: 2%; background: #ffc857; box-shadow: 0 0 10px #ffc857; }
.orbit-c { inset: 27%; border-color: rgba(104, 247, 255, .22); animation: orbit-tilt 7s ease-in-out infinite; }

.orbit i {
  --angle: calc((var(--node) - 1) * 45deg);
  position: absolute;
  left: calc(50% + 50% * cos(var(--angle)));
  top: calc(50% + 50% * sin(var(--angle)));
  width: 4px;
  height: 4px;
  border: 1px solid var(--core-color);
  background: #061522;
  box-shadow: 0 0 8px color-mix(in srgb, var(--core-color) 78%, transparent);
  transform: translate(-50%, -50%) rotate(45deg);
}
.orbit-b i { --angle: calc((var(--node) - 1) * 60deg + 25deg); border-color: #8b7cff; }

.orbit-token {
  --angle: calc(var(--token-index) * 60deg - 90deg);
  position: absolute;
  left: calc(50% + 45% * cos(var(--angle)));
  top: calc(50% + 45% * sin(var(--angle)));
  padding: 2px 5px;
  border: 1px solid rgba(90, 207, 244, .18);
  background: rgba(3, 11, 21, .82);
  color: #6d91a8;
  font: 8px/1.2 Consolas, monospace;
  letter-spacing: .12em;
  transform: translate(-50%, -50%);
}

.progress-halo {
  position: absolute;
  inset: 21%;
  border-radius: 50%;
  background: conic-gradient(var(--core-color) var(--core-progress), rgba(78, 129, 156, .12) 0);
  box-shadow: 0 0 18px color-mix(in srgb, var(--core-color) 23%, transparent);
  -webkit-mask: radial-gradient(circle, transparent 0 83%, #000 84% 100%);
  mask: radial-gradient(circle, transparent 0 83%, #000 84% 100%);
  transition: background .55s cubic-bezier(.2,.75,.2,1);
}

.core-shell {
  position: relative;
  display: grid;
  place-content: center;
  width: 47%;
  aspect-ratio: 1;
  overflow: hidden;
  border: 1px solid color-mix(in srgb, var(--core-color) 66%, #0d2235);
  border-radius: 50%;
  background: radial-gradient(circle at 50% 35%, rgba(23, 71, 98, .72), rgba(4, 12, 24, .96) 67%);
  box-shadow: inset 0 0 34px rgba(39, 215, 255, .12), 0 0 24px color-mix(in srgb, var(--core-color) 18%, transparent);
  text-align: center;
}
.core-grid { position: absolute; inset: 0; opacity: .16; background-image: linear-gradient(rgba(87, 232, 255, .28) 1px, transparent 1px), linear-gradient(90deg, rgba(87, 232, 255, .2) 1px, transparent 1px); background-size: 13px 13px; transform: perspective(90px) rotateX(22deg) scale(1.18); }
.core-shell small { position: relative; color: #6ca5be; font: 7px Consolas, monospace; letter-spacing: .16em; }
.core-shell strong { position: relative; margin: 4px 0 2px; font: 600 48px/1 Bahnschrift, 'Microsoft YaHei', sans-serif; letter-spacing: -.05em; text-shadow: 0 0 18px color-mix(in srgb, var(--core-color) 75%, transparent); }
.core-shell sup { margin-left: 2px; color: var(--core-color); font-size: 13px; letter-spacing: 0; }
.core-shell > span { position: relative; display: flex; align-items: center; justify-content: center; gap: 5px; color: #83aec3; font-size: 8px; }
.core-shell > span i { width: 4px; height: 4px; border-radius: 50%; background: var(--core-color); box-shadow: 0 0 8px var(--core-color); animation: status-blink 1.2s step-end infinite; }

.pulse-wave { position: absolute; inset: 29%; z-index: -1; border: 1px solid var(--core-color); border-radius: 50%; opacity: 0; animation: core-wave 2.6s ease-out infinite; }
.wave-b { animation-delay: 1.3s; }

.core-event {
  position: absolute;
  left: 9%;
  right: 9%;
  bottom: -2px;
  display: grid;
  grid-template-columns: 8px minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px;
  min-height: 28px;
  padding: 0 9px;
  border: 1px solid rgba(60, 167, 209, .24);
  background: rgba(3, 12, 23, .9);
  box-shadow: 0 8px 28px rgba(0, 0, 0, .28);
}
.core-event > span { width: 5px; height: 5px; border: 1px solid var(--core-color); transform: rotate(45deg); box-shadow: 0 0 8px var(--core-color); }
.core-event p { overflow: hidden; margin: 0; color: #8fcbe1; font-size: 9px; text-overflow: ellipsis; white-space: nowrap; }
.core-event b { color: #d8f8ff; font: 11px Consolas, monospace; }.core-event small { color: #5a899d; font-size: 7px; }

.paused .core-shell > span i { background: #ffc857; box-shadow: 0 0 8px #ffc857; }
.paused .scan-sector,.paused .orbit-a { animation-duration: 24s; }
.paused .core-aura { animation-duration: 5s; opacity: .38; }
.state-success { --core-color: #55f991 !important; }.state-success .core-shell { box-shadow: inset 0 0 44px rgba(85, 249, 145, .18), 0 0 34px rgba(85, 249, 145, .25); }
.state-error { --core-color: #ff6577 !important; }.state-error .pulse-wave { animation: none; }

@keyframes core-spin { to { transform: rotate(360deg); } }
@keyframes core-spin-reverse { to { transform: rotate(-360deg); } }
@keyframes orbit-tilt { 50% { transform: scale(.94) rotate(12deg); opacity: .55; } }
@keyframes aura-breathe { 50% { transform: scale(1.08); opacity: .95; } }
@keyframes core-wave { 0% { transform: scale(.82); opacity: .38; } 100% { transform: scale(1.82); opacity: 0; } }
@keyframes status-blink { 50% { opacity: .25; } }

@media (prefers-reduced-motion: reduce) {
  .vector-core * { animation: none !important; transition: none !important; }
}
</style>
