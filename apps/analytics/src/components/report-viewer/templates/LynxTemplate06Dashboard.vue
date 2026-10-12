<script setup lang="ts">
/**
 * LynxTemplate06 企业销售目标看板模板（自包含布局与数据整理，不依赖 composable）。
 * 路由：无独立路由，由 ReportViewer / JimuBigScreenReplica 按模板选择渲染。
 * 依赖接口：无独立请求；screen（含 widgets）由父组件加载，参考底图取 BASE_URL 下 template-06.jpg。
 * 关键交互：drilldown 事件上抛点击上下文；每秒刷新时钟（卸载时清理定时器）；缩放由父组件传入的 zoom 控制。
 * 约束：与工作流/大屏域重叠的模板组件，本文件仅补充注释，不改动模板结构。
 */
import { computed, onBeforeUnmount, onMounted, shallowRef } from 'vue'

type Row = Record<string, any>
type Widget = Record<string, any>
type Point = { label: string; rawLabel: string; value: number; display: string; row: Row }

const props = defineProps<{
  screen: Record<string, any>
  zoom: number
}>()

const emit = defineEmits<{
  drilldown: [payload: Record<string, any>]
}>()

const now = shallowRef(new Date())
let clockTimer: ReturnType<typeof setInterval> | undefined

const title = computed(() => String(props.screen.name || '企业销售目标看板'))
const canvasStyle = computed(() => ({ transform: `translate(-50%, -50%) scale(${props.zoom})` }))
const referenceImage = `${import.meta.env.BASE_URL}jimu-screen/templates/template-06.jpg`
const dateText = computed(() => new Intl.DateTimeFormat('zh-CN', {
  year: 'numeric', month: '2-digit', day: '2-digit', weekday: 'short',
}).format(now.value).replaceAll('/', '-'))
const timeText = computed(() => now.value.toLocaleTimeString('zh-CN', { hour12: false }))

const widgets = computed<Widget[]>(() => (props.screen.widgets || []).filter((widget: Widget) => widget?.visible !== false))
function variant(widget?: Widget) { return String(widget?.panelVariant || widget?.visualRole || '').toLowerCase() }
function byVariant(kind: string) { return widgets.value.filter(widget => variant(widget).includes(`template-06-${kind}`)) }

function rawRows(widget?: Widget): any[] {
  if (!widget) return []
  if (Array.isArray(widget.data)) return widget.data
  if (Array.isArray(widget.config?.chartDataParsed)) return widget.config.chartDataParsed
  const raw = widget.config?.chartData
  if (Array.isArray(raw)) return raw
  try {
    const parsed = JSON.parse(raw || '[]')
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

function rows(widget?: Widget): Row[] {
  const headers = (widget?.config?.option?.header || []).map((item: any, index: number) => (
    String(item?.label || item?.name || `字段${index + 1}`)
  ))
  return rawRows(widget).map((row: any) => {
    if (!Array.isArray(row)) return row && typeof row === 'object' ? row : { value: row }
    return Object.fromEntries(row.map((value, index) => [headers[index] || `字段${index + 1}`, value]))
  })
}

function numeric(value: unknown) {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  const parsed = Number(String(value ?? '').replaceAll(',', '').replace('%', ''))
  return Number.isFinite(parsed) ? parsed : 0
}

function numericEntry(row: Row) {
  const entries = Object.entries(row || {}).filter(([, value]) => (
    typeof value === 'number' || (value !== '' && value !== null && Number.isFinite(Number(value)))
  ))
  return entries.find(([key]) => (
    /金额|总额|单价|数量|合计|收入|成本|利润|余额|value|amount|total|count|sum|metric/i.test(key)
    && !/同比|环比|增长|变化|yoy|mom|growth|change|rate/i.test(key)
  )) || entries.find(([key]) => !/(^|_)(id|code|year|month|day)$|编号|序号|autoid/i.test(key)) || entries[0]
}

function labelEntry(row: Row, valueKey = '') {
  const entries = Object.entries(row || {}).filter(([key]) => key !== valueKey)
  return entries.find(([key, value]) => (
    /名称|日期|时间|地区|区域|部门|产品|客户|类型|状态|category|period|date|time|name|label|type|region|department|product|customer/i.test(key)
    && value !== null && value !== ''
  )) || entries.find(([, value]) => typeof value === 'string' && value) || entries[0]
}

function isTechnicalId(value: unknown) {
  const text = String(value ?? '')
  return text.length > 20 && (/^[a-z0-9-]+$/i.test(text) || /[0-9a-f]{16,}/i.test(text))
}

function displayLabel(value: unknown, index: number) {
  if (value === null || value === undefined || String(value).trim() === '') return '未标注'
  const text = String(value).trim().replace(/,+$/, '')
  if (/^\d{4}-\d{2}-\d{2}t/i.test(text)) return text.replace('T', ' ').slice(0, 16)
  if (isTechnicalId(text)) return `业务对象 ${String(index + 1).padStart(2, '0')}`
  return text.length > 18 ? `${text.slice(0, 16)}…` : text
}

function formatNumber(value: number, compact = true) {
  const absolute = Math.abs(value)
  if (compact && absolute >= 100000000) return `${(value / 100000000).toFixed(2)}亿`
  if (compact && absolute >= 10000) return `${(value / 10000).toFixed(2)}万`
  return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: Number.isInteger(value) ? 0 : 2 }).format(value)
}

function points(widget?: Widget, limit = 12): Point[] {
  return rows(widget).slice(0, limit).map((row, index) => {
    const value = numericEntry(row)
    const label = labelEntry(row, value?.[0])
    const number = numeric(value?.[1])
    const rawLabel = String(label?.[1] ?? index + 1)
    return { label: displayLabel(rawLabel, index), rawLabel, value: number, display: formatNumber(number), row }
  })
}

function metricModel(widget: Widget | undefined, index: number) {
  const row = rows(widget)[0] || {}
  const valueEntry = numericEntry(row)
  const value = numeric(valueEntry?.[1])
  const label = String(widget?.componentName || widget?.title || `核心指标 ${index + 1}`)
  return {
    widget,
    label,
    display: valueEntry ? formatNumber(value, !/单价|金额|收入|成本|应收|应付|元/.test(label)) : '—',
    rawValue: value,
    available: Boolean(valueEntry),
  }
}

const metricWidgets = computed(() => byVariant('metric').slice(0, 5))
const metrics = computed(() => Array.from({ length: 5 }, (_, index) => metricModel(metricWidgets.value[index], index)))
const tableWidgets = computed(() => byVariant('table').slice(0, 2))
const lineWidget = computed(() => byVariant('line')[0])
const areaWidget = computed(() => byVariant('area')[0])
const barWidgets = computed(() => byVariant('bar').slice(0, 2))
const horizontalWidget = computed(() => byVariant('hbar')[0])
const pieWidget = computed(() => byVariant('pie')[0])

const linePoints = computed(() => points(lineWidget.value, 10))
const areaPoints = computed(() => points(areaWidget.value, 10))
const topBarPoints = computed(() => points(barWidgets.value[0], 8))
const bottomBarPoints = computed(() => points(barWidgets.value[1], 8))
const horizontalPoints = computed(() => points(horizontalWidget.value, 7))
const piePoints = computed(() => points(pieWidget.value, 6))
const topBarMax = computed(() => Math.max(1, ...topBarPoints.value.map(point => Math.abs(point.value))))
const bottomBarMax = computed(() => Math.max(1, ...bottomBarPoints.value.map(point => Math.abs(point.value))))
const horizontalMax = computed(() => Math.max(1, ...horizontalPoints.value.map(point => Math.abs(point.value))))
const pieTotal = computed(() => piePoints.value.reduce((sum, point) => sum + Math.max(0, point.value), 0))
const pieStyle = computed(() => {
  const colors = ['#25d6d1', '#3f8cff', '#ffd05c', '#55dfa7', '#9f72ed', '#ff7188']
  if (!pieTotal.value) return { background: 'rgba(31, 126, 151, .24)' }
  let cursor = 0
  const stops = piePoints.value.map((point, index) => {
    const start = cursor
    cursor += Math.max(0, point.value) / pieTotal.value * 100
    return `${colors[index % colors.length]} ${start}% ${cursor}%`
  })
  return { background: `conic-gradient(${stops.join(',')})` }
})

function geometry(source: Point[]) {
  const width = 620, height = 154, left = 22, right = 15, top = 13, bottom = 27
  if (!source.length) return { line: '', area: '', dots: [] as Array<Point & { x: number; y: number }> }
  const values = source.map(point => point.value)
  const minimum = Math.min(0, ...values), maximum = Math.max(1, ...values), range = Math.max(1, maximum - minimum)
  const dots = source.map((point, index) => ({
    ...point,
    x: source.length === 1 ? width / 2 : left + index * (width - left - right) / (source.length - 1),
    y: top + (maximum - point.value) / range * (height - top - bottom),
  }))
  const line = dots.map((point, index) => `${index ? 'L' : 'M'}${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(' ')
  return { line, area: `${line} L${dots.at(-1)!.x},${height - bottom} L${dots[0].x},${height - bottom} Z`, dots }
}

const lineGeometry = computed(() => geometry(linePoints.value))
const areaGeometry = computed(() => geometry(areaPoints.value))

const fieldNames: Array<[RegExp, string]> = [
  [/^autoid$/i, '序号'], [/createdon/i, '创建时间'], [/modifiedon/i, '更新时间'],
  [/ownerdepartment/i, '所属部门'], [/owneruser/i, '负责人'], [/isdeleted/i, '状态'],
  [/yingshouzhangkuan.*(?:duizhang|uizhang)/i, '应收对账'], [/xiaoshouchuku.*chanpinmingxi/i, '销售出库明细'],
  [/yingshouzhangkuan.*xiaoshouchuku/i, '应收出库记录'], [/xiaoshouchuku/i, '销售出库'],
  [/chanpinmingxi/i, '产品明细'], [/duizhang/i, '对账记录'],
]

function fieldLabel(key: string) {
  return fieldNames.find(([pattern]) => pattern.test(key))?.[1]
    || key.replace(/Id$/i, '').replace(/([a-z])([A-Z])/g, '$1 $2').replaceAll('_', ' ')
}

function fieldScore(key: string) {
  if (/^autoid$/i.test(key)) return 100
  if (/createdon|modifiedon/i.test(key)) return 90
  if (/xiaoshouchuku|yingshouzhangkuan|chanpinmingxi|duizhang/i.test(key)) return 80
  if (/name|名称|日期|金额|数量|地区|部门|产品/i.test(key)) return 75
  if (/createdby|modifiedby|owneruser|isdeleted/i.test(key)) return 10
  return 50
}

function tableModel(widget?: Widget) {
  const source = rows(widget).slice(0, 8)
  const keys = [...new Set(source.flatMap(row => Object.keys(row)))]
    .sort((left, right) => fieldScore(right) - fieldScore(left)).slice(0, 4)
  return { widget, rows: source, keys, title: String(widget?.componentName || widget?.title || '业务明细') }
}

const tables = computed(() => [tableModel(tableWidgets.value[0]), tableModel(tableWidgets.value[1])])
function tableCell(value: unknown, key: string) {
  if (value === null || value === undefined || value === '') return '—'
  if (/isdeleted/i.test(key)) return numeric(value) ? '已删除' : '有效'
  const text = String(value)
  if (/^\d{4}-\d{2}-\d{2}t/i.test(text)) return text.replace('T', ' ').slice(0, 16)
  if (isTechnicalId(text)) return `…${text.slice(-8)}`
  return text.length > 18 ? `${text.slice(0, 16)}…` : text
}

function drill(widget: Widget | undefined, point: Row | Point | Record<string, any>) {
  if (!widget?.id) return
  emit('drilldown', { widget, point: 'row' in point ? point.row : point })
}

onMounted(() => { clockTimer = setInterval(() => { now.value = new Date() }, 1000) })
onBeforeUnmount(() => { if (clockTimer) clearInterval(clockTimer) })
</script>

<template>
  <section class="lynx-template-06-viewport" aria-label="智慧云脑 Cloud Brain 企业销售目标看板">
    <div class="lynx-template-06-canvas" :style="canvasStyle">
      <img class="reference-plate" :src="referenceImage" alt="" aria-hidden="true">
      <div class="ambient-grid" aria-hidden="true"></div>
      <header class="sales-title"><i></i><h1 data-role="dashboard-title">{{ title }}</h1><i></i></header>
      <div class="sales-clock"><small>LOCAL · LIVE</small><strong>{{ dateText }}</strong><b>{{ timeText }}</b></div>

      <section class="sales-metrics" aria-label="核心指标">
        <button v-for="(metric, index) in metrics" :key="`${metric.label}-${index}`" type="button" :disabled="!metric.widget" @click="drill(metric.widget, { value: metric.rawValue })">
          <span class="metric-corners" aria-hidden="true"></span><small>{{ metric.label }}</small><strong>{{ metric.display }}</strong><em>{{ metric.available ? '已验证真实数据' : '暂无查询结果' }}</em><b>{{ String(index + 1).padStart(2, '0') }}</b>
        </button>
      </section>

      <section v-for="(table, tableIndex) in tables" :key="tableIndex" :class="['data-panel', 'table-panel', `table-${tableIndex + 1}`]">
        <header><h2>{{ table.title }}</h2><span>LIVE DATA</span></header>
        <div v-if="table.rows.length && table.keys.length" class="business-table">
          <div class="table-head" :style="{ gridTemplateColumns: `repeat(${table.keys.length}, minmax(0, 1fr))` }"><b v-for="key in table.keys" :key="key">{{ fieldLabel(key) }}</b></div>
          <button v-for="(row, rowIndex) in table.rows" :key="rowIndex" type="button" :style="{ gridTemplateColumns: `repeat(${table.keys.length}, minmax(0, 1fr))` }" @click="drill(table.widget, row)"><span v-for="key in table.keys" :key="key" :title="String(row[key] ?? '')">{{ tableCell(row[key], key) }}</span></button>
        </div><p v-else class="empty">当前查询没有明细数据</p>
      </section>

      <section class="data-panel chart-panel line-panel"><header><h2>{{ lineWidget?.componentName || '销售趋势' }}</h2><span><i></i>趋势分析</span></header><div v-if="lineGeometry.dots.length" class="line-chart"><svg viewBox="0 0 620 154" preserveAspectRatio="none"><defs><linearGradient id="t06-line-area" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#25d6d1" stop-opacity=".36"/><stop offset="1" stop-color="#25d6d1" stop-opacity=".02"/></linearGradient></defs><g class="chart-grid"><line v-for="index in 4" :key="index" x1="22" x2="605" :y1="15 + index * 25" :y2="15 + index * 25"/></g><path class="area" :d="lineGeometry.area"/><path class="line" :d="lineGeometry.line"/><circle v-for="(point, index) in lineGeometry.dots" :key="index" :cx="point.x" :cy="point.y" r="3.1" @click="drill(lineWidget, point)"/></svg><div class="axis"><span v-for="point in lineGeometry.dots" :key="point.rawLabel" :title="point.rawLabel">{{ point.label }}</span></div></div><p v-else class="empty">当前查询没有趋势数据</p></section>

      <section class="data-panel chart-panel bar-panel top-bar"><header><h2>{{ barWidgets[0]?.componentName || '销售分布' }}</h2><span>分类对比</span></header><div v-if="topBarPoints.length" class="vertical-bars"><button v-for="point in topBarPoints" :key="point.rawLabel" type="button" @click="drill(barWidgets[0], point)"><em>{{ point.display }}</em><span><i :style="{ height: `${Math.max(4, Math.abs(point.value) / topBarMax * 100)}%` }"></i></span><small :title="point.rawLabel">{{ point.label }}</small></button></div><p v-else class="empty">当前查询没有分布数据</p></section>

      <section class="data-panel chart-panel area-panel"><header><h2>{{ areaWidget?.componentName || '收款计划趋势' }}</h2><span><i></i>动态变化</span></header><div v-if="areaGeometry.dots.length" class="line-chart area-chart"><svg viewBox="0 0 620 154" preserveAspectRatio="none"><defs><linearGradient id="t06-area-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#3e9fff" stop-opacity=".42"/><stop offset="1" stop-color="#3e9fff" stop-opacity=".02"/></linearGradient></defs><g class="chart-grid"><line v-for="index in 4" :key="index" x1="22" x2="605" :y1="15 + index * 25" :y2="15 + index * 25"/></g><path class="area" :d="areaGeometry.area"/><path class="line" :d="areaGeometry.line"/><circle v-for="(point, index) in areaGeometry.dots" :key="index" :cx="point.x" :cy="point.y" r="3.1" @click="drill(areaWidget, point)"/></svg><div class="axis"><span v-for="point in areaGeometry.dots" :key="point.rawLabel" :title="point.rawLabel">{{ point.label }}</span></div></div><p v-else class="empty">当前查询没有计划数据</p></section>

      <section class="data-panel chart-panel hbar-panel"><header><h2>{{ horizontalWidget?.componentName || '部门分布' }}</h2><span>业务结构</span></header><ol v-if="horizontalPoints.length"><li v-for="point in horizontalPoints" :key="point.rawLabel" tabindex="0" @click="drill(horizontalWidget, point)" @keydown.enter="drill(horizontalWidget, point)"><span :title="point.rawLabel">{{ point.label }}</span><b><i :style="{ width: `${Math.max(4, Math.abs(point.value) / horizontalMax * 100)}%` }"></i></b><em>{{ point.display }}</em></li></ol><p v-else class="empty">当前查询没有部门数据</p></section>

      <section class="data-panel chart-panel pie-panel"><header><h2>{{ pieWidget?.componentName || '业务占比' }}</h2><span>结构分布</span></header><div v-if="piePoints.length" class="pie-layout"><button type="button" class="pie" :style="pieStyle" @click="drill(pieWidget, piePoints[0])"><span><strong>{{ formatNumber(pieTotal) }}</strong><small>合计</small></span></button><ul><li v-for="(point, index) in piePoints" :key="point.rawLabel"><i :style="{ '--legend-index': index }"></i><span :title="point.rawLabel">{{ point.label }}</span><b>{{ point.display }}</b></li></ul></div><p v-else class="empty">当前查询没有占比数据</p></section>

      <section class="data-panel chart-panel bar-panel bottom-bar"><header><h2>{{ barWidgets[1]?.componentName || '区域销售分布' }}</h2><span>区域对比</span></header><div v-if="bottomBarPoints.length" class="vertical-bars"><button v-for="point in bottomBarPoints" :key="point.rawLabel" type="button" @click="drill(barWidgets[1], point)"><em>{{ point.display }}</em><span><i :style="{ height: `${Math.max(4, Math.abs(point.value) / bottomBarMax * 100)}%` }"></i></span><small :title="point.rawLabel">{{ point.label }}</small></button></div><p v-else class="empty">当前查询没有区域数据</p></section>
    </div>
  </section>
</template>

<style scoped>
.lynx-template-06-viewport { position: absolute; inset: 0; overflow: hidden; background: #020b13; }
.lynx-template-06-canvas { position: absolute; top: 50%; left: 50%; width: 1920px; height: 1080px; overflow: hidden; color: #ddfbff; background: radial-gradient(circle at 48% 8%, rgba(22, 133, 158, .28), transparent 34%), linear-gradient(115deg, #052431, #073747 49%, #032234); box-shadow: inset 0 0 130px rgba(0, 0, 0, .5); transform-origin: center; isolation: isolate; }
.lynx-template-06-canvas::before { position: absolute; inset: 0; z-index: -1; opacity: .4; background-image: radial-gradient(circle, rgba(36, 211, 210, .38) 1.1px, transparent 1.3px); background-size: 46px 46px; content: ''; mask-image: linear-gradient(90deg, transparent, #000 8%, #000 92%, transparent); }
.lynx-template-06-canvas::after { position: absolute; top: -160px; right: 18%; z-index: -1; width: 230px; height: 1420px; opacity: .13; background: linear-gradient(90deg, transparent, #65e9e8, transparent); content: ''; filter: blur(20px); transform: rotate(13deg); }
.reference-plate { position: absolute; inset: 0; z-index: -2; width: 100%; height: 100%; object-fit: fill; opacity: .012; filter: saturate(1.25) contrast(1.08); mix-blend-mode: screen; }
.ambient-grid { position: absolute; inset: 0; z-index: -1; background: linear-gradient(rgba(28, 196, 201, .035) 1px, transparent 1px), linear-gradient(90deg, rgba(28, 196, 201, .03) 1px, transparent 1px); background-size: 72px 72px; pointer-events: none; }
.sales-title { position: absolute; top: 0; left: 50%; display: flex; width: 920px; height: 92px; align-items: center; justify-content: center; gap: 24px; transform: translateX(-50%); }
.sales-title h1 { max-width: 620px; margin: 0; overflow: hidden; color: #f2feff; font: 700 38px/1 'Microsoft YaHei UI', sans-serif; letter-spacing: .08em; text-align: center; text-overflow: ellipsis; text-shadow: 0 0 9px rgba(68, 225, 229, .65), 0 0 22px rgba(42, 158, 192, .45); white-space: nowrap; }
.sales-title > i { width: 130px; height: 2px; background: linear-gradient(90deg, transparent, #2bd7d4); box-shadow: 0 0 9px rgba(43, 215, 212, .7); }.sales-title > i:last-child { transform: scaleX(-1); }
.sales-clock { position: absolute; top: 24px; right: 33px; display: grid; justify-items: end; }.sales-clock small { color: #72a8b9; font: 8px Consolas, monospace; letter-spacing: .24em; }.sales-clock strong { margin-top: 5px; color: #d9f5fa; font: 12px 'Microsoft YaHei UI', sans-serif; }.sales-clock b { margin-top: 4px; color: #32d8d3; font: 700 19px Bahnschrift, monospace; letter-spacing: .12em; }
.sales-metrics { position: absolute; top: 105px; right: 29px; left: 29px; display: grid; height: 140px; grid-template-columns: 1.25fr repeat(4, 1fr); gap: 22px; }
.sales-metrics button, .data-panel { position: relative; overflow: hidden; color: inherit; background: linear-gradient(135deg, rgba(8, 62, 77, .82), rgba(5, 39, 58, .9)); border: 1px solid rgba(30, 224, 219, .82); box-shadow: inset 0 0 24px rgba(32, 188, 198, .09), 0 0 9px rgba(22, 199, 203, .15); clip-path: polygon(11px 0, calc(100% - 11px) 0, 100% 11px, 100% calc(100% - 11px), calc(100% - 11px) 100%, 11px 100%, 0 calc(100% - 11px), 0 11px); }
.sales-metrics button { display: flex; min-width: 0; padding: 24px 20px 18px; flex-direction: column; align-items: flex-start; text-align: left; cursor: pointer; }.sales-metrics button::before { position: absolute; inset: 5px; border-top: 2px solid rgba(51, 183, 221, .46); border-bottom: 2px solid rgba(51, 183, 221, .22); content: ''; clip-path: polygon(0 0, 28% 0, 28% 2px, 72% 2px, 72% 0, 100% 0, 100% 100%, 68% 100%, 68% calc(100% - 2px), 32% calc(100% - 2px), 32% 100%, 0 100%); pointer-events: none; }.sales-metrics small { order: 2; max-width: 100%; margin-top: 8px; overflow: hidden; color: #b2d3dc; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }.sales-metrics strong { order: 1; color: #2ed8d3; font: 700 30px/1 Bahnschrift, 'Microsoft YaHei UI', sans-serif; letter-spacing: -.015em; text-shadow: 0 0 10px rgba(46, 216, 211, .38); }.sales-metrics em { order: 3; margin-top: 10px; color: #56899b; font: normal 9px Consolas, monospace; }.sales-metrics button > b { position: absolute; top: 16px; right: 15px; color: rgba(66, 219, 218, .28); font: 10px Consolas, monospace; }.sales-metrics button:focus-visible, .business-table button:focus-visible, .vertical-bars button:focus-visible, .hbar-panel li:focus-visible, .pie:focus-visible { outline: 2px solid #68fcf4; outline-offset: -4px; }
.data-panel { position: absolute; }.data-panel::before, .data-panel::after { position: absolute; z-index: 2; width: 62px; height: 2px; background: #25d3d1; box-shadow: 0 0 8px rgba(37, 211, 209, .65); content: ''; pointer-events: none; }.data-panel::before { top: 5px; left: 20px; }.data-panel::after { right: 20px; bottom: 5px; }
.data-panel > header { display: flex; height: 43px; align-items: center; justify-content: space-between; padding: 0 17px 0 19px; border-bottom: 1px solid rgba(46, 173, 188, .18); background: linear-gradient(90deg, rgba(18, 92, 111, .34), transparent); }.data-panel h2 { margin: 0; overflow: hidden; color: #d7f9fc; font-size: 13px; font-weight: 650; text-overflow: ellipsis; white-space: nowrap; }.data-panel header > span { display: inline-flex; align-items: center; gap: 6px; color: #5f9bab; font: 8px Consolas, monospace; letter-spacing: .08em; }.data-panel header > span i { width: 6px; height: 6px; border-radius: 50%; background: #25d8d2; box-shadow: 0 0 8px #25d8d2; }
.table-1 { top: 270px; left: 29px; width: 430px; height: 296px; }.table-2 { top: 590px; left: 29px; width: 430px; height: 462px; }.line-panel { top: 270px; left: 482px; width: 694px; height: 225px; }.top-bar { top: 270px; right: 29px; width: 694px; height: 225px; }.area-panel { top: 519px; left: 482px; width: 694px; height: 225px; }.hbar-panel { top: 519px; right: 29px; width: 694px; height: 225px; }.pie-panel { top: 768px; left: 482px; width: 694px; height: 284px; }.bottom-bar { top: 768px; right: 29px; width: 694px; height: 284px; }
.business-table { position: absolute; inset: 43px 1px 1px; overflow: hidden; }.table-head, .business-table button { display: grid; width: 100%; align-items: center; }.table-head { height: 33px; padding: 0 11px; color: #72e4e0; background: rgba(16, 88, 104, .56); font-size: 9px; text-align: center; }.table-head b { overflow: hidden; padding: 0 5px; text-overflow: ellipsis; white-space: nowrap; }.business-table button { height: 39px; padding: 0 11px; color: #9fc8d3; background: rgba(8, 44, 59, .48); border: 0; font-size: 9px; text-align: center; cursor: pointer; }.business-table button:nth-child(odd) { background: rgba(16, 73, 87, .48); }.business-table button:hover { background: rgba(25, 105, 118, .66); }.business-table button span { overflow: hidden; padding: 0 5px; text-overflow: ellipsis; white-space: nowrap; }
.line-chart { position: absolute; inset: 44px 10px 8px; }.line-chart svg { display: block; width: 100%; height: 141px; overflow: visible; }.chart-grid line { stroke: rgba(101, 173, 184, .34); stroke-width: 1; vector-effect: non-scaling-stroke; }.line-chart .area { fill: url(#t06-line-area); }.line-chart .line { fill: none; stroke: #29d7d1; stroke-width: 2.2; filter: drop-shadow(0 0 5px rgba(41, 215, 209, .5)); vector-effect: non-scaling-stroke; }.line-chart circle { fill: #d8ffff; stroke: #24cfcd; stroke-width: 1.7; cursor: pointer; vector-effect: non-scaling-stroke; }.area-chart .area { fill: url(#t06-area-fill); }.area-chart .line { stroke: #42a7ff; }.area-chart circle { stroke: #42a7ff; }.axis { position: absolute; right: 16px; bottom: 0; left: 22px; display: flex; justify-content: space-between; }.axis span { max-width: 70px; overflow: hidden; color: #5c8798; font: 8px Consolas, monospace; text-overflow: ellipsis; white-space: nowrap; }
.vertical-bars { position: absolute; inset: 53px 15px 10px; display: flex; align-items: flex-end; gap: 9px; }.vertical-bars button { display: grid; min-width: 0; height: 100%; flex: 1; grid-template-rows: 14px 1fr 23px; padding: 0; color: #668e9d; background: transparent; border: 0; cursor: pointer; }.vertical-bars em { overflow: hidden; color: #83bec8; font: normal 8px Consolas, monospace; text-overflow: ellipsis; white-space: nowrap; }.vertical-bars button > span { display: flex; align-items: flex-end; justify-content: center; border-bottom: 1px solid rgba(104, 177, 188, .36); background: repeating-linear-gradient(to top, rgba(83, 145, 158, .19) 0 1px, transparent 1px 27px); }.vertical-bars i { display: block; width: min(42px, 70%); min-height: 4px; background: linear-gradient(180deg, #27d5d0, #1698b0); box-shadow: 0 0 9px rgba(39, 213, 208, .26); }.vertical-bars small { margin-top: 6px; overflow: hidden; font-size: 8px; text-overflow: ellipsis; white-space: nowrap; }
.hbar-panel ol { display: grid; gap: 7px; margin: 0; padding: 11px 18px; list-style: none; }.hbar-panel li { display: grid; min-height: 19px; grid-template-columns: 108px 1fr 60px; align-items: center; gap: 10px; color: #8fb8c2; font-size: 9px; cursor: pointer; }.hbar-panel li > span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.hbar-panel li > b { display: block; height: 9px; overflow: hidden; background: rgba(29, 82, 97, .7); }.hbar-panel li i { display: block; height: 100%; background: linear-gradient(90deg, #168fa9, #2bd9d2); box-shadow: 0 0 7px rgba(43, 217, 210, .25); }.hbar-panel li em { color: #bfe7ea; font: normal 9px Consolas, monospace; text-align: right; }
.pie-layout { display: grid; height: calc(100% - 43px); grid-template-columns: 47% 53%; align-items: center; padding: 10px 30px; }.pie { position: relative; display: grid; width: 180px; height: 180px; place-items: center; margin: auto; border: 0; border-radius: 50%; box-shadow: 0 0 24px rgba(37, 200, 203, .18); cursor: pointer; }.pie::before { position: absolute; inset: 39px; border-radius: 50%; background: #073142; box-shadow: inset 0 0 18px rgba(30, 160, 171, .22); content: ''; }.pie > span { position: relative; z-index: 1; display: grid; justify-items: center; }.pie strong { color: #d9ffff; font: 700 21px Bahnschrift, sans-serif; }.pie small { color: #6d9aa8; font-size: 8px; }.pie-layout ul { display: grid; gap: 11px; margin: 0; padding: 0; list-style: none; }.pie-layout li { display: grid; grid-template-columns: 8px 1fr auto; align-items: center; gap: 8px; color: #89b3be; font-size: 9px; }.pie-layout li i { width: 7px; height: 7px; border-radius: 50%; background: hsl(calc(180deg + var(--legend-index) * 45deg) 75% 58%); }.pie-layout li span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.pie-layout li b { color: #c8eef1; font: 600 9px Consolas, monospace; }
.bottom-bar .vertical-bars { top: 58px; }.empty { display: grid; height: calc(100% - 43px); margin: 0; place-items: center; color: #568594; font-size: 10px; }
@media (prefers-reduced-motion: reduce) { * { scroll-behavior: auto !important; } }
</style>
