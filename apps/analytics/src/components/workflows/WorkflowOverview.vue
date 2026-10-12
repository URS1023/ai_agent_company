<script setup lang="ts">
/**
 * WorkflowOverview —— 工作流「监测」页，路由 `/workflows/:id/overview`。
 *
 * 职责：按今天/7 天/30 天统计真实运行记录（调用量、活跃用户、模型 Tokens 与费用、平均耗时），
 * 用内联 SVG 折线 + 面积图呈现趋势，不引入图表库。
 * 依赖接口：`api/workflows.workflows.monitor(id, days, signal)` → WorkflowMonitor / WorkflowMonitorPoint。
 * 关键约束：
 * - 请求带 AbortController：切换统计区间或组件卸载时中止上一次请求，并用 `controller === active` 判定
 *   只接受最新一次响应，避免旧响应覆盖新数据；
 * - 费用与 Tokens 由后端计价，未计价 Tokens 必须显式提示，不能当作 0 费用展示；
 * - 单点数据（series.length === 1）用圆点而非折线，避免出现"两点连线"的假趋势。
 */
import { computed, onBeforeUnmount, shallowRef, watch } from 'vue'
import { workflows, type Workflow, type WorkflowMonitor, type WorkflowMonitorPoint } from '../../api/workflows'

const props = defineProps<{ flow: Workflow }>()
const days = shallowRef(7)
const data = shallowRef<WorkflowMonitor | null>(null)
const loading = shallowRef(false)
const error = shallowRef('')
let controller: AbortController | null = null

type MetricKey = 'runs' | 'activeUsers' | 'cost' | 'averageRunsPerUser'
const cards = computed(() => {
  const totals = data.value?.totals
  return [
    { key: 'runs' as MetricKey, title: '全部消息数', value: formatNumber(totals?.runs || 0), note: `${totals?.completed || 0} 次完成 · ${totals?.failed || 0} 次失败`, color: '#155eef' },
    { key: 'activeUsers' as MetricKey, title: '活跃用户数', value: formatNumber(totals?.activeUsers || 0), note: '按实际运行发起人去重', color: '#7f56d9' },
    { key: 'cost' as MetricKey, title: '费用消耗', value: `¥${(totals?.cost || 0).toFixed(4)}`, note: totals?.unpricedTokens ? `${formatNumber(totals.unpricedTokens)} Tokens 尚未计价` : `${formatNumber(totals?.tokens || 0)} Tokens`, color: '#039855' },
    { key: 'averageRunsPerUser' as MetricKey, title: '平均用户调用次数', value: (totals?.averageRunsPerUser || 0).toFixed(2), note: `平均耗时 ${formatDuration(totals?.averageDurationMs || 0)}`, color: '#dc6803' },
  ]
})

function formatNumber(value: number) { return new Intl.NumberFormat('zh-CN').format(value) }
function formatDuration(value: number) {
  if (!value) return '0 ms'
  return value < 1000 ? `${value} ms` : `${(value / 1000).toFixed(2)} s`
}
/**
 * 把一条统计点换算成折线纵坐标：平均调用次数需由 runs/activeUsers 现算（后端不下发该字段），
 * activeUsers 为 0 时返回 0，避免除零产生 Infinity 破坏 SVG 坐标。
 */
function pointValue(point: WorkflowMonitorPoint, key: MetricKey) {
  if (key === 'averageRunsPerUser') return point.activeUsers ? point.runs / point.activeUsers : 0
  return point[key]
}
/** 生成 600x160 视口内的折线点串；最大值下限取 1，全 0 序列时贴底而不是除以 0。 */
function chartPoints(key: MetricKey) {
  const values = (data.value?.series || []).map(item => pointValue(item, key))
  const max = Math.max(1, ...values)
  const count = Math.max(2, values.length)
  return values.map((value, index) => `${values.length === 1 ? 300 : (index / (count - 1)) * 600},${150 - (value / max) * 122}`).join(' ')
}
function areaPoints(key: MetricKey) {
  const line = chartPoints(key)
  return line ? `0,150 ${line} 600,150` : ''
}
function shortDate(value: string) { return value.slice(5).replace('-', '/') }
async function load() {
  if (!props.flow.id) return
  controller?.abort()
  const active = new AbortController()
  controller = active
  loading.value = true
  error.value = ''
  try {
    const response = await workflows.monitor(props.flow.id, days.value, active.signal)
    if (!active.signal.aborted && controller === active) data.value = response.data
  } catch (cause) {
    if (!active.signal.aborted && controller === active) error.value = (cause as Error).message
  } finally {
    if (!active.signal.aborted && controller === active) loading.value = false
  }
}

watch([() => props.flow.id, days], () => void load(), { immediate: true })
onBeforeUnmount(() => controller?.abort())
</script>

<template>
  <main class="wf-workspace-surface wf-overview-page" aria-labelledby="wf-overview-title">
    <header class="wf-workspace-heading wf-overview-heading">
      <div><h1 id="wf-overview-title">监测</h1><p>根据真实工作流运行记录分析调用量、用户、模型用量和耗时。</p></div>
      <div class="wf-period-switch" aria-label="统计时间范围">
        <button v-for="item in [{ value: 1, label: '今天' }, { value: 7, label: '最近 7 天' }, { value: 30, label: '最近 30 天' }]" :key="item.value" type="button" :class="{ active: days === item.value }" @click="days = item.value">{{ item.label }}</button>
        <button type="button" :disabled="loading" aria-label="刷新监测" @click="load">{{ loading ? '刷新中…' : '刷新' }}</button>
      </div>
    </header>
    <p v-if="error" class="wf-error" role="alert">{{ error }} <button type="button" @click="load">重试</button></p>
    <div v-if="loading && !data" class="wf-workspace-loading" role="status"><span /><strong>正在统计运行数据</strong></div>
    <section v-else-if="data && !error" class="wf-metric-grid" :aria-busy="loading">
      <article v-for="card in cards" :key="card.key" class="wf-metric-card">
        <header><h2>{{ card.title }}</h2><i class="fa-solid fa-circle-info" title="统计来自当前有权查看的持久化运行记录" aria-hidden="true" /></header>
        <strong>{{ card.value }}</strong>
        <p>{{ card.note }}</p>
        <svg viewBox="0 0 600 160" preserveAspectRatio="none" role="img" :aria-label="`${card.title}趋势`">
          <defs><linearGradient :id="`metric-fill-${card.key}`" x1="0" y1="0" x2="0" y2="1"><stop offset="0" :stop-color="card.color" stop-opacity=".2" /><stop offset="1" :stop-color="card.color" stop-opacity="0" /></linearGradient></defs>
          <line v-for="y in [28, 69, 110, 150]" :key="y" x1="0" :y1="y" x2="600" :y2="y" />
          <polygon v-if="data && data.series.length > 1" :points="areaPoints(card.key)" :fill="`url(#metric-fill-${card.key})`" />
          <polyline v-if="data && data.series.length > 1" :points="chartPoints(card.key)" :stroke="card.color" />
          <circle v-if="data?.series.length === 1" cx="300" :cy="150 - pointValue(data.series[0]!, card.key) / Math.max(1, pointValue(data.series[0]!, card.key)) * 122" r="4" :fill="card.color"><title>{{ data.series[0]!.date }}：{{ pointValue(data.series[0]!, card.key) }}</title></circle>
        </svg>
        <footer v-if="data?.series.length" :style="data.series.length === 1 ? { justifyContent: 'center' } : undefined"><span>{{ shortDate(data.series[0]!.date) }}</span><span v-if="data.series.length > 1">{{ shortDate(data.series[data.series.length - 1]!.date) }}</span></footer>
      </article>
    </section>
    <section v-if="data && !error" class="wf-monitor-summary">
      <div><span>执行完成率</span><strong>{{ data.totals.runs ? ((data.totals.completed / data.totals.runs) * 100).toFixed(1) : '0.0' }}%</strong></div>
      <div><span>模型 Tokens</span><strong>{{ formatNumber(data.totals.tokens) }}</strong></div>
      <div><span>平均运行耗时</span><strong>{{ formatDuration(data.totals.averageDurationMs) }}</strong></div>
      <div><span>统计区间 · {{ data.timeZone }}</span><strong>{{ data.start }} — {{ data.end }}</strong></div>
    </section>
  </main>
</template>
