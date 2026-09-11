<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import JimuChart from './JimuChart.vue'
import { post } from '@/api/request'
import {
  normalizeDashboardTheme,
  shouldShowPanelChrome,
  shouldShowTechFrame
} from '@/utils/dashboardTheme'
import {
  cleanDisplayValue,
  deriveTableHeaders,
  sanitizeChartOption
} from '@/utils/dashboardData'

const props = defineProps({
  widget: { type: Object, required: true },
  interactive: { type: Boolean, default: false }
})
const emit = defineEmits(['drilldown'])

const now = ref(new Date())
const remoteData = ref(null)
const activeChoice = ref(0)
let timer = null

const componentType = computed(() => props.widget.component || '')
const option = computed(() => sanitizeChartOption(props.widget.config?.option || {}))
const data = computed(() => remoteData.value ?? parseData(props.widget.config?.chartDataParsed ?? props.widget.config?.chartData))
const borderType = computed(() => String(option.value.type || '7'))
const decorationStyle = computed(() => ({
  '--main-color': option.value.mainColor || '#05f8d6',
  '--sub-color': option.value.subColor || '#0082fc'
}))
const isChart = computed(() => /Bar|Line|Area|Ring|Pie|Rose|Radar|Gauge|Funnel|Pyramid|Scatter|Quadrant|Bubble|Histogram|Capsule|Chart|Map|Progress|Liquid|Pictorial|Rectangle|Earth|Calendar|Orbit|WordCloud|Echart/i.test(componentType.value))
const isTable = computed(() => /Table|ScrollBoard|RankingBoard/i.test(componentType.value))
const theme = computed(() => normalizeDashboardTheme(props.widget))
const isThemed = computed(() => Boolean(props.widget.theme || props.widget.themeId || props.widget.panelVariant))
const isAiVisual = computed(() => props.widget.visualMode === 'ai')
const isShowcaseReplica = computed(() => String(props.widget.panelVariant || '').startsWith('showcase-template-'))
const isMetric = computed(() => componentType.value === 'JStatsSummary')
const showPanelChrome = computed(() => (
  (isChart.value || isTable.value)
  && (!isShowcaseReplica.value || isAiVisual.value)
  && shouldShowPanelChrome(props.widget)
))
const showTechFrame = computed(() => (
  (isChart.value || isTable.value || isMetric.value)
  && (!isShowcaseReplica.value || isAiVisual.value)
  && shouldShowTechFrame(props.widget)
))
const showTableTitle = computed(() => isAiVisual.value
  ? option.value.title?.show === true
  : isThemed.value)
const showEmptyState = computed(() => (isChart.value || isTable.value) && Array.isArray(data.value) && data.value.length === 0)
const visualRoleLabel = computed(() => ({
  'primary-insight': '核心洞察', 'supporting-trend': '趋势追踪', 'business-ranking': '贡献排行',
  'composition-insight': '结构透视', 'status-monitor': '履约状态', 'relation-analysis': '关联研判',
  'exception-signal': '异常信号', 'detail-stream': '实时明细', 'hero-metric': '关键指标',
  'support-metric': '经营指标', 'progress-metric': '目标进度', 'alert-metric': '风险指标'
})[props.widget.visualRole] || '智能洞察')
const chromeStatusLabel = computed(() => props.widget.chromeMode === 'signal' ? 'ATTENTION' : 'PRIME ANALYTIC')

const currentTime = computed(() => {
  const date = now.value
  const pad = value => String(value).padStart(2, '0')
  const weekdays = ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六']
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}  ${weekdays[date.getDay()]}`
})

const textStyle = computed(() => {
  const body = option.value.body || {}
  return {
    color: body.color ?? '#d0e0f0',
    fontSize: `${body.fontSize ?? Math.min(40, Math.max(14, props.widget.h * 0.55))}px`,
    fontFamily: body.fontFamily,
    fontWeight: body.fontWeight ?? 'bold',
    letterSpacing: `${body.letterSpacing ?? 0}px`,
    lineHeight: body.lineHeight,
    textShadow: body.textShadow,
    textAlign: body.textAlign ?? 'center'
  }
})

const stats = computed(() => {
  if (!Array.isArray(data.value)) return []
  return data.value.map((item, index) => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) {
      return { name: props.widget.componentName, value: item, suffix: '' }
    }
    if ('value' in item) return item
    const entries = Object.entries(item)
    const valueEntry = entries.find(([, value]) => typeof value === 'number')
      || entries.find(([, value]) => value != null)
    return {
      ...item,
      id: item.id || `${props.widget.id}-${index}`,
      name: item.name || props.widget.componentName,
      value: valueEntry?.[1] ?? '-',
      suffix: item.suffix || item.unit || props.widget.unit || ''
    }
  })
})
const choices = computed(() => Array.isArray(data.value) ? data.value : [])
const replicaProgress = computed(() => {
  const first = Array.isArray(data.value) ? data.value[0] : data.value
  const raw = typeof first === 'object' && first !== null ? first.value : first
  const value = Number(raw ?? option.value.progress ?? 0)
  return Math.max(0, Math.min(100, Number.isFinite(value) ? value : 0))
})
const replicaProgressStyle = computed(() => ({
  '--replica-progress': `${replicaProgress.value}%`,
  '--replica-ring': theme.value.primary,
  '--replica-ring-secondary': theme.value.secondary,
  '--replica-track': theme.value.grid
}))
const statsLayoutStyle = computed(() => {
  const layout = option.value.layout || {}
  const padding = layout.padding || {}
  const requestedColumns = Number(layout.columns)
  const columns = Number.isFinite(requestedColumns) && requestedColumns > 0
    ? `repeat(${Math.round(requestedColumns)}, minmax(0, 1fr))`
    : (typeof layout.columns === 'string' && layout.columns.trim()
        ? layout.columns
        : (isAiVisual.value
            ? `repeat(${Math.max(1, stats.value.length)}, minmax(0, 1fr))`
            : 'repeat(auto-fit, minmax(min(100%, 180px), 1fr))'))
  return {
    '--stats-columns': columns,
    '--stats-gap': `${layout.gap ?? (isAiVisual.value ? 0 : 16)}px`,
    '--stats-padding': `${padding.top ?? (isAiVisual.value ? 0 : 16)}px ${padding.right ?? (isAiVisual.value ? 0 : 16)}px ${padding.bottom ?? 0}px ${padding.left ?? (isAiVisual.value ? 0 : 16)}px`,
    '--stats-align': layout.alignItems ?? 'stretch',
    '--stats-justify': layout.justifyItems ?? 'stretch'
  }
})
const metricCard = computed(() => option.value.card || {})
const showMetricIcon = computed(() => metricCard.value.showIcon ?? !isAiVisual.value)
const showMetricKicker = computed(() => metricCard.value.showKicker ?? !isAiVisual.value)
const showMetricSync = computed(() => metricCard.value.showSync ?? !isAiVisual.value)
const showMetricSignal = computed(() => metricCard.value.showSignal ?? !isAiVisual.value)
const showMetricDecorations = computed(() => metricCard.value.showDecorations ?? !isAiVisual.value)
const statCardStyle = computed(() => {
  const card = metricCard.value
  const fill = card.fill || {}
  const padding = card.padding || {}
  return {
    '--stat-card-bg': fill.color ?? (isAiVisual.value ? 'transparent' : 'linear-gradient(135deg, color-mix(in srgb, var(--widget-primary) 13%, transparent), rgba(3, 12, 24, .15))'),
    '--stat-card-border': card.borderColor ?? (isAiVisual.value ? 'transparent' : 'color-mix(in srgb, var(--widget-primary) 42%, transparent)'),
    '--stat-card-border-width': `${card.borderWidth ?? (isAiVisual.value ? 0 : 1)}px`,
    '--stat-card-radius': `${card.borderRadius ?? (isAiVisual.value ? 0 : 3)}px`,
    '--stat-card-shadow': card.shadow ?? (isAiVisual.value ? 'none' : 'inset 0 1px 0 color-mix(in srgb, var(--widget-primary) 28%, transparent), 0 0 24px color-mix(in srgb, var(--widget-glow) 12%, transparent)'),
    '--stat-card-padding': `${padding.vertical ?? (isAiVisual.value ? 0 : 14)}px ${padding.horizontal ?? (isAiVisual.value ? 0 : 18)}px`,
    '--stat-card-direction': card.direction ?? 'column',
    '--stat-card-align': card.alignItems ?? 'flex-start',
    '--stat-card-justify': card.justifyContent ?? 'center',
    '--stat-card-min-height': `${card.minHeight ?? (isAiVisual.value ? 0 : 72)}px`,
    '--stat-card-overflow': card.overflow ?? 'hidden',
    '--stat-main-gap': `${card.mainGap ?? (isAiVisual.value ? 0 : 16)}px`,
    '--stat-main-margin-top': `${card.mainMarginTop ?? (showMetricKicker.value ? 14 : 0)}px`
  }
})
const tableRows = computed(() => Array.isArray(data.value) ? data.value : [])
const tableHeaders = computed(() => deriveTableHeaders(
  tableRows.value,
  option.value.header,
  option.value.dataset?.dimensions
))
const repeatedRows = computed(() => tableRows.value.length ? [...tableRows.value, ...tableRows.value.slice(0, 5)] : [])
const visibleRows = computed(() => Number(option.value.rowNum || 4))
const scrollRowHeight = computed(() => {
  const headerHeight = Number(option.value.headerHeight || 38)
  return Math.max(24, Math.floor((props.widget.h - headerHeight) / Math.max(1, visibleRows.value)))
})
const scrollDuration = computed(() => `${Math.max(8, tableRows.value.length * Number(option.value.waitTime || 2000) / 1000)}s`)
const scrollColumns = computed(() => {
  const widths = tableHeaders.value.map(header => Number(header.width || 0))
  if (widths.some(Boolean)) return widths.map(width => `${Math.max(width, 40)}fr`).join(' ')
  return `repeat(${Math.max(1, tableHeaders.value.length)}, 1fr)`
})

const borderGeometry = computed(() => {
  const width = Math.max(30, props.widget.w)
  const height = Math.max(30, props.widget.h)
  return {
    viewBox: `0 0 ${width} ${height}`,
    width,
    height,
    fullOuter: `8,5 ${width - 5},5 ${width - 5},${height - 100} ${width - 100},${height - 5} 8,${height - 5} 8,5`,
    fullInner: `3,0 ${width},0 ${width},${height - 78} ${width - 74},${height - 5} 3,${height - 5} 3,5`,
    fullTop: `50,13 ${width - 35},13`,
    fullTop2: `15,20 ${width - 35},20`,
    fullBottom: `15,${height - 20} ${width - 110},${height - 20}`,
    fullBottom2: `15,${height - 13} ${width - 110},${height - 13}`
  }
})

function formatValue(value) {
  if (typeof value !== 'number') return value
  return value.toLocaleString('zh-CN', { maximumFractionDigits: 1 })
}

function statValueStyle(value, index) {
  const length = String(value ?? '').length
  const compactTile = props.widget.style?.density === 'compact' && Number(props.widget.w || 0) < 180
  const automaticSize = compactTile
    ? (length > 10 ? 18 : length > 7 ? 21 : 24)
    : (length > 13 ? 23 : length > 10 ? 27 : length > 7 ? 31 : (String(props.widget.panelVariant || '').includes('metric-spotlight') ? 43 : 35))
  const requestedSize = Number(metricCard.value.valueFontSize ?? props.widget.style?.valueFontSize)
  const size = Number.isFinite(requestedSize) ? requestedSize : automaticSize
  return {
    color: props.widget.style?.valueColor ?? theme.value.palette[index % theme.value.palette.length],
    fontSize: `${size}px`,
    fontFamily: metricCard.value.valueFontFamily ?? props.widget.style?.fontFamily,
    fontWeight: metricCard.value.valueFontWeight ?? props.widget.style?.valueFontWeight,
    letterSpacing: metricCard.value.valueLetterSpacing == null ? undefined : `${metricCard.value.valueLetterSpacing}px`
  }
}

function metricIcon(name = '') {
  const value = String(name)
  if (/金额|收入|营收|销售额|回款|应收|利润|成本|售价/.test(value)) return 'fa-solid fa-yen-sign'
  if (/订单|单数|出库单/.test(value)) return 'fa-solid fa-receipt'
  if (/出库|发货|配送|物流/.test(value)) return 'fa-solid fa-truck-fast'
  if (/产品|商品|物料|SKU/.test(value)) return 'fa-solid fa-boxes-stacked'
  if (/客户|用户|会员/.test(value)) return 'fa-solid fa-users'
  if (/确认|完成率|履约率|达成率/.test(value)) return 'fa-solid fa-circle-check'
  if (/准时|周期|时长|时间/.test(value)) return 'fa-solid fa-clock'
  if (/库存|仓库|库容/.test(value)) return 'fa-solid fa-warehouse'
  if (/异常|风险|逾期|未确认|告警/.test(value)) return 'fa-solid fa-triangle-exclamation'
  if (/增长|趋势|同比|环比/.test(value)) return 'fa-solid fa-arrow-trend-up'
  return 'fa-solid fa-wave-square'
}

function parseData(value) {
  if (Array.isArray(value)) return value
  if (value && typeof value === 'object') return value
  if (typeof value === 'string' && value.trim()) {
    try {
      return JSON.parse(value)
    } catch {
      return []
    }
  }
  return []
}

function rowCells(row) {
  if (Array.isArray(row)) return row.map(cleanDisplayValue)
  return tableHeaders.value.map(header => cleanDisplayValue(row?.[header.key] ?? row?.[header.label] ?? ''))
}

function emitDrilldown(point = {}) {
  if (!props.interactive) return
  emit('drilldown', {
    ...point,
    fallbackRows: data.value
  })
}

onMounted(() => {
  if (componentType.value === 'JCurrentTime') timer = setInterval(() => { now.value = new Date() }, 1000)
  loadRemoteData()
})

onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
})

function assetUrl(url) {
  if (!url?.startsWith('/jimu-screen/')) return url
  return `${import.meta.env.BASE_URL}${url.slice(1)}`
}

async function loadRemoteData() {
  const config = props.widget.config || {}
  const type = Number(config.dataType)
  if (type === 3 && config.datasourceId && config.sql) {
    try {
      const result = await post('/api/data-analysis/execute-sql', null, {
        params: { datasourceId: config.datasourceId, sql: config.sql }
      })
      const rows = result?.data?.data ?? result?.data?.rows ?? result?.data
      if (Array.isArray(rows)) remoteData.value = rows
    } catch {
      // Keep configured static data visible when the data source cannot be queried.
    }
    return
  }
  if (type !== 2 || !config.url) return
  try {
    const response = await fetch(config.url, { method: config.method || 'GET' })
    if (!response.ok) return
    let result = await response.json()
    for (const key of String(config.dataPath || 'data').split('.').filter(Boolean)) result = result?.[key]
    if (Array.isArray(result)) remoteData.value = result
  } catch {
    // Keep configured static data visible when an external API is unavailable.
  }
}
</script>

<template>
  <div
    class="widget-renderer"
    :class="[
      `component-${componentType}`,
      `variant-${widget.panelVariant || 'legacy'}`,
      `density-${widget.style?.density || 'comfortable'}`,
      `frame-${widget.frameMode || 'none'}`,
      `chrome-${widget.chromeMode || 'none'}`,
      { interactive: interactive, themed: isThemed && !isAiVisual, 'ai-visual': isAiVisual }
    ]"
  >
    <div v-if="showTechFrame" class="tech-frame" aria-hidden="true">
      <i class="corner-tl"></i><i class="corner-tr"></i>
      <i class="corner-bl"></i><i class="corner-br"></i>
      <span></span>
    </div>
    <div v-if="showPanelChrome" class="panel-chrome" aria-hidden="true">
      <small><i></i>{{ visualRoleLabel }}</small>
      <b><i></i>{{ chromeStatusLabel }}</b>
      <span class="chrome-spectrum"></span>
    </div>

    <template v-if="componentType === 'JAmbientGlow'">
      <div class="ambient-glow" :style="decorationStyle"><i></i><span></span></div>
    </template>

    <template v-else-if="componentType === 'JDataConstellation'">
      <div class="data-constellation" :style="decorationStyle">
        <i
          v-for="index in 18"
          :key="index"
          :style="{
            left: `${(index * 47) % 97}%`,
            top: `${(index * 71) % 89}%`,
            width: `${1 + (index % 3)}px`,
            height: `${1 + (index % 3)}px`,
            opacity: .13 + (index % 5) * .04,
            animationDelay: `${-(index % 6)}s`
          }"
        ></i>
      </div>
    </template>

    <template v-else-if="componentType === 'JFlowRibbon'">
      <div class="flow-ribbon" :style="decorationStyle">
        <svg viewBox="0 0 1920 620" preserveAspectRatio="none" aria-hidden="true">
          <path class="ribbon-halo" d="M-90 430 C260 90 560 530 930 250 S1540 72 2020 318" />
          <path class="ribbon-main" d="M-90 430 C260 90 560 530 930 250 S1540 72 2020 318" />
          <path class="ribbon-echo" d="M-80 500 C310 180 590 585 980 330 S1570 150 2010 390" />
          <path class="ribbon-pulse" d="M-90 430 C260 90 560 530 930 250 S1540 72 2020 318" />
        </svg>
        <span
          v-for="index in 9"
          :key="index"
          :style="{ left: `${8 + index * 10.7}%`, top: `${58 - Math.sin(index * 1.4) * 28}%`, animationDelay: `${-index * .7}s` }"
        ></span>
      </div>
    </template>

    <template v-else-if="componentType === 'JSignalRail'">
      <div class="signal-rail" :style="decorationStyle"><i></i><span></span><b></b></div>
    </template>

    <template v-else-if="componentType === 'JFocusOrb'">
      <div class="focus-orb" :style="decorationStyle"><i></i><span></span><b></b></div>
    </template>

    <template v-else-if="componentType === 'JReplicaWeatherStrip'">
      <div class="replica-weather-strip">
        <div v-for="(item, index) in choices.slice(0, 3)" :key="item.day || index">
          <i :class="`fa-solid fa-${item.icon || 'cloud-sun'}`"></i>
          <span><b>{{ item.day }}</b><small>{{ item.weather }}</small></span>
          <em>{{ item.temp }}</em>
        </div>
      </div>
    </template>

    <template v-else-if="componentType === 'JReplicaMapOperations'">
      <div class="replica-map-operations">
        <div v-for="(item, index) in choices.slice(0, 3)" :key="item.name || index" :class="`tone-${item.tone || 'teal'}`">
          <span class="operation-beacon"><i></i><b></b></span>
          <strong>{{ item.name }}</strong>
        </div>
      </div>
    </template>

    <template v-else-if="componentType === 'JReplicaRankingList'">
      <ol class="replica-ranking-list">
        <li v-for="(item, index) in choices.slice(0, 5)" :key="item.name || index">
          <b>{{ item.rank || index + 1 }}</b><span>{{ item.name }}</span><i></i><em>{{ formatValue(item.value) }}</em>
        </li>
      </ol>
    </template>

    <div v-else-if="showEmptyState" class="data-empty-state">
      <div class="empty-orbit"><i></i><span></span></div>
      <strong>当前范围暂无有效数据</strong>
      <p>已保留查询配置，调整筛选范围后自动刷新</p>
      <small><i class="fa-solid fa-database"></i>{{ widget.config?.tableName || 'REAL DATA SOURCE' }}</small>
    </div>

    <template v-else-if="componentType === 'JText'">
      <div v-if="String(widget.panelVariant || '').includes('command-title')" class="command-heading">
        <small>INTELLIGENCE · COMMAND VIEW</small>
        <strong :style="{ color: textStyle.color }">{{ option.body?.text || widget.componentName }}</strong>
        <span><i></i><b></b></span>
      </div>
      <div v-else class="jimu-text" :style="textStyle">{{ option.body?.text || widget.componentName }}</div>
    </template>

    <template v-else-if="componentType === 'JCurrentTime'">
      <div class="jimu-time" :style="{ color: option.body?.color || '#fff' }">
        <small>LOCAL · LIVE</small>
        <span>{{ currentTime }}</span>
      </div>
    </template>

    <template v-else-if="componentType === 'JSystemStatus'">
      <div class="system-status">
        <span class="status-orbit"><i></i></span>
        <div><small>{{ widget.title || '数据链路' }}</small><strong>{{ widget.content || '实时同步' }}</strong></div>
        <b>ONLINE</b>
      </div>
    </template>

    <template v-else-if="/SelectRadio|TabToggle|RadioButton/.test(componentType)">
      <div class="choice-tabs">
        <button
          v-for="(choice, index) in choices"
          :key="choice.value || choice.label || index"
          :class="{ active: activeChoice === index }"
          :style="activeChoice === index ? { background: option.activeColor || '#1688df' } : { color: option.color || '#c9d8e6' }"
          @pointerdown.stop
          @click.stop="activeChoice = index"
        >{{ choice.label || choice.name || choice }}</button>
      </div>
    </template>

    <template v-else-if="componentType === 'JDragEditor'">
      <div class="rich-text" :style="textStyle" v-html="option.body?.text || widget.componentName"></div>
    </template>

    <template v-else-if="/VideoPlay|VideoJs/.test(componentType)">
      <video v-if="widget.config?.videoUrl" class="media-video" :src="assetUrl(widget.config.videoUrl)" :autoplay="option.autoplay" :controls="option.controls !== false"></video>
      <div v-else class="media-placeholder"><i class="fa-solid fa-play"></i><span>视频地址未配置</span></div>
    </template>

    <template v-else-if="/Iframe/.test(componentType)">
      <iframe v-if="widget.config?.url" class="embed-frame" :src="widget.config.url" :sandbox="option.sandbox === false ? undefined : 'allow-scripts allow-same-origin allow-forms'" :title="widget.componentName"></iframe>
      <div v-else class="media-placeholder"><i class="fa-solid fa-window-maximize"></i><span>内嵌页面地址未配置</span></div>
    </template>

    <template v-else-if="/JCountTo|JNumber/.test(componentType)">
      <div class="jimu-number" :style="textStyle">88,600</div>
    </template>

    <template v-else-if="componentType === 'JColorBlock'">
      <div class="jimu-color-block"></div>
    </template>

    <template v-else-if="componentType === 'JStatsSummary'">
      <div class="stats-summary" :style="statsLayoutStyle" @click="emitDrilldown()">
        <div
          v-for="item in stats"
          :key="item.id || item.name"
          class="stat-card"
          :class="{ 'with-decoration': showMetricDecorations }"
          :style="statCardStyle"
          @click.stop="emitDrilldown({ name: item.name, value: item.value, data: item })"
        >
          <div v-if="showMetricKicker" class="stat-kicker"><i></i><span>{{ visualRoleLabel }}</span><b>{{ String((widget.metricIndex || 0) + 1).padStart(2, '0') }}</b></div>
          <div class="stat-main">
            <span v-if="showMetricIcon" class="stat-icon"><i :class="metricIcon(item.name)"></i></span>
            <div class="stat-reading">
              <div class="stat-value" :style="statValueStyle(item.value, stats.indexOf(item))">{{ formatValue(item.value) }}<small>{{ item.suffix }}</small></div>
              <div v-if="item.compareValue" class="stat-compare"><span>{{ item.compareLabel || '较上期' }}</span><b>{{ item.compareValue }}</b></div>
              <div v-else-if="showMetricSync" class="stat-sync"><i></i><span>实时数据已同步</span></div>
              <div class="stat-label">{{ item.name }}</div>
            </div>
          </div>
          <div v-if="showMetricSignal" class="stat-signal" aria-hidden="true"><i></i></div>
        </div>
      </div>
    </template>

    <template v-else-if="componentType === 'JDragBorder'">
      <svg v-if="borderType === '5'" class="jimu-border border-5" :style="decorationStyle" :viewBox="borderGeometry.viewBox" preserveAspectRatio="none">
        <polyline class="b5-line-1" :points="borderGeometry.fullOuter" />
        <polyline class="b5-line-2" :points="borderGeometry.fullInner" />
        <polyline class="b5-line-3" :points="borderGeometry.fullTop" />
        <polyline class="b5-line-4" :points="borderGeometry.fullTop2" />
        <polyline class="b5-line-5" :points="borderGeometry.fullBottom" />
        <polyline class="b5-line-6" :points="borderGeometry.fullBottom2" />
      </svg>
      <svg v-else-if="borderType === '12'" class="jimu-border border-12" :style="decorationStyle" :viewBox="borderGeometry.viewBox" preserveAspectRatio="none">
        <rect x="2" y="2" :width="borderGeometry.width - 4" :height="borderGeometry.height - 4" rx="12" />
        <rect class="runner" x="9" y="9" :width="borderGeometry.width - 18" :height="borderGeometry.height - 18" rx="8" />
      </svg>
      <svg v-else class="jimu-border border-generic" :class="`border-${borderType}`" :style="decorationStyle" :viewBox="borderGeometry.viewBox" preserveAspectRatio="none">
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
          <polyline :points="`${borderGeometry.width - 32},3 ${borderGeometry.width - 3},3 ${borderGeometry.width - 3},32`" />
          <polyline :points="`${borderGeometry.width - 32},${borderGeometry.height - 3} ${borderGeometry.width - 3},${borderGeometry.height - 3} ${borderGeometry.width - 3},${borderGeometry.height - 32}`" />
          <polyline :points="`3,${borderGeometry.height - 32} 3,${borderGeometry.height - 3} 32,${borderGeometry.height - 3}`" />
        </template>
        <template v-if="borderType === '6'">
          <polyline :points="`3,70 3,3 95,3`" />
          <polyline :points="`${borderGeometry.width - 95},${borderGeometry.height - 3} ${borderGeometry.width - 3},${borderGeometry.height - 3} ${borderGeometry.width - 3},${borderGeometry.height - 70}`" />
          <line x1="18" y1="13" :x2="borderGeometry.width - 45" y2="13" />
        </template>
        <path v-if="borderType === '11'" class="border-tab" :d="`M${borderGeometry.width * .35},10 L${borderGeometry.width * .41},2 H${borderGeometry.width * .59} L${borderGeometry.width * .65},10`" />
      </svg>
    </template>

    <template v-else-if="componentType === 'JDragDecoration'">
      <div class="jimu-decoration" :class="`decoration-${borderType}`" :style="decorationStyle"><span></span><i></i><span></span></div>
    </template>

    <template v-else-if="componentType === 'JScrollBoard' || isTable">
      <div v-if="showTableTitle" class="table-panel-title">{{ widget.componentName }}</div>
      <div
        class="scroll-board"
        :class="{ 'pause-on-hover': option.hoverPause !== false }"
        :style="{
          color: option.fontColor || '#c0d4ee',
          '--row-height': `${scrollRowHeight}px`,
          '--scroll-duration': scrollDuration,
          '--scroll-columns': scrollColumns,
          '--header-height': option.headShow === false ? '0px' : `${option.headerHeight || 38}px`
        }"
        @click="emitDrilldown()"
      >
        <div
          v-if="option.headShow !== false"
          class="scroll-head"
          :style="{ background: option.headerBGC || '#1e3048', height: `${option.headerHeight || 38}px`, color: option.headerFontColor || '#5ba8f0' }"
        >
          <span v-for="header in tableHeaders" :key="header.label">{{ header.label }}</span>
        </div>
        <div class="scroll-body">
          <div class="scroll-track">
            <div
              v-for="(row, index) in repeatedRows"
              :key="`${index}-${rowCells(row).join('-')}`"
              class="scroll-row"
              :style="{ background: index % 2 ? (option.evenRowBGC || '#1e304833') : (option.oddRowBGC || '#1e3048aa') }"
              @click.stop="emitDrilldown({ dataIndex: index % Math.max(1, tableRows.length), data: row })"
            >
              <span v-for="(cell, cellIndex) in rowCells(row)" :key="cellIndex">{{ cell }}</span>
            </div>
          </div>
        </div>
      </div>
    </template>

    <template v-else-if="widget.config?.imageUrl && /Img|Icon|Carousel/i.test(componentType)">
      <img
        class="material-image"
        :src="assetUrl(widget.config.imageUrl)"
        :alt="widget.componentName"
        :style="{
          objectFit: widget.config?.objectFit || 'contain',
          opacity: widget.config?.opacity ?? 1
        }"
      >
    </template>

    <template v-else-if="componentType === 'JReplicaProgressRing'">
      <div class="replica-progress-ring" :style="replicaProgressStyle">
        <div class="replica-progress-orbit" aria-hidden="true"><i></i><span></span></div>
        <strong>{{ replicaProgress }}<small>%</small></strong>
        <p>{{ widget.componentName }}</p>
      </div>
    </template>

    <template v-else-if="isChart">
      <JimuChart :widget="widget" :data="data" @data-click="emitDrilldown" />
    </template>

    <template v-else>
      <div class="generic-widget">
        <i class="fa-solid fa-chart-simple"></i>
        <strong>{{ widget.componentName }}</strong>
        <span>{{ componentType }}</span>
      </div>
    </template>
  </div>
</template>

<style scoped>
.widget-renderer {
  position: relative;
  width: 100%;
  height: 100%;
  overflow: hidden;
  pointer-events: none;
}
.widget-renderer.interactive { pointer-events: auto; }

.widget-renderer[class*="variant-reference-"] {
  clip-path: polygon(
    0 0,
    calc(100% - 16px) 0,
    100% 16px,
    100% calc(100% - 10px),
    calc(100% - 10px) 100%,
    14px 100%,
    0 calc(100% - 14px)
  );
}

.tech-frame {
  position: absolute;
  inset: 0;
  z-index: 12;
  overflow: hidden;
  pointer-events: none;
}
.tech-frame > i {
  position: absolute;
  width: 24px;
  height: 24px;
  border-color: var(--widget-surface, var(--widget-primary));
  filter: drop-shadow(0 0 7px color-mix(in srgb, var(--widget-surface, var(--widget-primary)) 58%, transparent));
}
.tech-frame .corner-tl { top: 5px; left: 5px; border-top: 2px solid; border-left: 2px solid; }
.tech-frame .corner-tr { top: 5px; right: 5px; border-top: 2px solid; border-right: 2px solid; }
.tech-frame .corner-bl { bottom: 5px; left: 5px; border-bottom: 2px solid; border-left: 2px solid; }
.tech-frame .corner-br { right: 5px; bottom: 5px; border-right: 2px solid; border-bottom: 2px solid; }
.widget-renderer[class*="variant-reference-"] .tech-frame > i {
  width: 31px;
  height: 23px;
  border-color: var(--widget-primary);
  filter: drop-shadow(0 0 8px color-mix(in srgb, var(--widget-primary) 74%, transparent));
}
.widget-renderer[class*="variant-reference-"] .tech-frame .corner-tl,
.widget-renderer[class*="variant-reference-"] .tech-frame .corner-br { border-width: 2px; }
.widget-renderer[class*="variant-reference-"] .tech-frame > span {
  left: 34px;
  width: min(46%, 260px);
  background: linear-gradient(90deg, var(--widget-primary) 0 32%, var(--widget-secondary) 56%, transparent);
}
.tech-frame > span {
  position: absolute;
  top: 0;
  left: 42px;
  width: min(34%, 190px);
  height: 3px;
  background: linear-gradient(90deg, var(--widget-surface), var(--widget-accent), transparent);
  box-shadow: 0 0 14px color-mix(in srgb, var(--widget-surface) 54%, transparent);
}

.panel-chrome { position: absolute; inset: 0; z-index: 8; pointer-events: none; }
.widget-renderer[class*="variant-showcase-"] .jimu-time small { display: none; }
.variant-showcase-template-01-title .jimu-text {
  position: relative;
  overflow: visible;
  justify-content: flex-start;
  padding-left: 0;
  text-shadow: 0 0 7px color-mix(in srgb, var(--widget-primary) 58%, transparent);
}
.variant-showcase-template-01-title .jimu-text::before {
  position: absolute;
  top: -5px;
  right: -3px;
  width: 72px;
  height: 42px;
  border-top: 1px solid color-mix(in srgb, var(--widget-primary) 34%, transparent);
  border-right: 1px solid color-mix(in srgb, var(--widget-primary) 34%, transparent);
  transform: skewX(-25deg);
  content: '';
}
.variant-showcase-template-01-title .jimu-text::after {
  position: absolute;
  right: -4px;
  bottom: -5px;
  left: 0;
  height: 2px;
  background: linear-gradient(90deg, var(--widget-primary), color-mix(in srgb, var(--widget-secondary) 54%, transparent) 74%, transparent);
  box-shadow: 0 0 7px color-mix(in srgb, var(--widget-primary) 36%, transparent);
  content: '';
}
.variant-showcase-template-01-section-band .jimu-text {
  justify-content: flex-start;
  padding: 0 14px;
  box-sizing: border-box;
}
.variant-showcase-template-01-metric .stat-card { min-height: 0; padding: 4px 6px; overflow: visible; }
.variant-showcase-template-01-metric .stat-kicker,
.variant-showcase-template-01-metric .stat-sync,
.variant-showcase-template-01-metric .stat-compare,
.variant-showcase-template-01-metric .stat-signal,
.variant-showcase-template-01-metric .stat-card::before,
.variant-showcase-template-01-metric .stat-card::after { display: none; }
.variant-showcase-template-01-metric .stat-main { gap: 9px; margin-top: 0; }
.variant-showcase-template-01-metric .stat-icon {
  flex-basis: 54px;
  width: 54px;
  height: 54px;
  border-radius: 3px;
  clip-path: polygon(50% 0, 95% 22%, 86% 78%, 50% 100%, 14% 78%, 5% 22%);
}
.variant-showcase-template-01-metric .stat-reading { gap: 2px; }
.variant-showcase-template-01-metric .stat-value { font-size: 16px !important; }
.variant-showcase-template-01-metric .stat-label { color: var(--widget-text); font-size: 9px; }
.replica-progress-ring {
  position: relative;
  display: grid;
  width: 100%;
  height: 100%;
  place-items: center;
  color: var(--widget-text, #d8ecff);
  filter: drop-shadow(0 0 8px color-mix(in srgb, var(--replica-ring) 54%, transparent));
}
.replica-progress-ring::before,
.replica-progress-ring::after,
.replica-progress-orbit {
  position: absolute;
  border-radius: 50%;
  content: '';
}
.replica-progress-ring::before {
  inset: 11%;
  background: conic-gradient(
    from -90deg,
    var(--replica-ring) 0 var(--replica-progress),
    color-mix(in srgb, var(--replica-track) 76%, transparent) var(--replica-progress) 100%
  );
  -webkit-mask: radial-gradient(circle, transparent 0 53%, #000 54% 69%, transparent 70%);
  mask: radial-gradient(circle, transparent 0 53%, #000 54% 69%, transparent 70%);
}
.replica-progress-ring::after {
  inset: 3%;
  background: repeating-conic-gradient(
    from -90deg,
    color-mix(in srgb, var(--replica-ring-secondary) 86%, white) 0 1deg,
    transparent 1deg 8deg
  );
  opacity: .72;
  -webkit-mask: radial-gradient(circle, transparent 0 78%, #000 79% 82%, transparent 83%);
  mask: radial-gradient(circle, transparent 0 78%, #000 79% 82%, transparent 83%);
}
.replica-progress-orbit {
  inset: 18%;
  border: 1px solid color-mix(in srgb, var(--replica-ring) 38%, transparent);
  box-shadow: inset 0 0 16px color-mix(in srgb, var(--replica-ring) 12%, transparent);
}
.replica-progress-orbit i,
.replica-progress-orbit span {
  position: absolute;
  top: 50%;
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--replica-ring);
  box-shadow: 0 0 7px var(--replica-ring);
}
.replica-progress-orbit i { left: -2px; }
.replica-progress-orbit span { right: -2px; }
.replica-progress-ring strong {
  z-index: 2;
  margin-top: -2px;
  font-family: Bahnschrift, DIN Alternate, Microsoft YaHei UI, sans-serif;
  font-size: clamp(21px, 26%, 30px);
  font-weight: 500;
  letter-spacing: -.8px;
  line-height: 1;
  text-shadow: 0 0 9px color-mix(in srgb, var(--replica-ring) 80%, transparent);
}
.replica-progress-ring strong small { margin-left: 1px; font-size: .48em; font-weight: 500; }
.replica-progress-ring p {
  position: absolute;
  right: 0;
  bottom: 1px;
  left: 0;
  overflow: hidden;
  margin: 0;
  color: color-mix(in srgb, var(--widget-text, #d8ecff) 70%, transparent);
  font-size: 8px;
  letter-spacing: .7px;
  text-align: center;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.replica-weather-strip {
  display: grid;
  height: 100%;
  grid-template-columns: repeat(3, 1fr);
  color: var(--widget-text, #eaf8ff);
}
.replica-weather-strip > div {
  display: grid;
  grid-template-columns: 28px 1fr;
  grid-template-rows: 19px 12px;
  align-items: center;
  padding-left: 8px;
  border-left: 1px solid color-mix(in srgb, var(--widget-primary) 22%, transparent);
}
.replica-weather-strip i { grid-row: 1 / 3; color: #bfeaff; font-size: 20px; filter: drop-shadow(0 0 6px rgba(93, 211, 255, .45)); }
.replica-weather-strip span { display: flex; gap: 5px; align-items: center; font-size: 8px; }
.replica-weather-strip span b { font-size: 8px; font-weight: 600; }
.replica-weather-strip span small { color: var(--widget-muted, #7ea8c4); font-size: 7px; }
.replica-weather-strip em { color: var(--widget-muted, #7ea8c4); font-size: 7px; font-style: normal; }
.replica-map-operations { display: grid; height: 100%; align-content: space-around; }
.replica-map-operations > div { display: flex; align-items: center; gap: 11px; min-height: 34px; color: var(--widget-text, #eaf8ff); }
.operation-beacon {
  position: relative;
  display: block;
  width: 27px;
  height: 18px;
  transform: skewY(-12deg);
  border: 2px solid currentColor;
  border-radius: 50%;
  background: color-mix(in srgb, currentColor 28%, #05121c);
  box-shadow: 0 0 10px currentColor, inset 0 0 8px currentColor;
}
.operation-beacon::before,
.operation-beacon::after { position: absolute; right: 3px; left: 3px; height: 4px; border: 1px solid currentColor; border-radius: 50%; content: ''; }
.operation-beacon::before { top: -7px; }
.operation-beacon::after { bottom: -7px; }
.operation-beacon i { position: absolute; top: -13px; left: 10px; width: 6px; height: 28px; background: currentColor; opacity: .42; filter: blur(2px); }
.replica-map-operations strong { font-size: 11px; font-weight: 600; text-shadow: 0 0 6px #000; }
.replica-map-operations .tone-amber { color: #f5a94c; }
.replica-map-operations .tone-teal { color: #35e4a4; }
.replica-map-operations .tone-violet { color: #b278ff; }
.replica-ranking-list { display: grid; height: 100%; margin: 0; padding: 0 3px; list-style: none; }
.replica-ranking-list li { display: grid; grid-template-columns: 17px 66px 1fr 34px; align-items: center; color: var(--widget-muted, #7ea8c4); font-size: 9px; }
.replica-ranking-list li b { display: grid; width: 12px; height: 12px; place-items: center; background: color-mix(in srgb, var(--widget-secondary) 58%, transparent); color: var(--widget-text, #fff); font-size: 7px; }
.replica-ranking-list li i { position: relative; height: 1px; background: linear-gradient(90deg, color-mix(in srgb, var(--widget-primary) 76%, transparent), color-mix(in srgb, var(--widget-secondary) 24%, transparent)); }
.replica-ranking-list li i::after { position: absolute; top: -3px; right: 0; width: 6px; height: 6px; transform: rotate(45deg); border: 1px solid var(--widget-text, #fff); content: ''; }
.replica-ranking-list li em { color: var(--widget-text, #fff); font-size: 8px; font-style: normal; text-align: right; }
.panel-chrome::before {
  position: absolute;
  top: 7px;
  right: 9px;
  left: 9px;
  height: 26px;
  border-bottom: 1px solid color-mix(in srgb, var(--widget-surface, var(--widget-primary)) 24%, transparent);
  background: linear-gradient(90deg, color-mix(in srgb, var(--widget-surface, var(--widget-primary)) 17%, transparent), transparent 64%);
  clip-path: polygon(0 0, 72% 0, 76% 100%, 0 100%);
  content: '';
}
.widget-renderer[class*="variant-reference-"] .panel-chrome::before {
  top: 6px;
  right: 8px;
  left: 8px;
  height: 29px;
  border-bottom-color: color-mix(in srgb, var(--widget-primary) 52%, transparent);
  background:
    linear-gradient(90deg, color-mix(in srgb, var(--widget-primary) 24%, transparent), color-mix(in srgb, var(--widget-secondary) 8%, transparent) 56%, transparent 78%);
  clip-path: polygon(0 0, 76% 0, 82% 100%, 0 100%);
}
.widget-renderer[class*="variant-reference-"] .panel-chrome::after {
  position: absolute;
  top: 10px;
  left: 15px;
  width: 24px;
  height: 11px;
  background: repeating-linear-gradient(118deg, var(--widget-primary) 0 4px, transparent 4px 7px);
  opacity: .9;
  content: '';
}
.widget-renderer[class*="variant-reference-"] .panel-chrome > small { left: 48px; color: var(--widget-text); font-weight: 600; }
.panel-chrome > small,
.panel-chrome > b {
  position: absolute;
  top: 13px;
  display: flex;
  align-items: center;
  gap: 7px;
  color: var(--widget-muted, #8296a8);
  font: 8px 'Segoe UI', 'Microsoft YaHei', sans-serif;
  letter-spacing: .16em;
  opacity: .82;
}
.panel-chrome > small { left: 18px; }
.panel-chrome > b { right: 18px; color: color-mix(in srgb, var(--widget-primary, #77e6ff) 72%, #fff); font-size: 7px; font-weight: 600; }
.panel-chrome > small i {
  width: 18px;
  height: 1px;
  background: linear-gradient(90deg, var(--widget-primary, #77e6ff), transparent);
}
.panel-chrome > b i {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--widget-positive, #77f2bc);
  box-shadow: 0 0 9px color-mix(in srgb, var(--widget-positive, #77f2bc) 74%, transparent);
  animation: panel-live 2.2s ease-in-out infinite;
}
.chrome-spectrum {
  position: absolute;
  top: 0;
  left: 18px;
  width: 86px;
  height: 2px;
  border-radius: 4px;
  background: linear-gradient(90deg, var(--widget-primary), var(--widget-secondary), transparent);
  box-shadow: 0 0 16px color-mix(in srgb, var(--widget-primary) 32%, transparent);
}

.ambient-glow {
  position: absolute;
  inset: 0;
  border-radius: 50%;
  background:
    radial-gradient(ellipse at 50% 50%, color-mix(in srgb, var(--main-color) 16%, transparent), transparent 54%),
    radial-gradient(ellipse at 62% 42%, color-mix(in srgb, var(--sub-color) 10%, transparent), transparent 68%);
  filter: blur(30px);
  opacity: .7;
  animation: ambient-drift 16s ease-in-out infinite alternate;
}
.ambient-glow i,
.ambient-glow span {
  position: absolute;
  inset: 18%;
  border: 1px solid color-mix(in srgb, var(--main-color) 10%, transparent);
  border-radius: 50%;
  transform: rotate(-12deg);
}
.ambient-glow span { inset: 31%; border-color: color-mix(in srgb, var(--sub-color) 9%, transparent); transform: rotate(18deg); }

.data-constellation { position: absolute; inset: 0; overflow: hidden; opacity: .52; }
.data-constellation i {
  position: absolute;
  border-radius: 50%;
  background: var(--main-color);
  box-shadow: 0 0 12px color-mix(in srgb, var(--main-color) 38%, transparent);
  animation: constellation-breathe 8s ease-in-out infinite;
}

.flow-ribbon {
  position: absolute;
  inset: 0;
  overflow: hidden;
  opacity: .62;
  mix-blend-mode: screen;
}
.flow-ribbon svg { width: 100%; height: 100%; overflow: visible; }
.flow-ribbon path { fill: none; vector-effect: non-scaling-stroke; }
.ribbon-halo {
  stroke: color-mix(in srgb, var(--main-color) 18%, transparent);
  stroke-width: 34;
  filter: blur(18px);
}
.ribbon-main {
  stroke: var(--main-color);
  stroke-width: 1.7;
  stroke-dasharray: 7 15;
  filter: drop-shadow(0 0 8px var(--main-color));
  animation: ribbon-dash 18s linear infinite;
}
.ribbon-echo {
  stroke: color-mix(in srgb, var(--sub-color) 62%, transparent);
  stroke-width: 1;
  stroke-dasharray: 2 11;
  animation: ribbon-dash 26s linear reverse infinite;
}
.ribbon-pulse {
  stroke: var(--sub-color);
  stroke-width: 3;
  stroke-linecap: round;
  stroke-dasharray: 4 610;
  filter: drop-shadow(0 0 12px var(--sub-color));
  animation: ribbon-pulse 8s linear infinite;
}
.flow-ribbon > span {
  position: absolute;
  width: 6px;
  height: 6px;
  border: 1px solid color-mix(in srgb, var(--main-color) 76%, #fff);
  border-radius: 50%;
  background: var(--sub-color);
  box-shadow: 0 0 4px #fff, 0 0 18px var(--main-color);
  animation: ribbon-node 3.4s ease-in-out infinite;
}

.signal-rail { position: absolute; inset: 0; display: flex; align-items: center; gap: 10px; opacity: .72; }
.signal-rail::before {
  width: 64px;
  color: color-mix(in srgb, var(--main-color) 72%, #fff);
  font: 7px Consolas, monospace;
  letter-spacing: .18em;
  content: 'SIGNAL';
}
.signal-rail > i { width: 7px; height: 7px; border: 1px solid var(--main-color); border-radius: 50%; box-shadow: 0 0 10px var(--main-color); }
.signal-rail > span { flex: 1; height: 1px; background: linear-gradient(90deg, var(--main-color), color-mix(in srgb, var(--sub-color) 54%, transparent), transparent); }
.signal-rail > b { width: 120px; height: 3px; border-radius: 4px; background: repeating-linear-gradient(90deg, var(--sub-color) 0 8px, transparent 8px 13px); opacity: .46; }

.focus-orb { position: absolute; inset: 0; display: grid; place-items: center; opacity: .58; }
.focus-orb::before,
.focus-orb::after,
.focus-orb > i,
.focus-orb > span {
  position: absolute;
  border: 1px solid color-mix(in srgb, var(--main-color) 30%, transparent);
  border-radius: 50%;
  content: '';
}
.focus-orb::before { inset: 5%; border-style: dashed; animation: orb-rotate 24s linear infinite; }
.focus-orb::after { inset: 22%; border-color: color-mix(in srgb, var(--sub-color) 34%, transparent); animation: orb-rotate 15s linear reverse infinite; }
.focus-orb > i { inset: 38%; background: radial-gradient(circle, color-mix(in srgb, var(--main-color) 36%, transparent), transparent 68%); box-shadow: 0 0 24px color-mix(in srgb, var(--main-color) 22%, transparent); }
.focus-orb > span { inset: 14% 47%; border-radius: 2px; }
.focus-orb > b { width: 4px; height: 4px; border-radius: 50%; background: var(--main-color); box-shadow: 0 0 11px var(--main-color); transform: translate(32px, -18px); }

.table-panel-title { position: absolute; top: 38px; right: 18px; left: 18px; z-index: 9; overflow: hidden; color: var(--widget-text, #f4faff); font-size: 12px; font-weight: 600; letter-spacing: .035em; text-overflow: ellipsis; white-space: nowrap; }
.table-panel-title::before { display: inline-block; width: 16px; height: 2px; margin-right: 8px; background: var(--widget-primary); vertical-align: middle; content: ''; }
.widget-renderer.themed .scroll-board { position: absolute; inset: 60px 10px 10px; width: auto; height: auto; border-top: 1px solid color-mix(in srgb, var(--widget-grid, #21364a) 42%, transparent); }

.data-empty-state { position: absolute; inset: 26px 0 0; display: grid; place-content: center; justify-items: center; gap: 6px; color: var(--widget-muted, #7297aa); text-align: center; }
.data-empty-state strong { color: var(--widget-text, #e9faff); font-size: 13px; font-weight: 500; }.data-empty-state p { margin: 0; color: var(--widget-muted, #7297aa); font-size: 9px; }.data-empty-state small { margin-top: 5px; padding: 4px 7px; border: 1px solid color-mix(in srgb, var(--widget-grid, #164766) 78%, transparent); color: var(--widget-primary, #21e7ff); font: 8px Consolas, monospace; }.data-empty-state small i { margin-right: 5px; }
.empty-orbit { position: relative; width: 48px; height: 48px; margin-bottom: 3px; border: 1px dashed color-mix(in srgb, var(--widget-primary, #21e7ff) 48%, transparent); border-radius: 50%; animation: empty-spin 9s linear infinite; }.empty-orbit::before,.empty-orbit::after { content: ''; position: absolute; inset: 8px; border: 1px solid color-mix(in srgb, var(--widget-secondary, #3478ff) 35%, transparent); border-radius: 50%; }.empty-orbit::after { inset: 19px; background: var(--widget-primary, #21e7ff); box-shadow: 0 0 12px var(--widget-primary, #21e7ff); }.empty-orbit i,.empty-orbit span { position: absolute; width: 5px; height: 5px; border: 1px solid var(--widget-primary, #21e7ff); background: #061321; transform: rotate(45deg); }.empty-orbit i { left: 2px; top: 12px; }.empty-orbit span { right: 1px; bottom: 10px; }

.jimu-text,
.jimu-time,
.jimu-number {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  white-space: nowrap;
  text-shadow: 0 0 10px rgba(120, 196, 255, .35);
}

.command-heading {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  padding: 0 22px;
  box-sizing: border-box;
}
.command-heading > small {
  margin-bottom: 4px;
  color: var(--widget-primary, #77e6ff);
  font: 8px Consolas, monospace;
  letter-spacing: .26em;
  opacity: .75;
}
.command-heading > strong {
  overflow: hidden;
  max-width: 96%;
  font-family: Bahnschrift, 'Microsoft YaHei UI', sans-serif;
  font-size: clamp(26px, 2vw, 38px);
  font-weight: 650;
  letter-spacing: .055em;
  line-height: 1.05;
  text-overflow: ellipsis;
  text-shadow: 0 10px 32px rgba(0, 0, 0, .5);
  white-space: nowrap;
}
.command-heading > span {
  display: flex;
  gap: 8px;
  align-items: center;
  width: 174px;
  margin-top: 9px;
}
.command-heading > span::before { width: 46px; height: 2px; border-radius: 4px; background: var(--widget-primary); box-shadow: 0 0 14px color-mix(in srgb, var(--widget-primary) 45%, transparent); content: ''; }
.command-heading > span i { flex: 1; height: 1px; background: linear-gradient(90deg, color-mix(in srgb, var(--widget-secondary) 74%, transparent), transparent); }
.command-heading > span b { width: 4px; height: 4px; border-radius: 50%; background: var(--widget-accent); }

.jimu-time {
  flex-direction: column;
  align-items: flex-end;
  padding: 0 18px;
  box-sizing: border-box;
  font-size: 12px;
  font-weight: 500;
  line-height: 1.45;
  text-shadow: none;
}
.jimu-time small { color: var(--widget-muted, #8296a8); font: 7px Consolas, monospace; letter-spacing: .18em; }
.jimu-time span { color: var(--widget-text, #f4faff); font-variant-numeric: tabular-nums; letter-spacing: .02em; }

.system-status {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  height: 100%;
  padding: 0 15px;
  box-sizing: border-box;
}
.status-orbit { position: relative; display: grid; place-items: center; width: 24px; height: 24px; border: 1px solid color-mix(in srgb, var(--widget-positive) 28%, transparent); border-radius: 50%; }
.status-orbit::before { position: absolute; inset: 4px; border: 1px dashed color-mix(in srgb, var(--widget-positive) 42%, transparent); border-radius: 50%; animation: orb-rotate 7s linear infinite; content: ''; }
.status-orbit i { width: 6px; height: 6px; border-radius: 50%; background: var(--widget-positive); box-shadow: 0 0 12px var(--widget-positive); animation: panel-live 2s ease-in-out infinite; }
.system-status > div { display: flex; flex: 1; min-width: 0; flex-direction: column; }
.system-status small { color: var(--widget-muted); font-size: 8px; letter-spacing: .1em; }
.system-status strong { overflow: hidden; color: var(--widget-text); font-size: 11px; font-weight: 550; text-overflow: ellipsis; white-space: nowrap; }
.system-status > b { color: var(--widget-positive); font: 7px Consolas, monospace; letter-spacing: .08em; }

.variant-reference-command-title {
  overflow: visible;
  clip-path: none !important;
}
.variant-reference-command-title .command-heading {
  position: relative;
  overflow: visible;
  border-top: 1px solid color-mix(in srgb, var(--widget-primary) 86%, #fff 8%);
  border-bottom: 2px solid var(--widget-primary);
  background: linear-gradient(180deg, rgba(4, 28, 55, .94), rgba(1, 10, 28, .98));
  clip-path: polygon(8% 0, 92% 0, 100% 100%, 0 100%);
  filter: drop-shadow(0 0 11px color-mix(in srgb, var(--widget-primary) 30%, transparent));
}
.variant-reference-command-title .command-heading::before,
.variant-reference-command-title .command-heading::after {
  position: absolute;
  top: 14px;
  width: 78px;
  height: 30px;
  background: repeating-linear-gradient(112deg, var(--widget-primary) 0 9px, transparent 9px 15px);
  opacity: .86;
  content: '';
}
.variant-reference-command-title .command-heading::before { right: calc(100% + 38px); }
.variant-reference-command-title .command-heading::after { left: calc(100% + 38px); transform: scaleX(-1); }
.variant-reference-command-title .command-heading > small,
.variant-reference-command-title .command-heading > span { display: none; }
.variant-reference-command-title .command-heading > strong {
  font-family: 'Microsoft YaHei UI', Bahnschrift, sans-serif;
  font-size: 30px;
  font-weight: 760;
  letter-spacing: .08em;
  text-shadow: 0 0 8px color-mix(in srgb, var(--widget-primary) 35%, transparent), 0 4px 18px #000;
}

.variant-reference-status-ribbon,
.variant-reference-time-ribbon {
  border-top: 1px solid color-mix(in srgb, var(--widget-primary) 66%, transparent);
  border-bottom: 1px solid color-mix(in srgb, var(--widget-primary) 70%, transparent);
  background: linear-gradient(90deg, #064762 0%, #06334c 58%, #031a32 100%);
}
.variant-reference-status-ribbon { clip-path: polygon(0 0, 94% 0, 100% 100%, 0 100%) !important; }
.variant-reference-time-ribbon { clip-path: polygon(6% 0, 100% 0, 100% 100%, 0 100%) !important; }
.variant-reference-status-ribbon::after,
.variant-reference-time-ribbon::before {
  position: absolute;
  top: 13px;
  width: 76px;
  height: 28px;
  background: repeating-linear-gradient(112deg, #11ddf7 0 9px, transparent 9px 15px);
  content: '';
}
.variant-reference-status-ribbon::after { right: 54px; }
.variant-reference-time-ribbon::before { left: 54px; transform: scaleX(-1); }
.variant-reference-status-ribbon .system-status { padding-left: 42px; }
.variant-reference-status-ribbon .status-orbit { width: 18px; height: 18px; }
.variant-reference-status-ribbon .system-status small,
.variant-reference-status-ribbon .system-status > b,
.variant-reference-time-ribbon .jimu-time small { display: none; }
.variant-reference-status-ribbon .system-status strong {
  color: #eaffff;
  font-size: 13px;
  letter-spacing: .04em;
}
.variant-reference-time-ribbon .jimu-time {
  justify-content: center;
  padding-right: 44px;
  color: #eaffff !important;
  font-family: Consolas, 'Microsoft YaHei UI', sans-serif;
  font-size: 13px;
}

.jimu-number {
  color: #79dcff;
  font-variant-numeric: tabular-nums;
  text-shadow: 0 0 14px rgba(42, 162, 255, .62);
}

.jimu-color-block {
  width: 100%;
  height: 100%;
  background: linear-gradient(90deg, #0082fc 0 33%, #05f8d6 33% 66%, #f7c948 66%);
}

.stats-summary {
  display: grid;
  grid-template-columns: var(--stats-columns, repeat(auto-fit, minmax(min(100%, 180px), 1fr)));
  gap: var(--stats-gap, 10px);
  align-items: var(--stats-align, stretch);
  justify-items: var(--stats-justify, stretch);
  width: 100%;
  height: 100%;
  padding: var(--stats-padding, 0);
  box-sizing: border-box;
}

.density-compact .stats-summary {
  --stats-gap: 8px !important;
  --stats-padding: 8px !important;
}

.density-compact .stat-card {
  --stat-card-padding: 8px 10px !important;
  min-height: 56px;
}

.density-compact .scroll-head,
.density-compact .scroll-row {
  font-size: 9px;
}

.stat-card {
  position: relative;
  display: flex;
  flex-direction: var(--stat-card-direction, column);
  align-items: var(--stat-card-align, flex-start);
  justify-content: var(--stat-card-justify, center);
  min-width: 0;
  width: 100%;
  height: 100%;
  min-height: var(--stat-card-min-height, 72px);
  padding: var(--stat-card-padding, 15px 18px 13px);
  box-sizing: border-box;
  overflow: var(--stat-card-overflow, hidden);
  border: var(--stat-card-border-width, 0px) solid var(--stat-card-border, transparent);
  border-radius: var(--stat-card-radius, inherit);
  background: var(--stat-card-bg, transparent);
  box-shadow: var(--stat-card-shadow, none);
}
.stat-main {
  position: relative;
  z-index: 2;
  display: flex;
  align-items: center;
  gap: var(--stat-main-gap, clamp(9px, 1vw, 16px));
  width: 100%;
  min-width: 0;
  margin-top: var(--stat-main-margin-top, 14px);
}
.stat-icon {
  position: relative;
  display: grid;
  flex: 0 0 44px;
  place-items: center;
  width: 44px;
  height: 44px;
  overflow: hidden;
  border: 1px solid color-mix(in srgb, var(--widget-glow) 68%, #fff 8%);
  border-radius: 10px 3px 10px 3px;
  background:
    linear-gradient(145deg, color-mix(in srgb, var(--widget-glow) 34%, transparent), color-mix(in srgb, var(--widget-background-raised) 84%, transparent)),
    linear-gradient(90deg, transparent 48%, color-mix(in srgb, var(--widget-glow) 12%, transparent) 50%, transparent 52%);
  color: color-mix(in srgb, var(--widget-glow) 78%, #fff);
  box-shadow:
    inset 0 0 18px color-mix(in srgb, var(--widget-glow) 13%, transparent),
    0 0 18px color-mix(in srgb, var(--widget-glow) 15%, transparent);
  font-size: 18px;
  clip-path: polygon(0 0, calc(100% - 9px) 0, 100% 9px, 100% 100%, 9px 100%, 0 calc(100% - 9px));
}
.stat-icon::before,
.stat-icon::after {
  position: absolute;
  content: '';
  pointer-events: none;
}
.stat-icon::before {
  inset: 5px;
  border: 1px solid color-mix(in srgb, var(--widget-glow) 18%, transparent);
  border-radius: 7px 1px 7px 1px;
}
.stat-icon::after {
  right: -12px;
  bottom: -13px;
  width: 30px;
  height: 30px;
  border: 1px solid color-mix(in srgb, var(--widget-accent) 36%, transparent);
  border-radius: 50%;
}
.stat-icon > i {
  z-index: 1;
  filter: drop-shadow(0 0 6px color-mix(in srgb, var(--widget-glow) 70%, transparent));
}
.stat-reading {
  display: flex;
  flex: 1;
  min-width: 0;
  flex-direction: column;
}
.density-compact .stat-main { gap: 7px; margin-top: 10px; }
.density-compact .stat-icon {
  flex-basis: 32px;
  width: 32px;
  height: 32px;
  border-radius: 7px 2px 7px 2px;
  font-size: 13px;
}
.variant-metric-spotlight .stat-icon {
  flex-basis: 50px;
  width: 50px;
  height: 50px;
  border-color: color-mix(in srgb, var(--widget-glow) 82%, #fff 10%);
  font-size: 21px;
}
.stat-card::before {
  position: absolute;
  top: 0;
  right: 14px;
  left: 14px;
  height: 1px;
  background: linear-gradient(90deg, var(--widget-glow), transparent 62%);
  opacity: .46;
  content: '';
}
.stat-card::after {
  position: absolute;
  right: -28px;
  bottom: -48px;
  width: 118px;
  height: 118px;
  border: 1px solid color-mix(in srgb, var(--widget-glow) 11%, transparent);
  border-radius: 50%;
  box-shadow: inset 0 0 28px color-mix(in srgb, var(--widget-glow) 5%, transparent);
  content: '';
}
.stat-card:not(.with-decoration)::before,
.stat-card:not(.with-decoration)::after { display: none; }
.stat-kicker {
  position: absolute;
  top: 12px;
  right: 14px;
  left: 16px;
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--widget-muted);
  font: 7px 'Segoe UI', 'Microsoft YaHei', sans-serif;
  letter-spacing: .08em;
}
.stat-kicker > i { width: 4px; height: 4px; border-radius: 50%; background: var(--widget-glow); box-shadow: 0 0 8px var(--widget-glow); }
.stat-kicker > b { margin-left: auto; color: color-mix(in srgb, var(--widget-glow) 60%, var(--widget-muted)); font: 7px Consolas, monospace; }
.stat-signal { position: absolute; right: 16px; bottom: 13px; width: 42px; height: 9px; overflow: hidden; opacity: .46; }
.stat-signal::before,
.stat-signal::after,
.stat-signal i {
  position: absolute;
  bottom: 0;
  width: 10px;
  border-radius: 4px 4px 0 0;
  background: color-mix(in srgb, var(--widget-glow) 55%, transparent);
  content: '';
}
.stat-signal::before { left: 0; height: 3px; }
.stat-signal i { left: 15px; height: 7px; }
.stat-signal::after { right: 0; height: 5px; }

.variant-metric-spotlight .stat-card::after {
  right: -12px;
  bottom: -72px;
  width: 158px;
  height: 158px;
  border-color: color-mix(in srgb, var(--widget-glow) 16%, transparent);
}

.variant-reference-hero-kpi .tech-frame,
.variant-reference-hex-metric .tech-frame,
.variant-reference-score-strip .tech-frame,
.variant-reference-news-panel .tech-frame,
.variant-reference-ranking-panel .tech-frame,
.variant-reference-radar-panel .tech-frame,
.variant-reference-hero-panel .tech-frame { display: none; }

.variant-reference-hero-kpi .stats-summary { padding: 0; }
.variant-reference-hero-kpi .stat-card {
  padding: 12px 20px;
  border: 1px solid color-mix(in srgb, var(--widget-glow) 74%, #fff 8%);
  border-radius: 6px;
  background:
    radial-gradient(circle at 16% 50%, color-mix(in srgb, var(--widget-glow) 28%, transparent), transparent 28%),
    linear-gradient(135deg, color-mix(in srgb, var(--widget-glow) 22%, #03213c 78%), color-mix(in srgb, var(--widget-background-raised) 92%, transparent));
  box-shadow: inset 0 0 18px color-mix(in srgb, var(--widget-glow) 8%, transparent);
  clip-path: polygon(0 0, calc(100% - 9px) 0, 100% 9px, 100% 100%, 9px 100%, 0 calc(100% - 9px));
}
.variant-reference-hero-kpi .stat-kicker,
.variant-reference-hero-kpi .stat-sync,
.variant-reference-hero-kpi .stat-compare,
.variant-reference-hero-kpi .stat-signal { display: none; }
.variant-reference-hero-kpi .stat-main { margin-top: 0; gap: 18px; }
.variant-reference-hero-kpi .stat-icon {
  flex-basis: 58px;
  width: 58px;
  height: 58px;
  border: 1px solid color-mix(in srgb, var(--widget-glow) 86%, #fff 10%);
  border-radius: 50%;
  background: radial-gradient(circle, color-mix(in srgb, var(--widget-glow) 38%, transparent), color-mix(in srgb, var(--widget-glow) 8%, transparent) 62%, transparent 64%);
  clip-path: none;
  font-size: 23px;
}
.variant-reference-hero-kpi .stat-reading { justify-content: center; gap: 3px; }
.variant-reference-hero-kpi .stat-value { font-size: clamp(25px, 1.7vw, 34px); }
.variant-reference-hero-kpi .stat-label { color: #e8fbff; font-size: 13px; letter-spacing: .05em; }

.variant-reference-hex-metric .stats-summary { padding: 0; }
.variant-reference-hex-metric .stat-card {
  position: relative;
  overflow: visible;
  align-items: center;
  padding: 8px 9px 0;
  border: 0;
  background: transparent;
  box-shadow: none;
  clip-path: none;
}
.variant-reference-hex-metric .stat-card::before {
  position: absolute;
  top: 0;
  right: 18px;
  left: 18px;
  height: 98px;
  border: 1px solid color-mix(in srgb, var(--widget-glow) 72%, #fff 7%);
  background:
    radial-gradient(circle at 50% 38%, color-mix(in srgb, var(--widget-glow) 38%, transparent), transparent 42%),
    linear-gradient(160deg, color-mix(in srgb, var(--widget-glow) 18%, #031329 82%), #02091a 82%);
  box-shadow: inset 0 0 20px color-mix(in srgb, var(--widget-glow) 12%, transparent), 0 0 7px color-mix(in srgb, var(--widget-glow) 13%, transparent);
  clip-path: polygon(50% 0, 100% 28%, 100% 100%, 0 100%, 0 28%);
  content: '';
}
.variant-reference-hex-metric .stat-kicker,
.variant-reference-hex-metric .stat-sync,
.variant-reference-hex-metric .stat-compare,
.variant-reference-hex-metric .stat-signal { display: none; }
.variant-reference-hex-metric .stat-main {
  position: static;
  z-index: 1;
  height: 88px;
  flex-direction: column;
  justify-content: flex-start;
  gap: 3px;
  margin-top: 0;
  padding-top: 10px;
  box-sizing: border-box;
  text-align: center;
}
.variant-reference-hex-metric .stat-icon {
  position: absolute;
  top: 17px;
  left: 50%;
  flex-basis: 36px;
  width: 36px;
  height: 36px;
  border-radius: 50%;
  clip-path: none;
  font-size: 15px;
  transform: translateX(-50%);
}
.variant-reference-hex-metric .stat-reading { position: static; align-items: center; }
.variant-reference-hex-metric .stat-value {
  position: absolute;
  top: 106px;
  right: 0;
  left: 0;
  max-width: none;
  font-size: 23px !important;
  line-height: 1;
  text-align: center;
  z-index: 3;
}
.variant-reference-hex-metric .stat-value small { margin-left: 2px; font-size: 9px; }
.variant-reference-hex-metric .stat-label {
  position: absolute;
  right: 7px;
  bottom: 1px;
  left: 7px;
  display: grid;
  place-items: start center;
  height: 56px;
  padding-top: 5px;
  box-sizing: border-box;
  max-width: none;
  border: 1px solid color-mix(in srgb, var(--widget-glow) 74%, transparent);
  border-radius: 4px;
  background: color-mix(in srgb, var(--widget-glow) 8%, #03142d 92%);
  color: #eaffff;
  font-size: 10px;
  text-align: center;
  z-index: 2;
}

.variant-reference-score-strip .stats-summary { padding: 0; }
.variant-reference-score-strip .stat-card {
  position: relative;
  padding: 0 22px;
  border: 1px solid color-mix(in srgb, var(--widget-glow) 76%, #fff 7%);
  background: linear-gradient(90deg, color-mix(in srgb, var(--widget-glow) 12%, #03132c 88%), #020a1c 78%);
  box-shadow: inset 0 0 18px color-mix(in srgb, var(--widget-glow) 8%, transparent);
  clip-path: polygon(12px 0, 100% 0, 100% calc(100% - 12px), calc(100% - 12px) 100%, 0 100%, 0 12px);
}
.variant-reference-score-strip .stat-card::after {
  position: absolute;
  right: 18px;
  bottom: 7px;
  left: 18px;
  height: 2px;
  background: linear-gradient(90deg, var(--widget-glow) 0 72%, color-mix(in srgb, var(--widget-glow) 18%, transparent) 72%);
  box-shadow: 0 0 8px color-mix(in srgb, var(--widget-glow) 58%, transparent);
  content: '';
}
.variant-reference-score-strip .stat-kicker,
.variant-reference-score-strip .stat-sync,
.variant-reference-score-strip .stat-compare,
.variant-reference-score-strip .stat-signal { display: none; }
.variant-reference-score-strip .stat-main { width: 100%; gap: 12px; margin: 0; }
.variant-reference-score-strip .stat-icon {
  flex-basis: 30px;
  width: 30px;
  height: 30px;
  border-radius: 0;
  background: transparent;
  clip-path: none;
  color: var(--widget-glow);
  font-size: 15px;
}
.variant-reference-score-strip .stat-reading {
  display: grid;
  flex: 1;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px;
}
.variant-reference-score-strip .stat-label {
  grid-column: 1;
  grid-row: 1;
  color: #dff8ff;
  font-size: 13px;
  letter-spacing: .04em;
}
.variant-reference-score-strip .stat-value {
  grid-column: 2;
  grid-row: 1;
  font-size: 25px !important;
  text-shadow: 0 0 10px color-mix(in srgb, var(--widget-glow) 42%, transparent);
}

.variant-reference-news-panel .table-panel-title {
  position: absolute;
  top: 0;
  right: 22px;
  left: 12px;
  z-index: 3;
  display: flex;
  align-items: center;
  height: 42px;
  padding-left: 34px;
  border-top: 1px solid rgba(17, 221, 247, .48);
  border-bottom: 1px solid rgba(8, 123, 255, .42);
  background: linear-gradient(90deg, rgba(8, 123, 255, .18), transparent 84%);
  color: #f2fcff;
  font-size: 17px;
  font-weight: 700;
}
.variant-reference-news-panel .table-panel-title::before {
  position: absolute;
  left: 8px;
  width: 20px;
  height: 10px;
  background: repeating-linear-gradient(116deg, #11ddf7 0 5px, transparent 5px 8px);
  content: '';
}
.variant-reference-news-panel .scroll-board { padding: 50px 18px 10px; }
.variant-reference-news-panel .scroll-track { animation: none; }
.variant-reference-news-panel .scroll-row {
  position: relative;
  margin-bottom: 6px;
  border: 1px solid color-mix(in srgb, var(--widget-primary) 18%, transparent);
  background: linear-gradient(90deg, color-mix(in srgb, var(--widget-primary) 8%, transparent), transparent) !important;
}
.variant-reference-news-panel .scroll-row::before {
  width: 5px;
  height: 5px;
  margin-left: 10px;
  border-radius: 1px;
  background: var(--widget-primary);
  box-shadow: 0 0 8px var(--widget-primary);
  content: '';
}
.variant-reference-news-panel .scroll-row span {
  color: #cdeeff;
  font-size: 11px;
  text-align: left;
}

.variant-reference-region-label .jimu-text {
  position: relative;
  justify-content: flex-start;
  padding: 0 18px 0 48px;
  border-bottom: 1px solid color-mix(in srgb, var(--widget-primary) 42%, transparent);
  background: linear-gradient(90deg, color-mix(in srgb, var(--widget-primary) 18%, transparent), transparent 78%);
  clip-path: polygon(0 0, 86% 0, 92% 100%, 0 100%);
  color: #f0fcff !important;
  font-size: 17px !important;
  font-weight: 700;
  letter-spacing: .04em;
  text-align: left;
}
.variant-reference-region-label .jimu-text::before {
  position: absolute;
  left: 12px;
  width: 26px;
  height: 12px;
  background: repeating-linear-gradient(116deg, var(--widget-primary) 0 5px, transparent 5px 8px);
  content: '';
}

.variant-reference-process-step {
  overflow: visible;
  clip-path: none !important;
}
.variant-reference-process-step .jimu-text {
  position: relative;
  isolation: isolate;
  box-sizing: border-box;
  flex-direction: column;
  justify-content: flex-end;
  padding: 82px 5px 4px;
  border: 0;
  background: transparent;
  clip-path: none;
  color: #eaffff !important;
  font-size: 14px !important;
  font-weight: 680;
  line-height: 1.75;
  text-shadow: 0 0 8px color-mix(in srgb, var(--widget-primary) 34%, transparent);
  white-space: pre-line;
}
.variant-reference-process-step .jimu-text::before {
  position: absolute;
  top: 5px;
  left: 50%;
  width: 76px;
  height: 68px;
  border: 1px solid color-mix(in srgb, var(--widget-primary) 76%, #fff 6%);
  background:
    radial-gradient(circle at 50% 48%, #fff 0 3px, var(--widget-primary) 4px 8px, transparent 9px),
    linear-gradient(145deg, color-mix(in srgb, var(--widget-primary) 22%, #063066 78%), #020b24 78%);
  box-shadow: inset 0 0 24px color-mix(in srgb, var(--widget-primary) 14%, transparent), 0 0 10px color-mix(in srgb, var(--widget-primary) 34%, transparent);
  clip-path: polygon(50% 0, 100% 24%, 100% 76%, 50% 100%, 0 76%, 0 24%);
  transform: translateX(-50%);
  content: '';
}

.variant-reference-region-frame .border-generic {
  filter: none;
  opacity: .82;
}
.variant-reference-region-frame .border-1 polyline {
  stroke: #168ed0;
  stroke-width: 1.5;
}
.variant-reference-region-frame .border-2 .border-base {
  stroke: #168ed0;
  stroke-width: 1;
}

.variant-reference-process-connector .signal-rail {
  gap: 0;
  opacity: .92;
}
.variant-reference-process-connector .signal-rail::before,
.variant-reference-process-connector .signal-rail > i,
.variant-reference-process-connector .signal-rail > b { display: none; }
.variant-reference-process-connector .signal-rail > span {
  width: 100%;
  height: 16px;
  flex: none;
  background: linear-gradient(90deg, rgba(8, 123, 255, .22), #087bff 70%, #11ddf7);
  clip-path: polygon(0 36%, 68% 36%, 68% 0, 100% 50%, 68% 100%, 68% 64%, 0 64%);
  filter: drop-shadow(0 0 5px rgba(17, 221, 247, .58));
}
.variant-reference-process-step .jimu-text::after {
  position: absolute;
  right: 5px;
  bottom: 30px;
  left: 5px;
  height: 30px;
  border: 1px solid color-mix(in srgb, var(--widget-primary) 62%, transparent);
  border-radius: 4px;
  background: color-mix(in srgb, var(--widget-secondary) 16%, #03132b 84%);
  box-shadow: inset 0 0 12px color-mix(in srgb, var(--widget-primary) 8%, transparent);
  content: '';
  z-index: -1;
}

.variant-reference-hero-hud {
  opacity: .96;
  clip-path: polygon(0 0, calc(100% - 14px) 0, 100% 14px, 100% 100%, 10px 100%, 0 calc(100% - 10px)) !important;
}
.variant-reference-hero-hud .panel-chrome > b { display: none; }

.interactive .stat-card,
.interactive .scroll-row,
.interactive :deep(.jimu-chart) {
  cursor: pointer;
}

.stat-value {
  z-index: 1;
  color: var(--widget-primary, #77e6ff);
  font-family: Bahnschrift, 'Microsoft YaHei', sans-serif;
  font-size: 34px;
  font-weight: 650;
  line-height: 1.1;
  letter-spacing: -.025em;
  font-variant-numeric: tabular-nums;
}

.stat-value small {
  margin-left: 6px;
  color: var(--widget-muted, #9ed3ff);
  font-size: .38em;
  font-weight: 500;
}

.stat-compare {
  display: flex;
  gap: 5px;
  align-items: baseline;
  margin-top: 7px;
  font-size: 10px;
}

.stat-compare span { color: var(--widget-muted, #9ed3ff); }
.stat-compare b { color: var(--widget-positive, #77f2bc); }
.stat-sync { display: flex; align-items: center; gap: 6px; margin-top: 7px; color: var(--widget-muted); font-size: 8px; }
.stat-sync i { width: 4px; height: 4px; border-radius: 50%; background: var(--widget-positive); box-shadow: 0 0 7px var(--widget-positive); }

.stat-label {
  z-index: 1;
  margin-top: 4px;
  color: var(--widget-text, #cfeaff);
  font-size: 10px;
  font-weight: 500;
  letter-spacing: .025em;
}

.jimu-border {
  width: 100%;
  height: 100%;
  overflow: visible;
  fill: none;
}

.border-5 { filter: drop-shadow(0 0 5px rgba(0, 130, 252, .6)); }
.border-5 polyline { fill: none; }
.b5-line-1 { stroke: var(--main-color, #05f8d6); stroke-width: 3; }
.b5-line-2,
.b5-line-3,
.b5-line-4,
.b5-line-5,
.b5-line-6 { stroke: var(--sub-color, #0082fc); stroke-width: 2; }

.border-generic { filter: drop-shadow(0 0 3px rgba(5, 248, 214, .45)); }
.border-generic .border-base,
.border-generic .border-inner,
.border-generic polyline,
.border-generic line,
.border-generic .border-tab { fill: none; stroke: var(--main-color, #05f8d6); stroke-width: 2; }
.border-generic .border-inner { stroke: var(--sub-color, #0082fc); stroke-width: 1; }
.border-1 polyline { stroke-width: 4; }
.border-2 .border-base { stroke: var(--main-color, #05bfcf); stroke-width: 1; }
.border-3 .border-base { stroke-width: 3; }
.border-4 .border-base { stroke: var(--main-color, #0799c7); stroke-width: 2; }
.border-6 polyline { stroke: var(--sub-color, #0082fc); stroke-width: 3; }
.border-6 line { stroke: var(--main-color, #05f8d6); stroke-width: 1; }
.border-7 .border-base { stroke-width: 3; }
.border-7 .border-inner { stroke-width: 2; filter: drop-shadow(0 0 8px #05f8d6); }
.border-8 { filter: none; }
.border-8 .border-base { stroke: #174768; stroke-width: 1; }
.border-9 polyline { stroke-dasharray: 14 7; stroke-width: 3; }
.border-10 .border-base { stroke-width: 4; filter: drop-shadow(0 0 10px #05f8d6); }
.border-11 .border-tab { fill: #063e51; stroke-width: 2; }
.border-13 .border-base { stroke: var(--sub-color, #0082fc); stroke-dasharray: 22 8; stroke-width: 2; }

.border-12 rect:first-child {
  fill: rgba(0, 23, 54, .16);
  stroke: var(--main-color, #05f8d6);
  stroke-width: 2;
  filter: drop-shadow(0 0 8px rgba(0, 130, 252, .8));
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

.jimu-decoration span:first-child { transform: skewX(38deg) translateY(-5px); }
.jimu-decoration span:last-child { transform: skewX(-38deg) translateY(-5px); }
.jimu-decoration i {
  width: 42px;
  height: 11px;
  border-bottom: 3px solid var(--sub-color);
  transform: translateY(3px);
}

.decoration-1 span { height: 8px; background: repeating-linear-gradient(90deg, var(--main-color) 0 3px, transparent 3px 9px); box-shadow: none; }
.decoration-2 span:first-child { flex: .18; }
.decoration-2 span:last-child { flex: 1.5; height: 1px; }
.decoration-3 span { height: 7px; background: repeating-linear-gradient(90deg, var(--sub-color) 0 7px, var(--main-color) 7px 13px); }
.decoration-4 { transform: rotate(90deg); }
.decoration-4 span { height: 2px; }
.decoration-6 span { height: 4px; background: repeating-linear-gradient(90deg, var(--main-color) 0 2px, var(--sub-color) 2px 5px, transparent 5px 8px); }
.decoration-7 span:first-child { transform: skewX(-38deg); }
.decoration-7 span:last-child { transform: skewX(38deg); }
.decoration-8 span { height: 2px; }
.decoration-8 i { width: 90px; border-top: 2px solid var(--main-color); border-bottom-color: #f7c948; transform: skewX(35deg); }
.decoration-9 span { height: 6px; border-radius: 50%; background: repeating-radial-gradient(circle, var(--main-color) 0 3px, transparent 4px 10px); }
.decoration-10 span { height: 1px; background: #f7c948; box-shadow: 0 0 6px #f7c948; }
.decoration-11 i { width: 120px; height: 30px; border: 2px solid var(--main-color); background: rgba(5, 248, 214, .12); transform: skewX(-22deg); }
.decoration-12 span { height: 1px; }
.decoration-12 i { width: 58px; height: 58px; border: 1px solid var(--main-color); border-radius: 50%; transform: rotate(45deg); }

.scroll-board {
  width: 100%;
  height: 100%;
  font-size: 12px;
}

.scroll-head,
.scroll-row {
  display: grid;
  grid-template-columns: var(--scroll-columns, .8fr 1.6fr 1.35fr .8fr);
  align-items: center;
  min-width: 0;
  text-align: center;
}

.scroll-head {
  height: 38px;
  color: #5ba8f0;
  font-size: 14px;
  font-weight: 600;
}

.scroll-body {
  height: calc(100% - var(--header-height, 38px));
  overflow: hidden;
}

.scroll-track { animation: table-scroll 22s linear infinite; }
.scroll-track { animation-duration: var(--scroll-duration, 22s); }
.scroll-board.pause-on-hover:hover .scroll-track { animation-play-state: paused; }

.scroll-row { height: var(--row-height, 51px); }
.scroll-row span {
  overflow: hidden;
  padding: 0 6px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.themed .scroll-board { padding: 0; box-sizing: border-box; color: var(--widget-text, #f4faff) !important; }
.themed .scroll-head { border-bottom: 1px solid color-mix(in srgb, var(--widget-primary, #77e6ff) 24%, transparent); background: linear-gradient(90deg, color-mix(in srgb, var(--widget-primary, #77e6ff) 8%, transparent), transparent) !important; color: color-mix(in srgb, var(--widget-primary, #77e6ff) 72%, var(--widget-text)) !important; font-size: 10px; font-weight: 550; letter-spacing: .04em; }
.themed .scroll-row { border-bottom: 1px solid color-mix(in srgb, var(--widget-grid, #21364a) 24%, transparent); background: transparent !important; color: color-mix(in srgb, var(--widget-text, #f4faff) 86%, transparent); font-size: 10px; transition: background .2s ease, color .2s ease; }
.themed .scroll-row:nth-child(even) { background: rgba(255, 255, 255, .015) !important; }
.themed .scroll-row:hover { background: color-mix(in srgb, var(--widget-primary) 8%, transparent) !important; color: var(--widget-text); }

.material-image {
  position: relative;
  z-index: 2;
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.variant-wisdom-brain {
  overflow: visible;
  clip-path: none;
  background:
    radial-gradient(ellipse at 50% 52%, rgba(8, 112, 220, .52), rgba(3, 37, 91, .72) 56%, rgba(1, 8, 23, .16) 86%),
    linear-gradient(180deg, rgba(4, 33, 82, .72), rgba(1, 8, 23, .14));
}
.variant-wisdom-brain::before,
.variant-wisdom-brain::after {
  position: absolute;
  z-index: 0;
  border-radius: 50%;
  content: '';
  pointer-events: none;
}
.variant-wisdom-brain::before {
  inset: 12% 14%;
  border: 1px solid color-mix(in srgb, var(--widget-primary) 24%, transparent);
  background: radial-gradient(circle, color-mix(in srgb, var(--widget-primary) 16%, transparent), transparent 66%);
  box-shadow: 0 0 70px color-mix(in srgb, var(--widget-secondary) 18%, transparent);
  animation: wisdom-halo 7s ease-in-out infinite alternate;
}
.variant-wisdom-brain::after {
  inset: 20% 24%;
  border: 1px dashed color-mix(in srgb, var(--widget-primary) 48%, transparent);
  animation: orb-rotate 24s linear infinite;
}
.variant-wisdom-brain .material-image {
  object-fit: contain !important;
  mix-blend-mode: screen;
  filter:
    saturate(1.18)
    contrast(1.08)
    drop-shadow(0 0 18px color-mix(in srgb, var(--widget-primary) 46%, transparent))
    drop-shadow(0 18px 42px rgba(0, 0, 0, .72));
  animation: wisdom-float 6.8s ease-in-out infinite;
}

.generic-widget {
  display: grid;
  place-items: center;
  align-content: center;
  width: 100%;
  height: 100%;
  border: 1px solid rgba(91, 168, 240, .5);
  background: rgba(9, 41, 79, .78);
  color: #d7e8f7;
}

.generic-widget i { margin-bottom: 10px; color: #4a9eff; font-size: 30px; }
.generic-widget strong { font-size: 16px; }
.generic-widget span { margin-top: 6px; color: #7894af; font-size: 11px; }

.choice-tabs { display: flex; align-items: stretch; gap: 6px; width: 100%; height: 100%; padding: 6px; box-sizing: border-box; }
.choice-tabs button { flex: 1; min-width: 0; border: 1px solid rgba(91, 168, 240, .42); background: rgba(8, 35, 66, .72); color: #c9d8e6; cursor: pointer; }
.choice-tabs button.active { border-color: #67c5ff; color: #fff; box-shadow: 0 0 12px rgba(22, 136, 223, .45); }
.rich-text { width: 100%; height: 100%; padding: 8px; box-sizing: border-box; overflow: auto; }
.variant-reference-exact .rich-text { padding: 0; overflow: hidden; }
.media-video, .embed-frame { width: 100%; height: 100%; border: 0; object-fit: contain; background: #071423; }
.media-placeholder { display: grid; place-items: center; align-content: center; gap: 9px; width: 100%; height: 100%; border: 1px solid rgba(91, 168, 240, .4); background: rgba(7, 20, 35, .82); color: #89a9c7; }
.media-placeholder i { color: #4aa9ee; font-size: 28px; }

@keyframes table-scroll {
  from { transform: translateY(0); }
  to { transform: translateY(-55%); }
}

@keyframes border-run {
  to { stroke-dashoffset: -480; }
}

@keyframes panel-live { 50% { opacity: .35; transform: scale(.82); } }
@keyframes empty-spin { to { transform: rotate(360deg); } }
@keyframes ambient-drift {
  from { opacity: .46; transform: translate3d(-1.5%, 1%, 0) scale(.96); }
  to { opacity: .72; transform: translate3d(2%, -1.5%, 0) scale(1.04); }
}
@keyframes constellation-breathe { 50% { opacity: .85; transform: scale(1.8); } }
@keyframes orb-rotate { to { transform: rotate(360deg); } }
@keyframes ribbon-dash { to { stroke-dashoffset: -420; } }
@keyframes ribbon-pulse {
  from { stroke-dashoffset: 0; opacity: .18; }
  45% { opacity: .92; }
  to { stroke-dashoffset: -1228; opacity: .2; }
}
@keyframes ribbon-node {
  0%, 100% { opacity: .28; transform: scale(.72); }
  50% { opacity: 1; transform: scale(1.42); }
}
@keyframes wisdom-float {
  0%, 100% { transform: translate3d(0, 2px, 0) scale(.985); }
  50% { transform: translate3d(0, -7px, 0) scale(1.012); }
}
@keyframes wisdom-halo {
  from { opacity: .48; transform: scale(.96); }
  to { opacity: .86; transform: scale(1.035); }
}

@media (prefers-reduced-motion: reduce) {
  .panel-chrome *,
  .empty-orbit,
  .scroll-track,
  .ambient-glow,
  .data-constellation *,
  .flow-ribbon *,
  .focus-orb *,
  .variant-wisdom-brain *,
  .status-orbit::before { animation: none !important; }
}
</style>
