/**
 * metricData —— 指标类组件的数据行推导与结果字段枚举。
 *
 * 位置：JimuWidgetRenderer（指标卡渲染）、属性面板的结果字段下拉。
 * 关键约束：
 * - 指标值必须唯一可判定：返回多个数值字段或无法确定时返回中文 error 让用户显式选择字段，
 *   绝不"取第一个"糊过去（错指标比不显示更糟）；
 * - 数值判定同时接受 number 与纯数字字符串（后端可能以字符串返回精确小数）；
 * - 字段映射（dataMapping/valueField/labelField）缺失的字段必须报错，而不是静默取默认列。
 */

/** 数值判定：接受有限 number 与可解析为数字的字符串（后端可能返回精确小数字符串）。 */
const own = (row, key) => Object.prototype.hasOwnProperty.call(row, key)
const numeric = value => (typeof value === 'number' && Number.isFinite(value))
  || (typeof value === 'string' && /^-?\d+(?:\.\d+)?$/.test(value.trim()))

/**
 * 推导指标行。
 * @param {Array} data 查询结果行
 * @param {object} config 组件配置（含 dataMapping/valueField/labelField）
 * @returns {{rows: Array<{name: string, value: any}>, error: string}} error 非空时 rows 为空，调用方需展示该文案
 */
export function metricRows(data, config = {}) {
  if (!Array.isArray(data)) return { rows: [], error: '' }
  const mapping = config.snapshotBinding || config.dataMapping || {}
  const valueField = mapping.valueField || config.valueField || ''
  const labelField = mapping.labelField || config.labelField || ''
  const rows = []
  for (const row of data) {
    if (!row || typeof row !== 'object' || Array.isArray(row)) {
      if (valueField || labelField) return { rows: [], error: '当前结果不是字段记录，请检查字段映射' }
      rows.push({ value: row })
      continue
    }
    if (valueField && !own(row, valueField)) return { rows: [], error: `结果缺少数值字段“${valueField}”，请重新选择` }
    if (labelField && !own(row, labelField)) return { rows: [], error: `结果缺少维度字段“${labelField}”，请重新选择` }
    let chosen = valueField || (own(row, 'value') ? 'value' : '')
    if (!chosen) {
      const fields = Object.keys(row).filter(key => key !== labelField && !['id', 'name', 'unit', 'suffix'].includes(key))
      const numbers = fields.filter(key => numeric(row[key]))
      if (numbers.length > 1) return { rows: [], error: '返回多个数值字段，请在数据配置中选择数值字段' }
      chosen = numbers[0] || (fields.length === 1 ? fields[0] : '')
      if (!chosen && fields.length > 1) return { rows: [], error: '无法确定指标字段，请在数据配置中选择数值字段' }
    }
    rows.push({ ...row, value: chosen ? row[chosen] : null, name: labelField ? row[labelField] : row.name })
  }
  return { rows, error: '' }
}

/**
 * 枚举组件结果字段（用于字段映射下拉）：显式 resultColumns 优先，否则取前 50 行的键并集。
 * chartData 可能是 JSON 字符串，解析失败返回空数组而不抛错。
 */
export function widgetResultFields(config = {}) {
  if (Array.isArray(config.resultColumns) && config.resultColumns.length) return config.resultColumns
  let rows = config.chartDataParsed ?? config.chartData
  if (typeof rows === 'string') { try { rows = JSON.parse(rows) } catch { return [] } }
  if (!Array.isArray(rows)) return []
  return [...new Set(rows.slice(0, 50).flatMap(row => row && typeof row === 'object' && !Array.isArray(row) ? Object.keys(row) : []))]
}
