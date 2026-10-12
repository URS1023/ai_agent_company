/**
 * dashboardData —— 大屏图表/表格的数据归一化与 option 清洗（纯函数）。
 *
 * 位置：JimuChart、JimuWidgetRenderer、aiChartOption 共用的数据处理层。
 * 关键约束：
 * - 显式表头/字段名优先于按列顺序猜测：只有显式键在数据里不存在时才尝试去掉单位后缀匹配，
 *   绝不允许用列顺序推断字段（错列比缺列更危险）；
 * - 模板里的旧字段名不能遮盖真实查询结果列；
 * - sanitizeChartOption 负责剔除来自模型或历史数据的危险 option 字段，输出前必须过一遍；
 * - 空值展示统一经 cleanDisplayValue，不要在各组件里自行拼"空"文案。
 */

import { chartPoint, parseChartDecimal } from './chartPrecision.js'

/** 展示层文本清理：还原被转义的换行/制表符并去首尾空白。 */
function cleanText(value) {
  if (value == null) return ''
  return String(value)
    .replace(/\\n/g, '\n')
    .replace(/\\t/g, ' ')
    .trim()
}

function normalizeHeader(header) {
  if (typeof header === 'string') return { key: header, label: header }
  if (!header || typeof header !== 'object') return null
  const key = header.key || header.prop || header.field || header.columnName || header.label
  if (!key) return null
  return { ...header, key, label: header.label || header.title || key }
}

/**
 * 推导表格表头：显式表头 > 维度定义 > 数据实际键。
 * 显式键缺失时会尝试去掉括号单位后缀再匹配（单位后缀不属于查询字段别名），
 * 但仍以显式键为准，不按列顺序猜字段。
 */
export function deriveTableHeaders(rows = [], explicitHeaders = [], dimensions = [], options = {}) {
  const available = new Set(rows.flatMap(row => row && typeof row === 'object' && !Array.isArray(row) ? Object.keys(row) : []))
  const resolveHeader = header => {
    const normalized = normalizeHeader(header)
    if (!normalized || typeof header !== 'string' || available.has(normalized.key)) return normalized
    // A display-only unit suffix is not part of the query's field alias.
    // Explicit object keys remain authoritative; never guess by column order.
    const key = header.replace(/[（(][^（）()]*[）)]\s*$/u, '').trim()
    return available.has(key) ? { ...normalized, key } : normalized
  }
  const explicit = Array.isArray(explicitHeaders)
    ? explicitHeaders.map(resolveHeader).filter(Boolean)
    : []
  // A template's old field names cannot hide actual verified query rows.
  if (explicit.length && (!options.actualFieldsOnly || !available.size || explicit.every(header => available.has(header.key)))) return explicit

  const dimensionHeaders = Array.isArray(dimensions)
    ? dimensions.map(normalizeHeader).filter(Boolean)
    : []
  if (dimensionHeaders.length && (!options.actualFieldsOnly || !available.size || dimensionHeaders.every(header => available.has(header.key)))) return dimensionHeaders

  const keys = []
  for (const row of Array.isArray(rows) ? rows : []) {
    if (!row || typeof row !== 'object' || Array.isArray(row)) continue
    for (const key of Object.keys(row)) {
      if (!keys.includes(key)) keys.push(key)
    }
  }
  return keys.map(key => ({ key, label: key }))
}

function numericValue(value) {
  return parseChartDecimal(value)
}

export function normalizeSeriesRows(rows = [], mapping = {}) {
  if (!Array.isArray(rows)) return []
  return rows.map((row, index) => {
    if (Array.isArray(row)) {
      const value = row.slice(1).find(item => numericValue(item) != null) ?? null
      return { label: cleanText(row[0] ?? `${index + 1}`), ...chartPoint(value) }
    }
    if (!row || typeof row !== 'object') {
      return { label: `${index + 1}`, ...chartPoint(row) }
    }
    const entries = Object.entries(row)
    const labelEntry = entries.find(([, value]) => value != null && numericValue(value) == null)
    const valueEntry = entries.find(([, value]) => numericValue(value) != null)
    return {
      label: cleanText((mapping.labelField ? row[mapping.labelField] : labelEntry?.[1]) ?? row.name ?? row.label ?? `${index + 1}`),
      ...chartPoint(mapping.valueField ? row[mapping.valueField] : valueEntry?.[1] ?? row.value)
    }
  })
}

export function inferSeriesSchema(rows = [], mapping = {}) {
  if (!Array.isArray(rows) || !rows.length) {
    return { labelKey: '', labels: [], numericKeys: [], series: [] }
  }
  const keys = []
  for (const row of rows.slice(0, 12)) {
    if (!row || typeof row !== 'object' || Array.isArray(row)) continue
    for (const key of Object.keys(row)) {
      if (!keys.includes(key)) keys.push(key)
    }
  }
  const numericKeys = mapping.valueField ? [mapping.valueField] : keys
    .filter(key => rows.some(row => numericValue(row?.[key]) != null))
  const labelKey = mapping.labelField || keys.find(key => !numericKeys.includes(key)
    && rows.some(row => row?.[key] != null && numericValue(row[key]) == null)) || ''
  const labels = rows.map((row, index) => cleanText(row?.[labelKey] ?? `${index + 1}`))
  const series = numericKeys.map(key => ({
    name: key,
    data: rows.map(row => chartPoint(row?.[key]).value),
    exactValues: rows.map(row => chartPoint(row?.[key]).exactValue),
    approximateValues: rows.map(row => chartPoint(row?.[key]).approximate)
  }))
  return { labelKey, labels, numericKeys, series }
}

const CHINA_REGION_NAMES = [
  '北京', '天津', '上海', '重庆', '河北', '山西', '辽宁', '吉林', '黑龙江',
  '江苏', '浙江', '安徽', '福建', '江西', '山东', '河南', '湖北', '湖南',
  '广东', '海南', '四川', '贵州', '云南', '陕西', '甘肃', '青海', '台湾',
  '内蒙古', '广西', '西藏', '宁夏', '新疆', '香港', '澳门'
]

export function normalizeChinaRegionLabel(value) {
  const label = cleanText(value).replace(/^中华人民共和国/, '')
  if (!label) return ''
  const known = CHINA_REGION_NAMES.find(region => label.startsWith(region))
  if (known) return known
  return label
    .split(/[，,/]/)[0]
    .replace(/(?:壮族|回族|维吾尔)?自治区$/, '')
    .replace(/特别行政区$/, '')
    .replace(/[省市]$/, '')
}

export function toMapSeriesData(rows = []) {
  return normalizeSeriesRows(rows)
    .map(item => ({ ...item, name: normalizeChinaRegionLabel(item.label) }))
    .filter(item => CHINA_REGION_NAMES.includes(item.name))
}

export function sanitizeChartOption(value) {
  if (typeof value === 'string') {
    return value.replace(/\\n/g, '\n').replace(/\\t/g, ' ')
  }
  if (Array.isArray(value)) return value.map(sanitizeChartOption)
  if (!value || typeof value !== 'object') return value
  return Object.fromEntries(
    Object.entries(value).map(([key, item]) => [key, sanitizeChartOption(item)])
  )
}

export function cleanDisplayValue(value) {
  return cleanText(value)
}
