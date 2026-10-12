<script setup lang="ts">
/**
 * LynxTemplate19 设备数字运维大屏模板（整屏布局：指标卡 + 多面板 + 时钟 + 钻取）。
 * 路由：无独立路由，由 ReportViewer / JimuBigScreenReplica 按模板选择渲染。
 * 依赖接口：useTemplate19Dashboard（派生指标与面板数据，内部经 useWidgetData 取组件数据）；
 *   参考底图取自 BASE_URL 下的 jimu-screen/templates/template-19.jpg（仅作静态参考，不参与取数）。
 * 关键交互：drilldown 事件把点击上下文上抛给宿主；每秒刷新时钟，卸载时清理定时器。
 * 约束：与工作流/大屏域重叠的模板组件，本文件仅补充注释，不改动模板结构。
 */
import { computed, onBeforeUnmount, onMounted, shallowRef, toRef } from 'vue'
import { useTemplate19Dashboard, type Template19Point } from '@/composables/useTemplate19Dashboard'
import LynxTemplate19Metric from './LynxTemplate19Metric.vue'
import LynxTemplate19Panel from './LynxTemplate19Panel.vue'

const props = defineProps<{
  screen: Record<string, any>
  zoom: number
}>()

const emit = defineEmits<{
  drilldown: [payload: Record<string, any>]
}>()

const screen = toRef(props, 'screen')
const model = useTemplate19Dashboard(screen)
const now = shallowRef(new Date())
let clockTimer: ReturnType<typeof setInterval> | undefined

const title = computed(() => String(props.screen.name || '设备数字运维大屏'))
const referenceImage = `${import.meta.env.BASE_URL}jimu-screen/templates/template-19.jpg`
const canvasStyle = computed(() => ({
  transform: `translate(-50%, -50%) scale(${props.zoom})`,
}))
const fullSalesAmount = computed(() => new Intl.NumberFormat('zh-CN', {
  style: 'currency',
  currency: 'CNY',
  maximumFractionDigits: 0,
}).format(model.salesAmount.value.value))
const dateText = computed(() => new Intl.DateTimeFormat('zh-CN', {
  year: 'numeric', month: '2-digit', day: '2-digit', weekday: 'short',
}).format(now.value).replaceAll('/', '-'))
const timeText = computed(() => now.value.toLocaleTimeString('zh-CN', { hour12: false }))
const alertMax = computed(() => Math.max(1, ...model.alertRanking.value.map(item => item.value)))
const regionMax = computed(() => Math.max(1, ...model.regionRanking.value.map(item => item.value)))

const chartGeometry = computed(() => buildLineGeometry(model.salesTrend.value))
const donutStyle = computed(() => ({ background: donutGradient(model.deviceTypes.value) }))
const donutTotal = computed(() => model.deviceTypes.value.reduce((total, item) => total + item.value, 0))

function buildLineGeometry(points: Template19Point[]) {
  const width = 420
  const height = 160
  const left = 24
  const right = 12
  const top = 14
  const bottom = 28
  if (!points.length) return { line: '', area: '', dots: [], labels: [] }
  const values = points.map(point => Number(point.value) || 0)
  const minimum = Math.min(0, ...values)
  const maximum = Math.max(1, ...values)
  const range = Math.max(1, maximum - minimum)
  const dots = points.map((point, index) => {
    const x = points.length === 1
      ? (left + width - right) / 2
      : left + index * (width - left - right) / (points.length - 1)
    const y = top + (maximum - (Number(point.value) || 0)) / range * (height - top - bottom)
    return { x, y, label: point.label, value: point.value }
  })
  const line = dots.map((point, index) => `${index ? 'L' : 'M'}${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(' ')
  const area = `${line} L${dots.at(-1)!.x.toFixed(1)},${height - bottom} L${dots[0].x.toFixed(1)},${height - bottom} Z`
  const labelIndexes = [...new Set([0, Math.floor((points.length - 1) / 2), points.length - 1])]
  return { line, area, dots, labels: labelIndexes.map(index => dots[index]) }
}

function donutGradient(rows: Array<{ value: number }>) {
  const colors = ['#24c9ff', '#2f78ff', '#704fff', '#39e2c1', '#ffc253', '#ff6e8c']
  const total = rows.reduce((sum, row) => sum + Math.max(0, row.value), 0)
  if (!total) return 'conic-gradient(rgba(35, 190, 215, .18) 0 100%)'
  let cursor = 0
  const stops: string[] = []
  rows.forEach((row, index) => {
    const start = cursor
    cursor += Math.max(0, row.value) / total * 100
    const gap = rows.length > 1 ? Math.min(.9, (cursor - start) * .08) : 0
    stops.push(`${colors[index % colors.length]} ${start + gap}% ${Math.max(start + gap, cursor - gap)}%`)
  })
  return `conic-gradient(${stops.join(',')})`
}

function rowPercent(value: number, maximum: number) {
  return `${Math.max(4, Math.min(100, value / maximum * 100))}%`
}

function openDrilldown(widget: Record<string, any> | undefined, point: Record<string, any>) {
  if (!widget?.id) return
  emit('drilldown', { widget, point })
}

onMounted(() => {
  clockTimer = setInterval(() => { now.value = new Date() }, 1000)
})

onBeforeUnmount(() => {
  if (clockTimer) clearInterval(clockTimer)
})
</script>

<template>
  <section class="lynx-template-19-viewport" aria-label="智慧云脑 Cloud Brain 设备数字运维大屏">
    <div class="lynx-template-19-canvas" :style="canvasStyle">
      <img class="reference-plate" :src="referenceImage" alt="" aria-hidden="true">
      <div class="reference-veil" aria-hidden="true"></div>
      <div class="top-telemetry" aria-hidden="true">
        <i v-for="index in 16" :key="index"></i>
      </div>
      <div class="edge-rails" aria-hidden="true"><i></i><i></i><i></i><i></i></div>

      <header class="dashboard-title-bar">
        <span class="title-wing left" aria-hidden="true"></span>
        <h1 data-role="dashboard-title">{{ title }}</h1>
        <span class="title-wing right" aria-hidden="true"></span>
      </header>

      <div class="dashboard-clock" :class="{ loading: model.loading.value, warning: model.errors.value.length }">
        <small>LOCAL · LIVE</small>
        <strong>{{ dateText }}</strong>
        <b>{{ timeText }}</b>
      </div>

      <LynxTemplate19Metric class="metric metric-one" :metric="model.deviceTotal.value" />
      <LynxTemplate19Metric class="metric metric-two" :metric="model.alertCount.value" />
      <LynxTemplate19Metric class="metric metric-three" :metric="model.orderCount.value" />
      <LynxTemplate19Metric class="metric metric-four" :metric="model.statusCount.value" />

      <section class="hero-total" aria-label="销售总金额">
        <span>销售总金额</span>
        <strong>{{ model.salesAmount.value.display }} <em v-if="model.salesAmount.value.available">元</em></strong>
        <small>{{ model.salesAmount.value.available ? fullSalesAmount : '当前查询暂无结果' }}</small>
      </section>
      <div class="hero-date"><i></i><span>{{ dateText }} · 实时数据</span><i></i></div>
      <div class="status-caption"><span></span><b>今日实时数据情况</b><span></span></div>

      <div class="status-nodes">
        <article v-for="(node, index) in model.statusNodes.value" :key="node.label" :class="`tone-${node.tone}`">
          <span><i>{{ node.value }}</i></span>
          <small>{{ node.label }}</small>
          <b aria-hidden="true">{{ String(index + 1).padStart(2, '0') }}</b>
        </article>
      </div>

      <LynxTemplate19Panel class="panel alert-ranking" title="设备预警排行" variant="ranking">
        <ol v-if="model.alertRanking.value.length" class="ranking-list alert-list">
          <li
            v-for="(row, index) in model.alertRanking.value.slice(0, 7)"
            :key="`${row.label}-${index}`"
            tabindex="0"
            @click="openDrilldown(model.sourceWidgets?.alerts?.value, { name: row.label, value: row.value })"
            @keydown.enter="openDrilldown(model.sourceWidgets?.alerts?.value, { name: row.label, value: row.value })"
          >
            <span>{{ row.label }}</span>
            <b><i :style="{ width: rowPercent(row.value, alertMax) }"></i></b>
            <em>L{{ row.display }}</em>
          </li>
        </ol>
        <div v-else class="panel-empty">当前没有设备预警</div>
      </LynxTemplate19Panel>

      <LynxTemplate19Panel class="panel region-ranking" title="区域销售排行" variant="ranking">
        <ol v-if="model.regionRanking.value.length" class="ranking-list sales-list">
          <li
            v-for="(row, index) in model.regionRanking.value.slice(0, 7)"
            :key="`${row.label}-${index}`"
            tabindex="0"
            @click="openDrilldown(model.sourceWidgets?.regions?.value, { name: row.label, value: row.value })"
            @keydown.enter="openDrilldown(model.sourceWidgets?.regions?.value, { name: row.label, value: row.value })"
          >
            <span>{{ row.label }}</span>
            <b><i :style="{ width: rowPercent(row.value, regionMax) }"></i></b>
            <em>{{ row.display }}</em>
          </li>
        </ol>
        <div v-else class="panel-empty">当前没有区域销售数据</div>
      </LynxTemplate19Panel>

      <LynxTemplate19Panel class="panel problem-table" title="财务与设备运营明细" variant="table">
        <div class="problem-board">
          <div class="problem-head">
            <b>业务类型</b><b>发生日期</b><b>业务对象</b><b>数值 / 级别</b><b>说明</b>
          </div>
          <div v-if="model.operations.value.length" class="problem-rows">
            <button
              v-for="row in model.operations.value.slice(0, 8)"
              :key="row.id"
              type="button"
              @click="openDrilldown(model.sourceWidgets?.[row.source]?.value, { name: row.subject, value: row.rawValue })"
            >
              <span :class="{ danger: row.type === '设备预警' }">{{ row.type }}</span><span>{{ row.date }}</span><span>{{ row.subject }}</span><span :class="{ danger: row.type === '设备预警' }">{{ row.metric }}</span><span>{{ row.detail }}</span>
            </button>
          </div>
          <div v-else class="panel-empty">当前没有财务或设备运营记录</div>
        </div>
      </LynxTemplate19Panel>

      <LynxTemplate19Panel class="panel device-distribution" title="设备类型分布" variant="donut">
        <div v-if="model.deviceTypes.value.length" class="donut-layout">
          <div class="donut-chart" :style="donutStyle">
            <span><strong>{{ donutTotal }}</strong><small>设备</small></span>
          </div>
          <ul>
            <li v-for="(row, index) in model.deviceTypes.value.slice(0, 6)" :key="`${row.label}-${index}`">
              <i :style="{ '--legend-index': index }"></i><span>{{ row.label }}</span><b>{{ row.display }}</b>
            </li>
          </ul>
        </div>
        <div v-else class="panel-empty">当前没有设备类型数据</div>
      </LynxTemplate19Panel>

      <LynxTemplate19Panel class="panel sales-trend" title="销售金额趋势" variant="trend">
        <div v-if="model.salesTrend.value.length" class="line-chart">
          <svg viewBox="0 0 420 160" preserveAspectRatio="none" role="img" aria-label="销售金额趋势折线图">
            <defs>
              <linearGradient id="t19-line-area" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0" stop-color="#2b88ff" stop-opacity=".42" />
                <stop offset="1" stop-color="#2b88ff" stop-opacity="0" />
              </linearGradient>
              <filter id="t19-line-glow"><feGaussianBlur stdDeviation="2.4" result="blur" /><feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
            </defs>
            <g class="chart-grid"><line v-for="index in 4" :key="index" x1="24" x2="408" :y1="14 + index * 27" :y2="14 + index * 27" /></g>
            <path class="chart-area" :d="chartGeometry.area" />
            <path class="chart-line-glow" :d="chartGeometry.line" />
            <path class="chart-line" :d="chartGeometry.line" />
            <g class="chart-dots">
              <circle v-for="(point, index) in chartGeometry.dots" :key="index" :cx="point.x" :cy="point.y" r="2.8" />
            </g>
          </svg>
          <span v-for="(label, index) in chartGeometry.labels" :key="index" class="axis-label" :style="{ left: `${label.x / 420 * 100}%` }">{{ label.label.slice(5) }}</span>
        </div>
        <div v-else class="panel-empty">当前没有销售趋势数据</div>
      </LynxTemplate19Panel>
    </div>
  </section>
</template>

<style scoped>
.lynx-template-19-viewport {
  position: absolute;
  inset: 0;
  overflow: hidden;
  background: #01071e;
}
.lynx-template-19-canvas {
  position: absolute;
  top: 50%;
  left: 50%;
  width: 1920px;
  height: 1080px;
  overflow: hidden;
  color: #eaf8ff;
  background:
    radial-gradient(ellipse at 50% 105%, rgba(0, 112, 255, .34), transparent 37%),
    radial-gradient(circle at 50% 35%, rgba(16, 77, 173, .18), transparent 44%),
    linear-gradient(180deg, #020b2c 0%, #031236 49%, #020a26 100%);
  box-shadow: inset 0 0 100px rgba(0, 0, 0, .55);
  transform-origin: center;
  isolation: isolate;
}
.lynx-template-19-canvas::before {
  position: absolute;
  inset: 0;
  z-index: -1;
  opacity: .22;
  background-image:
    linear-gradient(rgba(24, 123, 255, .13) 1px, transparent 1px),
    linear-gradient(90deg, rgba(24, 123, 255, .1) 1px, transparent 1px);
  background-size: 48px 48px;
  mask-image: linear-gradient(to bottom, transparent, #000 18%, #000 83%, transparent);
  content: '';
}
.lynx-template-19-canvas::after {
  position: absolute;
  right: 12%;
  bottom: -22px;
  left: 12%;
  z-index: -1;
  height: 82px;
  border: 1px solid rgba(25, 130, 255, .38);
  background: radial-gradient(ellipse, rgba(10, 93, 255, .4), transparent 66%);
  box-shadow: 0 0 55px rgba(0, 103, 255, .34);
  transform: perspective(160px) rotateX(63deg);
  content: '';
}
.reference-plate {
  position: absolute;
  inset: 0;
  z-index: -2;
  width: 100%;
  height: 100%;
  object-fit: fill;
  opacity: .012;
  filter: saturate(1.12) contrast(1.03) blur(1px);
  mix-blend-mode: screen;
}
.reference-veil { position: absolute; inset: 0; z-index: -1; background: rgba(1, 7, 29, .22); pointer-events: none; }
.top-telemetry { position: absolute; top: 7px; left: 9px; display: flex; width: 650px; gap: 10px; opacity: .46; }
.top-telemetry i { width: 28px; height: 8px; border: 1px solid rgba(37, 134, 255, .38); background: linear-gradient(90deg, rgba(27, 83, 192, .1), rgba(20, 138, 255, .34)); }
.edge-rails i { position: absolute; height: 3px; background: linear-gradient(90deg, transparent, #178dff, #2ee6ff, transparent); box-shadow: 0 0 10px rgba(25, 178, 255, .5); }
.edge-rails i:nth-child(1) { top: 70px; left: 0; width: 470px; transform: skewX(28deg); }
.edge-rails i:nth-child(2) { top: 70px; right: 0; width: 470px; transform: skewX(-28deg); }
.edge-rails i:nth-child(3) { bottom: 22px; left: 0; width: 490px; }
.edge-rails i:nth-child(4) { right: 0; bottom: 22px; width: 490px; }

.dashboard-title-bar {
  position: absolute;
  top: 0;
  left: 50%;
  display: grid;
  width: 650px;
  height: 90px;
  place-items: center;
  transform: translateX(-50%);
}
.dashboard-title-bar::before {
  position: absolute;
  inset: 0 38px 9px;
  border: 1px solid rgba(37, 145, 255, .75);
  background: linear-gradient(180deg, rgba(15, 92, 205, .74), rgba(4, 35, 105, .78));
  box-shadow: inset 0 0 30px rgba(45, 199, 255, .19), 0 0 22px rgba(23, 107, 255, .24);
  clip-path: polygon(8% 0, 92% 0, 100% 58%, 92% 100%, 8% 100%, 0 58%);
  content: '';
}
.dashboard-title-bar::after {
  position: absolute;
  right: 94px;
  bottom: 6px;
  left: 94px;
  height: 2px;
  background: linear-gradient(90deg, transparent, #48e8ff 22%, #4396ff 78%, transparent);
  box-shadow: 0 0 12px #168dff;
  content: '';
}
.dashboard-title-bar h1 {
  position: relative;
  z-index: 1;
  max-width: 560px;
  margin: -8px 0 0;
  overflow: hidden;
  color: #f3fbff;
  font: 700 39px/1 'Microsoft YaHei UI', sans-serif;
  letter-spacing: .06em;
  text-align: center;
  text-overflow: ellipsis;
  text-shadow: 0 0 8px rgba(94, 226, 255, .68), 0 0 24px rgba(34, 119, 255, .55);
  white-space: nowrap;
}
.title-wing { position: absolute; top: 31px; width: 290px; height: 18px; border-top: 2px solid rgba(31, 167, 255, .62); }
.title-wing::before { position: absolute; top: -5px; width: 9px; height: 9px; border-radius: 50%; background: #4ce5ff; box-shadow: 0 0 11px #37bfff; content: ''; }
.title-wing.left { right: 614px; transform: skewX(-33deg); }
.title-wing.right { left: 614px; transform: skewX(33deg); }
.title-wing.left::before { left: 0; }
.title-wing.right::before { right: 0; }

.dashboard-clock { position: absolute; top: 20px; right: 38px; display: grid; justify-items: end; color: #d7f4ff; }
.dashboard-clock::before { position: absolute; top: 1px; right: -18px; width: 6px; height: 6px; border-radius: 50%; background: #39e9be; box-shadow: 0 0 8px #39e9be; content: ''; }
.dashboard-clock.loading::before { background: #4ebdff; }
.dashboard-clock.warning::before { background: #ffc357; }
.dashboard-clock small { color: #5da2d8; font: 9px Consolas, monospace; letter-spacing: .2em; }
.dashboard-clock strong { margin-top: 7px; font: 500 13px/1 'Microsoft YaHei UI', sans-serif; }
.dashboard-clock b { margin-top: 5px; color: #45d7ff; font: 700 24px/1 Bahnschrift, monospace; letter-spacing: .13em; text-shadow: 0 0 10px rgba(56, 196, 255, .48); }

.metric { position: absolute; top: 94px; width: 245px; height: 116px; }
.metric-one { left: 66px; }
.metric-two { left: 330px; }
.metric-three { right: 330px; }
.metric-four { right: 66px; }
.hero-total {
  position: absolute;
  top: 94px;
  left: 50%;
  display: grid;
  width: 384px;
  height: 116px;
  place-content: center;
  justify-items: center;
  border: 1px solid rgba(57, 166, 255, .72);
  color: #ecfaff;
  background: linear-gradient(180deg, rgba(11, 57, 129, .78), rgba(4, 31, 88, .75));
  box-shadow: inset 0 0 20px rgba(39, 132, 255, .2), 0 0 16px rgba(18, 116, 255, .16);
  transform: translateX(-50%);
}
.hero-total::before,
.hero-total::after { position: absolute; top: -5px; bottom: -5px; width: 12px; border-block: 1px solid #2dd1ff; content: ''; }
.hero-total::before { left: -6px; border-left: 2px solid #2dd1ff; }
.hero-total::after { right: -6px; border-right: 2px solid #2dd1ff; }
.hero-total > span { color: #a9cbe1; font: 500 14px 'Microsoft YaHei UI', sans-serif; }
.hero-total > strong { margin-top: 5px; color: #4bdcff; font: 700 34px/1 Bahnschrift, 'Microsoft YaHei UI', sans-serif; letter-spacing: .025em; text-shadow: 0 0 13px rgba(38, 200, 255, .55); }
.hero-total > strong em { color: #dff7ff; font: 500 13px 'Microsoft YaHei UI', sans-serif; font-style: normal; }
.hero-total > small { margin-top: 6px; color: #6fa8cc; font: 11px Bahnschrift, sans-serif; }
.hero-date { position: absolute; top: 218px; left: 50%; display: flex; align-items: center; gap: 10px; color: #9bcce7; transform: translateX(-50%); }
.hero-date i { width: 32px; height: 1px; background: linear-gradient(90deg, transparent, #2cc7ff); }
.hero-date i:last-child { transform: scaleX(-1); }
.hero-date span { padding: 5px 17px; border-block: 1px solid rgba(36, 155, 255, .38); background: rgba(5, 37, 91, .54); font: 11px Bahnschrift, 'Microsoft YaHei UI', sans-serif; }
.status-caption { position: absolute; top: 255px; left: 50%; display: flex; width: 430px; align-items: center; gap: 10px; color: #bcecff; transform: translateX(-50%); }
.status-caption span { height: 1px; flex: 1; background: linear-gradient(90deg, transparent, #2fbdff); }
.status-caption span:last-child { transform: scaleX(-1); }
.status-caption b { font: 600 14px 'Microsoft YaHei UI', sans-serif; }
.status-nodes { position: absolute; top: 284px; left: 50%; display: grid; width: 760px; grid-template-columns: repeat(6, 1fr); gap: 12px; transform: translateX(-50%); }
.status-nodes article { --node-color: #2fd6ff; position: relative; display: grid; justify-items: center; color: #a8cce0; }
.status-nodes article.tone-blue { --node-color: #407eff; }
.status-nodes article.tone-amber { --node-color: #ffbe4f; }
.status-nodes article > span { display: grid; width: 82px; height: 72px; place-items: center; border: 1px solid color-mix(in srgb, var(--node-color) 74%, transparent); background: linear-gradient(180deg, color-mix(in srgb, var(--node-color) 25%, #041b4b), #061942); filter: drop-shadow(0 0 12px color-mix(in srgb, var(--node-color) 28%, transparent)); clip-path: polygon(25% 0,75% 0,100% 50%,75% 100%,25% 100%,0 50%); }
.status-nodes article > span::before { position: absolute; width: 58px; height: 48px; border: 1px solid color-mix(in srgb, var(--node-color) 38%, transparent); clip-path: inherit; content: ''; }
.status-nodes article > span i { color: var(--node-color); font: 700 21px Bahnschrift, sans-serif; font-style: normal; text-shadow: 0 0 9px currentColor; }
.status-nodes article small { margin-top: 6px; font: 11px 'Microsoft YaHei UI', sans-serif; white-space: nowrap; }
.status-nodes article > b { position: absolute; top: 2px; right: 14px; color: color-mix(in srgb, var(--node-color) 46%, transparent); font: 8px Consolas, monospace; }

.panel.alert-ranking { top: 278px; left: 70px; width: 425px; height: 286px; }
.panel.region-ranking { top: 590px; left: 70px; width: 425px; height: 423px; }
.panel.problem-table { top: 472px; left: 50%; width: 810px; height: 500px; transform: translateX(-50%); }
.panel.device-distribution { top: 278px; right: 55px; width: 442px; height: 286px; }
.panel.sales-trend { right: 55px; bottom: 67px; width: 442px; height: 423px; }

.ranking-list { display: grid; gap: 10px; margin: 0; padding: 5px 3px 0; list-style: none; }
.ranking-list li { display: grid; min-height: 30px; grid-template-columns: 88px minmax(0, 1fr) 74px; align-items: center; gap: 9px; color: #9fc2d9; font: 12px 'Microsoft YaHei UI', sans-serif; cursor: pointer; }
.ranking-list li:focus-visible { outline: 1px solid #43d8ff; outline-offset: 2px; }
.ranking-list li > span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ranking-list li > b { height: 9px; overflow: hidden; border-radius: 1px; background: rgba(33, 79, 137, .54); box-shadow: inset 0 0 4px rgba(0, 0, 0, .5); }
.ranking-list li > b i { display: block; height: 100%; background: linear-gradient(90deg, #1672f7, #37d9ff); box-shadow: 0 0 8px rgba(44, 201, 255, .38); transition: width .28s ease; }
.alert-list li > b i { background: linear-gradient(90deg, #267dff, #ffbe54); }
.ranking-list li > em { color: #e8f9ff; font: 600 11px Bahnschrift, 'Microsoft YaHei UI', sans-serif; font-style: normal; text-align: right; }
.panel-empty { position: absolute; inset: 0; display: grid; place-items: center; color: #527a9d; font: 13px 'Microsoft YaHei UI', sans-serif; }

.problem-board { position: absolute; inset: 0; overflow: hidden; }
.problem-head,
.problem-rows button { display: grid; grid-template-columns: .8fr 1.05fr .72fr 1.25fr 1.28fr; align-items: center; }
.problem-head { height: 42px; padding: 0 12px; color: #6fd9ff; background: linear-gradient(90deg, rgba(31, 104, 213, .38), rgba(18, 70, 155, .58), rgba(31, 104, 213, .38)); font: 600 12px 'Microsoft YaHei UI', sans-serif; text-align: center; }
.problem-rows { overflow: hidden; }
.problem-rows button { width: 100%; height: 46px; padding: 0 12px; border: 0; color: #b8d1e1; background: rgba(7, 27, 68, .62); font: 12px 'Microsoft YaHei UI', sans-serif; text-align: center; cursor: pointer; }
.problem-rows button:nth-child(even) { background: rgba(11, 49, 97, .52); }
.problem-rows button:hover { background: rgba(24, 90, 159, .55); }
.problem-rows button:focus-visible { outline: 1px solid #46d4ff; outline-offset: -2px; }
.problem-rows span { overflow: hidden; padding: 0 6px; text-overflow: ellipsis; white-space: nowrap; }
.problem-rows span.danger { color: #ffbe55; text-shadow: 0 0 7px rgba(255, 190, 85, .44); }

.donut-layout { display: grid; height: 100%; grid-template-columns: 56% 44%; align-items: center; }
.donut-chart { position: relative; display: grid; width: 158px; height: 158px; place-items: center; margin: auto; border-radius: 50%; box-shadow: 0 0 23px rgba(31, 134, 255, .18); }
.donut-chart::before { position: absolute; inset: 30px; border: 1px solid rgba(46, 145, 255, .35); border-radius: 50%; background: #041735; box-shadow: inset 0 0 20px rgba(27, 106, 212, .28); content: ''; }
.donut-chart::after { position: absolute; inset: -10px; border: 1px dashed rgba(38, 171, 255, .17); border-radius: 50%; content: ''; }
.donut-chart span { position: relative; z-index: 1; display: grid; justify-items: center; }
.donut-chart strong { color: #55dcff; font: 700 25px Bahnschrift, sans-serif; }
.donut-chart small { margin-top: 2px; color: #789cb8; font: 10px 'Microsoft YaHei UI', sans-serif; }
.donut-layout ul { display: grid; gap: 12px; margin: 0; padding: 0 6px 0 0; list-style: none; }
.donut-layout li { display: grid; grid-template-columns: 8px minmax(0, 1fr) auto; align-items: center; gap: 7px; color: #8fb6cf; font: 11px 'Microsoft YaHei UI', sans-serif; }
.donut-layout li i { --legend-colors: #24c9ff; width: 7px; height: 7px; border-radius: 50%; background: hsl(calc(194deg + var(--legend-index) * 37deg) 88% 59%); box-shadow: 0 0 6px currentColor; }
.donut-layout li span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.donut-layout li b { color: #d9f5ff; font: 600 11px Bahnschrift, sans-serif; }

.line-chart { position: absolute; inset: 8px 6px 17px; }
.line-chart svg { width: 100%; height: calc(100% - 16px); overflow: visible; }
.chart-grid line { stroke: rgba(83, 152, 202, .32); stroke-width: 1; vector-effect: non-scaling-stroke; }
.chart-area { fill: url(#t19-line-area); }
.chart-line-glow { fill: none; stroke: rgba(46, 151, 255, .45); stroke-width: 6; filter: url(#t19-line-glow); }
.chart-line { fill: none; stroke: #38b7ff; stroke-width: 2; vector-effect: non-scaling-stroke; }
.chart-dots circle { fill: #a7f3ff; stroke: #188cff; stroke-width: 1.5; vector-effect: non-scaling-stroke; }
.axis-label { position: absolute; bottom: 0; color: #638aa9; font: 9px Bahnschrift, sans-serif; transform: translateX(-50%); white-space: nowrap; }

@media (prefers-reduced-motion: reduce) {
  .ranking-list li > b i { transition: none; }
}
</style>
