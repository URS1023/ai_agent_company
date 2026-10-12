/**
 * LynxTemplate19 大屏的组合式函数：把页面数据绑定成模板需要的指标/图表/面板结构。
 * 职责：解析 screen 快照里的组件配置，按需触发组件取数（内部复用 useWidgetData），
 *   计算指标卡数值、面板列表与聚合结果，供 LynxTemplate19* 系列组件渲染。
 * 关键约束：只读派生（computed），不直接写库；取数受 useWidgetData 的权限与刷新策略约束。
 * 错误暴露：每个绑定独立暴露 loading/error，单组件取数失败不影响整屏其它区域。
 */
import { computed, type ComputedRef, type Ref } from 'vue'
import { useWidgetData } from './useWidgetData'

type DashboardScreenRef = Ref<Record<string, any>> | ComputedRef<Record<string, any>>

interface TemplateBinding {
  widget: ComputedRef<Record<string, any>>
  rows: ComputedRef<Record<string, any>[]>
  loading: Ref<boolean>
  error: Ref<string>
}

export interface Template19Metric {
  label: string
  value: number
  display: string
  unit: string
  available: boolean
  icon: 'device' | 'alert' | 'money' | 'order' | 'status'
  tone: 'cyan' | 'blue' | 'amber'
}

export interface Template19RankRow {
  label: string
  value: number
  display: string
}

export interface Template19Point {
  label: string
  value: number
}

const SLOT_NAMES: Record<number, RegExp> = {
  1: /设备总数/,
  2: /预警数量|当前预警/,
  3: /最近预警/,
  4: /预警趋势/,
  5: /核心指标|销售总金额/,
  6: /核心趋势|销售金额趋势/,
  7: /设备问题|问题明细/,
  8: /销售订单数/,
  9: /设备状态分布/,
  10: /区域销售排行/,
  11: /设备类型分布/,
}

const missingWidgets = new Map<number, Record<string, any>>()

function missingWidget(slot: number) {
  if (!missingWidgets.has(slot)) {
    missingWidgets.set(slot, {
      id: `template-19-missing-${slot}`,
      componentName: '',
      config: { dataType: 1, chartDataParsed: [] },
    })
  }
  return missingWidgets.get(slot)!
}

function normalizeRows(value: unknown): Record<string, any>[] {
  if (Array.isArray(value)) return value.filter(item => item && typeof item === 'object')
  if (value && typeof value === 'object') return [value as Record<string, any>]
  return []
}

function configuredRows(widget: Record<string, any>) {
  const source = widget.config?.chartDataParsed ?? widget.config?.chartData ?? []
  if (typeof source !== 'string') return normalizeRows(source)
  try {
    return normalizeRows(JSON.parse(source || '[]'))
  } catch {
    return []
  }
}

function finiteNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value !== 'string') return null
  const normalized = value.replace(/[,%￥¥\s]/g, '')
  if (!/^-?\d+(?:\.\d+)?$/.test(normalized)) return null
  const parsed = Number(normalized)
  return Number.isFinite(parsed) ? parsed : null
}

function firstNumeric(row: Record<string, any> | undefined, preferred: string[] = []) {
  if (!row) return null
  for (const key of preferred) {
    const value = finiteNumber(row[key])
    if (value != null) return value
  }
  for (const value of Object.values(row)) {
    const parsed = finiteNumber(value)
    if (parsed != null) return parsed
  }
  return null
}

function firstText(row: Record<string, any>, preferred: string[], fallback: string) {
  for (const key of preferred) {
    const value = row[key]
    if (value != null && String(value).trim()) return String(value).trim()
  }
  const value = Object.values(row).find(item => item != null && finiteNumber(item) == null && String(item).trim())
  return value == null ? fallback : String(value).trim()
}

function bindSlot(screen: DashboardScreenRef, slot: number): TemplateBinding {
  const widget = computed(() => {
    const widgets = Array.isArray(screen.value.widgets) ? screen.value.widgets : []
    return widgets.find((item: Record<string, any>) => String(item.id || '').endsWith(`widget-${slot}`))
      || widgets.find((item: Record<string, any>) => SLOT_NAMES[slot]?.test(String(item.componentName || item.title || '')))
      || missingWidget(slot)
  })
  const state = useWidgetData(widget)
  const rows = computed(() => state.remote.value === null
    ? configuredRows(widget.value)
    : normalizeRows(state.remote.value))
  return { widget, rows, loading: state.loading, error: state.error }
}

export function formatTemplate19Number(value: number) {
  return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 1 }).format(value)
}

export function formatTemplate19Money(value: number) {
  if (Math.abs(value) >= 100000000) return `${(value / 100000000).toFixed(2)} 亿`
  if (Math.abs(value) >= 10000) return `${(value / 10000).toFixed(2)} 万`
  return formatTemplate19Number(value)
}

function metric(binding: TemplateBinding, options: {
  label: string
  unit: string
  keys: string[]
  icon: Template19Metric['icon']
  tone?: Template19Metric['tone']
  money?: boolean
}) {
  return computed<Template19Metric>(() => {
    const sourceValue = firstNumeric(binding.rows.value[0], options.keys)
    const available = sourceValue !== null
    const value = sourceValue ?? 0
    return {
      label: options.label,
      value,
      display: available
        ? (options.money ? formatTemplate19Money(value) : formatTemplate19Number(value))
        : '—',
      unit: available ? options.unit : '',
      available,
      icon: options.icon,
      tone: options.tone || 'cyan',
    }
  })
}

function rankingRows(rows: Record<string, any>[], labelKeys: string[], valueKeys: string[], money = false) {
  return rows.map((row, index) => {
    const value = firstNumeric(row, valueKeys)
    if (value === null) return null
    return {
      label: firstText(row, labelKeys, `对象 ${index + 1}`),
      value,
      display: money ? formatTemplate19Money(value) : formatTemplate19Number(value),
    }
  }).filter((row): row is Template19RankRow => row !== null)
    .sort((left, right) => right.value - left.value)
}

function lineRows(rows: Record<string, any>[], labelKeys: string[], valueKeys: string[]) {
  return rows.map((row, index) => {
    const value = firstNumeric(row, valueKeys)
    return value === null ? null : {
      label: firstText(row, labelKeys, `${index + 1}`),
      value,
    }
  }).filter((row): row is Template19Point => row !== null)
}

function displayDate(value: unknown) {
  const text = String(value ?? '').trim()
  if (!text) return '-'
  return text.replace('T', ' ').replace(/:\d{2}(?:\.\d+)?$/, '')
}

export function useTemplate19Dashboard(screen: DashboardScreenRef) {
  const deviceTotalBinding = bindSlot(screen, 1)
  const alertCountBinding = bindSlot(screen, 2)
  const recentAlertsBinding = bindSlot(screen, 3)
  const alertTrendBinding = bindSlot(screen, 4)
  const salesAmountBinding = bindSlot(screen, 5)
  const salesTrendBinding = bindSlot(screen, 6)
  const problemBinding = bindSlot(screen, 7)
  const orderCountBinding = bindSlot(screen, 8)
  const statusCountBinding = bindSlot(screen, 9)
  const regionSalesBinding = bindSlot(screen, 10)
  const deviceTypesBinding = bindSlot(screen, 11)

  const deviceTotal = metric(deviceTotalBinding, {
    label: '设备总数', unit: '台', keys: ['total', '数量'], icon: 'device', tone: 'cyan',
  })
  const alertCount = metric(alertCountBinding, {
    label: '当前预警', unit: '条', keys: ['alertCount', '数量'], icon: 'alert', tone: 'amber',
  })
  const salesAmount = metric(salesAmountBinding, {
    label: '销售总金额', unit: '元', keys: ['totalAmount', '销售金额', '金额'], icon: 'money', tone: 'blue', money: true,
  })
  const orderCount = metric(orderCountBinding, {
    label: '销售订单', unit: '单', keys: ['orderCount', '数量'], icon: 'order', tone: 'cyan',
  })
  const statusCount = metric(statusCountBinding, {
    label: '状态设备', unit: '台', keys: ['数量', 'total'], icon: 'status', tone: 'blue',
  })

  const alertRanking = computed<Template19RankRow[]>(() => rankingRows(
    recentAlertsBinding.rows.value,
    ['设备名称', 'deviceName', 'name'],
    ['预警级别', 'severity', 'level'],
  ))
  const regionRanking = computed<Template19RankRow[]>(() => rankingRows(
    regionSalesBinding.rows.value,
    ['区域', 'region', 'name'],
    ['销售金额', 'amount', 'value'],
    true,
  ))
  const salesTrend = computed<Template19Point[]>(() => lineRows(
    salesTrendBinding.rows.value,
    ['saleDay', '销售日期', '日期', 'name'],
    ['dailyAmount', '销售金额', 'amount', 'value'],
  ))
  const alertTrend = computed<Template19Point[]>(() => lineRows(
    alertTrendBinding.rows.value,
    ['alertDate', '预警日期', '日期', 'name'],
    ['alertCount', '预警数量', '数量', 'value'],
  ))
  const deviceTypes = computed<Template19RankRow[]>(() => rankingRows(
    deviceTypesBinding.rows.value,
    ['类型', '设备类型', 'type', 'name'],
    ['数量', 'count', 'value'],
  ).map(row => ({
    ...row,
    label: /^\d+(?:\.\d+)?$/.test(row.label) ? `类型 ${row.label}` : row.label,
  })))
  const problems = computed(() => problemBinding.rows.value.map((row, index) => ({
    id: String(row['设备编号'] ?? row.deviceCode ?? row.id ?? String(index + 1).padStart(4, '0')),
    name: String(row['设备名称'] ?? row.deviceName ?? row.name ?? '-'),
    level: String(row['预警级别'] ?? row.severity ?? row.level ?? '-'),
    reason: String(row['预警原因'] ?? row.reason ?? row.problem ?? '-'),
    time: displayDate(row['发生时间'] ?? row['预警时间'] ?? row.occurredAt ?? row.time),
  })))
  const operations = computed(() => [
    ...problems.value.map(row => ({
      id: `problem-${row.id}-${row.time}`,
      type: '设备预警',
      date: row.time,
      subject: row.name,
      metric: `L${row.level}`,
      detail: row.reason,
      source: 'problems' as const,
      rawValue: row.level,
    })),
    ...salesTrend.value.map((row, index) => ({
      id: `sales-${row.label}-${index}`,
      type: '销售日报',
      date: row.label,
      subject: '当日销售金额',
      metric: formatTemplate19Money(row.value),
      detail: '销售金额',
      source: 'salesTrend' as const,
      rawValue: row.value,
    })),
  ].sort((left, right) => String(right.date).localeCompare(String(left.date))))

  const statusNodes = computed(() => [
    { label: '设备总量', value: deviceTotal.value.display, tone: 'cyan' },
    { label: '当前预警', value: alertCount.value.display, tone: 'amber' },
    { label: '销售订单', value: orderCount.value.display, tone: 'blue' },
    { label: '覆盖区域', value: regionRanking.value.length ? String(regionRanking.value.length) : '—', tone: 'cyan' },
    { label: '设备类型', value: deviceTypes.value.length ? String(deviceTypes.value.length) : '—', tone: 'blue' },
    { label: '状态设备', value: statusCount.value.display, tone: 'cyan' },
  ])

  const bindings = [
    deviceTotalBinding, alertCountBinding, recentAlertsBinding, alertTrendBinding,
    salesAmountBinding, salesTrendBinding, problemBinding, orderCountBinding,
    statusCountBinding, regionSalesBinding, deviceTypesBinding,
  ]
  const loading = computed(() => bindings.some(binding => binding.loading.value))
  const errors = computed(() => bindings.map(binding => binding.error.value).filter(Boolean))
  const sourceWidgets = {
    alerts: recentAlertsBinding.widget,
    regions: regionSalesBinding.widget,
    salesTrend: salesTrendBinding.widget,
    problems: problemBinding.widget,
    deviceTypes: deviceTypesBinding.widget,
  }

  return {
    deviceTotal,
    alertCount,
    salesAmount,
    orderCount,
    statusCount,
    alertRanking,
    regionRanking,
    salesTrend,
    alertTrend,
    deviceTypes,
    problems,
    operations,
    statusNodes,
    loading,
    errors,
    sourceWidgets,
  }
}
