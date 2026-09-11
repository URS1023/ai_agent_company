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
  const key = header.key || header.prop || header.field || header.label
  if (!key) return null
  return { ...header, key, label: header.label || header.title || key }
}

export function deriveTableHeaders(rows = [], explicitHeaders = [], dimensions = []) {
  const explicit = Array.isArray(explicitHeaders)
    ? explicitHeaders.map(normalizeHeader).filter(Boolean)
    : []
  if (explicit.length) return explicit.slice(0, 6)

  const dimensionHeaders = Array.isArray(dimensions)
    ? dimensions.map(normalizeHeader).filter(Boolean)
    : []
  if (dimensionHeaders.length) return dimensionHeaders.slice(0, 6)

  const keys = []
  for (const row of Array.isArray(rows) ? rows.slice(0, 8) : []) {
    if (!row || typeof row !== 'object' || Array.isArray(row)) continue
    for (const key of Object.keys(row)) {
      if (!keys.includes(key)) keys.push(key)
      if (keys.length >= 6) break
    }
    if (keys.length >= 6) break
  }
  return keys.map(key => ({ key, label: key }))
}

function numericValue(value) {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value !== 'string' || !value.trim()) return null
  const normalized = value.replace(/,/g, '').trim()
  if (!/^-?\d+(?:\.\d+)?$/.test(normalized)) return null
  const parsed = Number(normalized)
  return Number.isFinite(parsed) ? parsed : null
}

export function normalizeSeriesRows(rows = []) {
  if (!Array.isArray(rows)) return []
  return rows.map((row, index) => {
    if (Array.isArray(row)) {
      const value = row.slice(1).map(numericValue).find(item => item != null) ?? 0
      return { label: cleanText(row[0] ?? `${index + 1}`), value }
    }
    if (!row || typeof row !== 'object') {
      return { label: `${index + 1}`, value: numericValue(row) ?? 0 }
    }
    const entries = Object.entries(row)
    const labelEntry = entries.find(([, value]) => value != null && numericValue(value) == null)
    const valueEntry = entries.find(([, value]) => numericValue(value) != null)
    return {
      label: cleanText(labelEntry?.[1] ?? row.name ?? row.label ?? `${index + 1}`),
      value: numericValue(valueEntry?.[1] ?? row.value) ?? 0
    }
  })
}

export function inferSeriesSchema(rows = []) {
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
  const numericKeys = keys
    .filter(key => rows.some(row => numericValue(row?.[key]) != null))
    .slice(0, 3)
  const labelKey = keys.find(key => !numericKeys.includes(key)
    && rows.some(row => row?.[key] != null && numericValue(row[key]) == null)) || ''
  const labels = rows.map((row, index) => cleanText(row?.[labelKey] ?? `${index + 1}`))
  const series = numericKeys.map(key => ({
    name: key,
    data: rows.map(row => numericValue(row?.[key]) ?? 0)
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
    .map(item => ({ name: normalizeChinaRegionLabel(item.label), value: item.value }))
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
