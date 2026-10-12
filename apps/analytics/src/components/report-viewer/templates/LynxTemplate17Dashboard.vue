<script setup lang="ts">
/**
 * LynxTemplate17 财务分析综合看板模板（按 widget 的 panelVariant/visualRole 归类渲染）。
 * 路由：无独立路由，由 ReportViewer / JimuBigScreenReplica 按模板选择渲染。
 * 依赖接口：无独立请求；screen.widgets 由父组件加载，图表类型靠组件名/变体关键字匹配（见 chartWidget）。
 * 关键交互：drilldown 事件上抛点击上下文；只渲染 visible !== false 的组件。
 * 约束：关键字匹配是"兜底"策略（模板元数据缺失时才使用），修改匹配规则需同步模板元数据；
 *   与工作流/大屏域重叠的模板组件，本文件仅补充注释，不改动模板结构。
 */
import { computed } from 'vue'

type Widget = Record<string, any>
type Point = { label: string; value: number; display: string; row: Record<string, any> }

const props = defineProps<{ screen: Record<string, any>; zoom: number }>()
const emit = defineEmits<{ drilldown: [payload: Record<string, any>] }>()

const canvasStyle = computed(() => ({ transform: `translate(-50%, -50%) scale(${props.zoom})` }))
const title = computed(() => String(props.screen.name || '财务分析综合看板'))
const widgets = computed<Widget[]>(() => (props.screen.widgets || []).filter((item: Widget) => item && item.visible !== false))

function variant(widget: Widget) { return String(widget.panelVariant || widget.visualRole || '').toLowerCase() }
function component(widget: Widget) { return String(widget.component || '').toLowerCase() }
function isMetric(widget: Widget) { return variant(widget).includes('template-17-metric') || component(widget).includes('statssummary') }
function chartWidget(kind: string, fallback = 0) {
  const byVariant = widgets.value.find((item) => variant(item).includes(`template-17-${kind}`))
  if (byVariant) return byVariant
  const patterns: Record<string, RegExp> = { line: /line/, area: /area/, pie: /pie|ring|doughnut/, bar: /bar|column/, hbar: /horizontal|hbar|ranking/ }
  return widgets.value.filter((item) => patterns[kind]?.test(component(item)))[fallback]
}

function rows(widget?: Widget) {
  if (!widget) return []
  if (Array.isArray(widget.data)) return widget.data
  if (Array.isArray(widget.config?.chartDataParsed)) return widget.config.chartDataParsed
  try { const parsed = JSON.parse(widget.config?.chartData || '[]'); return Array.isArray(parsed) ? parsed : [] } catch { return [] }
}

function numeric(value: unknown) {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  const parsed = Number(String(value ?? '').replaceAll(',', ''))
  return Number.isFinite(parsed) ? parsed : 0
}

function numericEntry(row: Record<string, any>) {
  const entries = Object.entries(row || {}).filter(([, value]) => (
    typeof value === 'number' || (value !== '' && Number.isFinite(Number(value)))
  ))
  return entries.find(([key]) => (
    /金额|总额|收入|成本|利润|余额|资产|负债|数量|合计|value|amount|total|count|sum|metric/i.test(key)
    && !/同比|环比|增长|变化|yoy|mom|growth|change|rate/i.test(key)
  )) || entries.find(([key]) => !/(^|_)(id|code|year|month|day)$|编号|序号/i.test(key)) || entries[0]
}

function points(widget?: Widget): Point[] {
  return rows(widget).slice(0, 12).map((row: Record<string, any>, index: number) => {
    const entries = Object.entries(row || {})
    const valueEntry = numericEntry(row)
    const labelEntry = entries.find(([key, value]) => key !== valueEntry?.[0] && typeof value === 'string')
      || entries.find(([key]) => key !== valueEntry?.[0])
    const value = numeric(valueEntry?.[1])
    return { label: String(labelEntry?.[1] ?? index + 1), value, display: formatValue(value), row }
  })
}

function formatValue(value: number) {
  const absolute = Math.abs(value)
  if (absolute >= 100000000) return `${(value / 100000000).toFixed(2)}亿`
  if (absolute >= 10000) return `${(value / 10000).toFixed(2)}万`
  return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 }).format(value)
}

function metricModel(widget: Widget | undefined, index: number) {
  const row = rows(widget)[0] || {}
  const valueEntry = numericEntry(row)
  const comparisonEntry = Object.entries(row).find(([key, value]) => (
    key !== valueEntry?.[0]
    && /同比|环比|增长|变化|yoy|mom|growth|change|rate/i.test(key)
    && value !== ''
    && Number.isFinite(Number(String(value).replace('%', '')))
  ))
  const available = Boolean(valueEntry)
  const value = available ? numeric(valueEntry?.[1]) : 0
  const comparison = comparisonEntry
    ? Number(String(comparisonEntry[1]).replace('%', ''))
    : null
  return {
    widget,
    label: String(widget?.componentName || widget?.title || ['营业总收入', '营业总成本', '利润总额', '资产总额', '负债总额', '净资产'][index]),
    value,
    display: available ? formatValue(value) : '—',
    available,
    comparison,
    row,
    tone: index % 6,
  }
}

const metricWidgets = computed(() => widgets.value.filter(isMetric).slice(0, 6))
const metrics = computed(() => Array.from({ length: 6 }, (_, index) => metricModel(metricWidgets.value[index], index)))
const panels = computed(() => ({
  line: chartWidget('line'), area: chartWidget('area'), pie: chartWidget('pie'),
  bar: chartWidget('bar', 0), hbar: chartWidget('hbar'),
}))

function geometry(widget?: Widget) {
  const source = points(widget)
  const width = 760, height = 210, left = 26, right = 18, top = 18, bottom = 34
  if (!source.length) return { source, line: '', area: '', dots: [] as Array<Record<string, any>> }
  const values = source.map(item => item.value)
  const min = Math.min(0, ...values), max = Math.max(1, ...values), range = Math.max(1, max - min)
  const dots = source.map((item, index) => ({ ...item,
    x: source.length === 1 ? width / 2 : left + index * (width - left - right) / (source.length - 1),
    y: top + (max - item.value) / range * (height - top - bottom),
  }))
  const line = dots.map((item, index) => `${index ? 'L' : 'M'}${item.x.toFixed(1)},${item.y.toFixed(1)}`).join(' ')
  return { source, dots, line, area: `${line} L${dots.at(-1)!.x},${height - bottom} L${dots[0].x},${height - bottom} Z` }
}

const lineGeometry = computed(() => geometry(panels.value.line))
const areaGeometry = computed(() => geometry(panels.value.area))
const barPoints = computed(() => points(panels.value.bar).slice(0, 8))
const hbarPoints = computed(() => points(panels.value.hbar).slice(0, 7))
const piePoints = computed(() => points(panels.value.pie).slice(0, 6))
const barMax = computed(() => Math.max(1, ...barPoints.value.map(item => item.value)))
const hbarMax = computed(() => Math.max(1, ...hbarPoints.value.map(item => item.value)))
const pieTotal = computed(() => piePoints.value.reduce((sum, item) => sum + Math.max(0, item.value), 0))
const pieStyle = computed(() => {
  const colors = ['#4ec9d1', '#75a6dd', '#f5c24f', '#39d99a', '#a074ec', '#ff6478']
  if (!pieTotal.value) return { background: '#dceff1' }
  let cursor = 0
  const stops = piePoints.value.map((item, index) => {
    const start = cursor; cursor += Math.max(0, item.value) / pieTotal.value * 100
    return `${colors[index % colors.length]} ${start}% ${cursor}%`
  })
  return { background: `conic-gradient(${stops.join(',')})` }
})

function drill(widget: Widget | undefined, point: Point | Record<string, any>) {
  if (widget?.id) emit('drilldown', { widget, point: 'row' in point ? point.row : point })
}
</script>

<template>
  <section class="finance-viewport" aria-label="智慧云脑 Cloud Brain 财务分析综合看板">
    <div class="finance-canvas" :style="canvasStyle">
      <header class="finance-title"><h1>{{ title }}</h1><div><small>统计日期</small><strong>{{ new Date().toLocaleDateString('zh-CN') }}</strong></div></header>

      <section class="metric-strip">
        <button v-for="metric in metrics" :key="metric.label" type="button" :class="`tone-${metric.tone}`" :disabled="!metric.widget" @click="drill(metric.widget, metric.row)">
          <span class="metric-copy"><small>{{ metric.label }}</small><strong>{{ metric.display }}</strong><em v-if="metric.comparison !== null">变化 <b>{{ metric.comparison >= 0 ? '▲' : '▼' }}</b> {{ Math.abs(metric.comparison).toFixed(1) }}%</em><em v-else>{{ metric.available ? '已验证查询' : '暂无数据' }}</em></span>
          <i><span></span></i>
        </button>
      </section>

      <section class="finance-panel trend-panel line-panel">
        <header><h2>{{ panels.line?.componentName || '各月指标趋势分析' }}</h2><span><i></i>真实查询结果</span></header>
        <div v-if="lineGeometry.dots.length" class="line-plot">
          <svg viewBox="0 0 760 210" preserveAspectRatio="none">
            <defs><linearGradient id="finance-line-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#6178ee" stop-opacity=".44"/><stop offset="1" stop-color="#6178ee" stop-opacity=".03"/></linearGradient></defs>
            <g class="grid"><line v-for="index in 5" :key="index" x1="26" x2="742" :y1="20 + index * 30" :y2="20 + index * 30" /></g>
            <path class="area" :d="lineGeometry.area"/><path class="line" :d="lineGeometry.line"/>
            <circle v-for="(point, index) in lineGeometry.dots" :key="index" :cx="point.x" :cy="point.y" r="4" @click="drill(panels.line, point)"/>
          </svg>
          <div class="axis"><span v-for="point in lineGeometry.dots" :key="point.label">{{ point.label }}</span></div>
        </div><p v-else class="empty">当前查询没有趋势数据</p>
      </section>

      <section class="finance-panel trend-panel area-panel">
        <header><h2>{{ panels.area?.componentName || '资金流变化趋势' }}</h2><span><i></i>动态数据</span></header>
        <div v-if="areaGeometry.dots.length" class="line-plot teal">
          <svg viewBox="0 0 760 210" preserveAspectRatio="none"><defs><linearGradient id="finance-area-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#35bec5" stop-opacity=".38"/><stop offset="1" stop-color="#35bec5" stop-opacity=".02"/></linearGradient></defs><g class="grid"><line v-for="index in 5" :key="index" x1="26" x2="742" :y1="20 + index * 30" :y2="20 + index * 30" /></g><path class="area" :d="areaGeometry.area"/><path class="line" :d="areaGeometry.line"/><circle v-for="(point, index) in areaGeometry.dots" :key="index" :cx="point.x" :cy="point.y" r="4" @click="drill(panels.area, point)"/></svg>
          <div class="axis"><span v-for="point in areaGeometry.dots" :key="point.label">{{ point.label }}</span></div>
        </div><p v-else class="empty">当前查询没有资金流数据</p>
      </section>

      <section class="finance-panel pie-panel">
        <header><h2>{{ panels.pie?.componentName || '资产指标分布' }}</h2><span>结构占比</span></header>
        <div v-if="piePoints.length" class="pie-layout"><button type="button" class="pie" :style="pieStyle" @click="drill(panels.pie, piePoints[0])"><span><strong>{{ piePoints.length }}</strong><small>分类</small></span></button><ol><li v-for="(point, index) in piePoints" :key="point.label"><i :style="{ '--index': index }"></i><span>{{ point.label }}</span><b>{{ point.display }}</b></li></ol></div><p v-else class="empty">当前查询没有结构数据</p>
      </section>

      <section class="finance-panel bar-panel">
        <header><h2>{{ panels.bar?.componentName || '盈利指标分布' }}</h2><span>期间对比</span></header>
        <div v-if="barPoints.length" class="bars"><button v-for="point in barPoints" :key="point.label" type="button" @click="drill(panels.bar, point)"><span><i :style="{ height: `${Math.max(4, point.value / barMax * 100)}%` }"></i></span><small>{{ point.label }}</small><em>{{ point.display }}</em></button></div><p v-else class="empty">当前查询没有对比数据</p>
      </section>

      <section class="finance-panel ranking-panel">
        <header><h2>{{ panels.hbar?.componentName || '各区指标分布' }}</h2><span>重点科目</span></header>
        <ol v-if="hbarPoints.length"><li v-for="point in hbarPoints" :key="point.label" tabindex="0" @click="drill(panels.hbar, point)" @keydown.enter="drill(panels.hbar, point)"><span>{{ point.label }}</span><b><i :style="{ width: `${Math.max(3, point.value / hbarMax * 100)}%` }"></i></b><em>{{ point.display }}</em></li></ol><p v-else class="empty">当前查询没有排行数据</p>
      </section>
    </div>
  </section>
</template>

<style scoped>
.finance-viewport { position: absolute; inset: 0; overflow: hidden; background: #e9f5f8; }
.finance-canvas { position: absolute; top: 50%; left: 50%; width: 1920px; height: 1080px; overflow: hidden; color: #17344c; background: radial-gradient(circle at 76% 4%, rgba(163, 221, 237, .58), transparent 28%), radial-gradient(circle at 14% 72%, rgba(159, 186, 224, .34), transparent 30%), linear-gradient(135deg, #edf5fb 0%, #dff3f5 58%, #e6f8f5 100%); box-shadow: inset 0 0 100px rgba(65, 116, 155, .08); transform-origin: center; }
.finance-canvas::before { position: absolute; inset: 0; opacity: .28; background-image: linear-gradient(rgba(70, 148, 178, .06) 1px, transparent 1px), linear-gradient(90deg, rgba(70, 148, 178, .05) 1px, transparent 1px); background-size: 42px 42px; content: ''; pointer-events: none; }
.finance-title { position: absolute; top: 0; right: 0; left: 0; z-index: 2; display: flex; height: 84px; align-items: center; justify-content: space-between; padding: 0 34px 0 42px; background: rgba(246, 252, 255, .66); border-bottom: 1px solid rgba(89, 153, 180, .28); box-shadow: 0 7px 24px rgba(66, 110, 143, .06); backdrop-filter: blur(12px); }
.finance-title h1 { margin: 0; color: #173750; font: 700 32px/1.1 'Microsoft YaHei UI', sans-serif; letter-spacing: .06em; }.finance-title > div { display: flex; align-items: baseline; gap: 16px; }.finance-title small { color: #6e8496; font-size: 12px; }.finance-title strong { color: #203f55; font: 600 18px ui-monospace, Consolas, monospace; }
.metric-strip { position: absolute; top: 104px; right: 28px; left: 28px; display: grid; height: 150px; grid-template-columns: repeat(6, 1fr); gap: 16px; }
.metric-strip button { position: relative; display: flex; min-width: 0; align-items: center; justify-content: space-between; overflow: hidden; padding: 19px 17px 17px 20px; color: #213a4f; background: linear-gradient(135deg, rgba(255,255,255,.95), rgba(248,252,255,.72)); border: 1px solid rgba(255,255,255,.9); border-radius: 20px; box-shadow: 0 10px 28px rgba(65, 109, 142, .11), inset 0 1px 0 #fff; text-align: left; cursor: pointer; }
.metric-strip button::before { position: absolute; inset: 0; opacity: .42; background: radial-gradient(circle at 85% 16%, var(--tone), transparent 39%); content: ''; }.metric-strip .tone-0 { --tone: #a8dfff; }.metric-strip .tone-1 { --tone: #b9d7ff; }.metric-strip .tone-2 { --tone: #fff0a9; }.metric-strip .tone-3 { --tone: #c9f0df; }.metric-strip .tone-4 { --tone: #c9e5ff; }.metric-strip .tone-5 { --tone: #bfeee8; }
.metric-copy { position: relative; z-index: 1; display: flex; min-width: 0; flex-direction: column; }.metric-copy small { overflow: hidden; color: #63788b; font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }.metric-copy strong { margin-top: 10px; color: #17394f; font: 700 25px ui-monospace, Consolas, monospace; letter-spacing: -.025em; }.metric-copy em { margin-top: 11px; color: #6f8293; font: normal 9px ui-monospace, Consolas, monospace; }.metric-copy b { color: #42b879; }
.metric-strip button > i { position: relative; z-index: 1; display: grid; width: 58px; height: 58px; flex: 0 0 auto; place-items: center; border: 7px solid rgba(255,255,255,.68); border-radius: 50%; background: linear-gradient(145deg, #55bef1, #597de6); box-shadow: 0 7px 18px rgba(73, 127, 196, .28); }.metric-strip button > i::before, .metric-strip button > i::after { position: absolute; width: 22px; height: 2px; background: #fff; content: ''; }.metric-strip button > i::after { transform: rotate(90deg); }
.finance-panel { position: absolute; overflow: hidden; background: rgba(250, 254, 255, .79); border: 1px solid rgba(255,255,255,.84); border-radius: 20px; box-shadow: 0 10px 30px rgba(63, 108, 143, .095), inset 0 1px 0 #fff; backdrop-filter: blur(12px); }.finance-panel header { display: flex; height: 49px; align-items: center; justify-content: space-between; padding: 0 20px; border-bottom: 1px solid rgba(97, 151, 177, .12); }.finance-panel h2 { margin: 0; color: #1d3a50; font-size: 14px; font-weight: 650; }.finance-panel header > span { display: inline-flex; align-items: center; gap: 6px; color: #7590a2; font-size: 9px; }.finance-panel header > span i { width: 7px; height: 7px; background: #45c2c8; border-radius: 50%; box-shadow: 0 0 0 3px rgba(69,194,200,.12); }
.line-panel { top: 276px; left: 28px; width: 914px; height: 330px; }.area-panel { top: 276px; left: 960px; width: 932px; height: 330px; }.pie-panel { top: 624px; left: 28px; width: 500px; height: 428px; }.bar-panel { top: 624px; left: 546px; width: 762px; height: 428px; }.ranking-panel { top: 624px; left: 1326px; width: 566px; height: 428px; }
.line-plot { position: relative; height: 260px; padding: 10px 20px 0; }.line-plot svg { display: block; width: 100%; height: 218px; overflow: visible; }.line-plot .grid line { stroke: rgba(70,119,148,.12); stroke-width: 1; }.line-plot .area { fill: url(#finance-line-fill); }.line-plot .line { fill: none; stroke: #596ee0; stroke-width: 3; filter: drop-shadow(0 3px 5px rgba(89,110,224,.22)); }.line-plot circle { fill: #fff; stroke: #596ee0; stroke-width: 2.4; cursor: pointer; }.line-plot.teal .area { fill: url(#finance-area-fill); }.line-plot.teal .line { stroke: #3bbac1; }.line-plot.teal circle { stroke: #3bbac1; }.axis { display: flex; margin: -3px 17px 0 29px; justify-content: space-between; color: #8495a5; font: 9px ui-monospace, Consolas, monospace; }.axis span { max-width: 76px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pie-layout { display: grid; height: 360px; grid-template-columns: 215px 1fr; align-items: center; gap: 14px; padding: 10px 18px 15px; }.pie { display: grid; width: 190px; height: 190px; place-items: center; border: 0; border-radius: 50%; box-shadow: 0 10px 30px rgba(47,135,162,.12); cursor: pointer; }.pie > span { display: flex; width: 112px; height: 112px; align-items: center; justify-content: center; flex-direction: column; color: #315069; background: rgba(250,254,255,.96); border-radius: 50%; box-shadow: inset 0 0 16px rgba(63,126,151,.08); }.pie strong { font: 700 27px ui-monospace, Consolas, monospace; }.pie small { color: #7790a1; font-size: 9px; }.pie-layout ol, .ranking-panel ol { margin: 0; padding: 0; list-style: none; }.pie-layout li { display: grid; grid-template-columns: 8px 1fr auto; gap: 7px; align-items: center; min-height: 40px; border-bottom: 1px solid rgba(93,139,163,.11); color: #61788b; font-size: 10px; }.pie-layout li i { width: 7px; height: 7px; background: hsl(calc(183 + var(--index) * 42), 66%, 58%); border-radius: 2px; }.pie-layout li b { color: #284b63; font: 600 10px ui-monospace, Consolas, monospace; }
.bars { display: flex; height: 350px; align-items: flex-end; gap: 14px; padding: 22px 25px 20px; }.bars button { display: grid; min-width: 0; height: 100%; flex: 1; grid-template-rows: 1fr 24px 20px; padding: 0; color: #74899b; background: transparent; border: 0; cursor: pointer; }.bars button > span { display: flex; align-items: flex-end; justify-content: center; border-bottom: 1px solid rgba(77,130,157,.2); background: repeating-linear-gradient(to top, rgba(75,130,159,.1) 0 1px, transparent 1px 48px); }.bars button i { display: block; width: min(54px, 70%); min-height: 4px; background: linear-gradient(180deg, #4ec6cc, #75a7dd); border-radius: 5px 5px 0 0; box-shadow: 0 5px 12px rgba(65,159,183,.15); }.bars small { margin-top: 6px; overflow: hidden; font-size: 9px; text-overflow: ellipsis; white-space: nowrap; }.bars em { color: #31566f; font: normal 9px ui-monospace, Consolas, monospace; }
.ranking-panel ol { padding: 14px 20px; }.ranking-panel li { display: grid; grid-template-columns: 104px 1fr 72px; gap: 10px; align-items: center; min-height: 45px; color: #61788b; border-bottom: 1px solid rgba(84,133,158,.1); font-size: 10px; cursor: pointer; }.ranking-panel li > span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.ranking-panel li > b { display: block; height: 10px; overflow: hidden; background: #edf2f5; border-radius: 5px; }.ranking-panel li i { display: block; height: 100%; background: linear-gradient(90deg, #51c4ca, #7da7df); border-radius: inherit; }.ranking-panel li em { color: #31566f; font: normal 9px ui-monospace, Consolas, monospace; text-align: right; }
.empty { display: grid; height: calc(100% - 49px); margin: 0; place-items: center; color: #8597a8; font-size: 11px; }
button:focus-visible, li:focus-visible { outline: 3px solid rgba(39,102,216,.45); outline-offset: 2px; }
@media (prefers-reduced-motion: reduce) { * { scroll-behavior: auto !important; } }
</style>
