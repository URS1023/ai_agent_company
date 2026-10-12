/**
 * dashboardDrilldown —— 大屏图表下钻的请求构造、结果归一化与 CSV 导出（纯函数）。
 *
 * 位置：composables/useReportDrilldown 调用，服务于 JimuDrilldownPanel。
 * 关键约束：
 * - 下钻 SQL 是模板：命名参数（:point 等）必须由点击点的值填充；若模板声明了该参数却取不到值，
 *   必须标记 incomplete 并清空 SQL，绝不能带着未绑定参数去执行查询（会查全表或直接报错）；
 * - 点击值取值优先级：triggerField 对应字段 > point.name > 数组值的首项，与后端参数语义保持一致；
 * - CSV 导出必须防公式注入：以 = + - @ 开头的单元格前置单引号；同时加 BOM 与 CRLF，
 *   保证 Excel 打开不乱码；
 * - 所有函数纯函数、不改入参，chartData 允许是 JSON 字符串（解析失败按空数组处理）。
 */

/** 把 chartData 归一化为数组：支持数组与 JSON 字符串，非法输入返回空数组。 */
function parseRows(value) {
  if (Array.isArray(value)) return value
  if (typeof value === 'string' && value.trim()) {
    try {
      const parsed = JSON.parse(value)
      return Array.isArray(parsed) ? parsed : []
    } catch {
      return []
    }
  }
  return []
}

function widgetRows(widget = {}) {
  const config = widget.config || {}
  return parseRows(config.chartDataParsed)
    .concat(parseRows(config.chartDataParsed).length ? [] : parseRows(config.chartData))
}

function pointParameter(drilldown, point = {}) {
  const triggerField = drilldown.triggerField
  if (triggerField && point.data && typeof point.data === 'object' && !Array.isArray(point.data)) {
    const exact = point.data[triggerField]
    if (exact != null) return exact
  }
  if (point.name != null && point.name !== '') return point.name
  if (Array.isArray(point.value)) return point.value[0]
  return point.value
}

function hasNamedParameter(sql, name) {
  const escaped = String(name || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return escaped ? new RegExp(`(^|[^:]):${escaped}\\b`).test(String(sql || '')) : false
}

/**
 * 构造下钻请求参数。
 * @param {object} widget 被点击的组件
 * @param {object} point 点击点（含 name/value/data/fallbackRows）
 * @returns {{widgetId:any, datasourceId:any, title:string, sql:string, parameter:string,
 *   incomplete:boolean, sourceSql:string, parameters:object, triggerField:string, columns:Array,
 *   point:object, fallbackRows:Array}} incomplete 为真时 sql 已清空，调用方应提示用户而非执行
 */
export function createDrilldownRequest(widget = {}, point = {}) {
  const config = widget.config || {}
  const drilldown = config.drilldown || {}
  const parameter = drilldown.parameter || 'point'
  const value = pointParameter(drilldown, point)
  const templateSql = drilldown.sql || ''
  const parameters = templateSql && value != null && value !== ''
    ? { [parameter]: value }
    : {}
  const incomplete = hasNamedParameter(templateSql, parameter) && !Object.hasOwn(parameters, parameter)
  const sql = incomplete
    ? ''
    : templateSql
  const fallbackRows = Object.hasOwn(point, 'fallbackRows')
    ? parseRows(point.fallbackRows)
    : widgetRows(widget)

  return {
    widgetId: widget.id,
    datasourceId: drilldown.datasourceId || config.datasourceId || null,
    title: drilldown.title || `${widget.componentName || '组件'} · 数据明细`,
    sql,
    parameter,
    incomplete,
    sourceSql: config.sql || '',
    parameters,
    triggerField: drilldown.triggerField || '',
    columns: Array.isArray(drilldown.columns) ? drilldown.columns : [],
    point: {
      name: point.name, value: point.value,
      data: point.data && typeof point.data === 'object' ? point.data : undefined,
    },
    fallbackRows
  }
}

/**
 * 归一化下钻结果为 { headers, rows }：显式列配置优先，其次按数据键出现顺序补齐；
 * 数组行（无字段名）按位置映射到 headers，并生成"字段N"占位列名。
 * @returns {{headers: Array<{key:string,label:string}>, rows: Array<object>}}
 */
export function normalizeDrilldownRows(rows = [], option = {}) {
  const sourceRows = Array.isArray(rows) ? rows : []
  const configured = Array.isArray(option.columns) ? option.columns : []
  const headers = []
  const seen = new Set()

  for (const column of configured) {
    const item = typeof column === 'string'
      ? { key: column, label: column }
      : {
          key: column.key || column.field || column.columnName || column.label,
          label: column.label || column.comment || column.columnName || column.key || column.field
        }
    if (!item.key || seen.has(item.key)) continue
    seen.add(item.key)
    headers.push(item)
  }

  for (const row of sourceRows) {
    if (!row || typeof row !== 'object' || Array.isArray(row)) continue
    for (const key of Object.keys(row)) {
      if (seen.has(key)) continue
      seen.add(key)
      headers.push({ key, label: key })
    }
  }

  if (!headers.length) {
    const width = sourceRows.reduce((max, row) => Math.max(max, Array.isArray(row) ? row.length : 0), 0)
    for (let index = 0; index < width; index += 1) headers.push({ key: String(index), label: `字段${index + 1}` })
  }

  const normalizedRows = sourceRows.map(row => {
    if (!Array.isArray(row)) return { ...(row || {}) }
    return Object.fromEntries(headers.map((header, index) => [header.key, row[index] ?? '']))
  })
  return { headers, rows: normalizedRows }
}

function csvCell(value) {
  let text = value == null ? '' : String(value)
  if (/^[\u0000-\u0020]*[=+\-@]/.test(text)) {
    text = `'${text}`
  }
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
}

/**
 * 生成 CSV 文本（含 BOM 与 CRLF）。以 = + - @ 开头的值前置单引号，避免被 Excel 当公式执行。
 * @returns {string} 可直接下载的 CSV 内容
 */
export function rowsToCsv(headers = [], rows = []) {
  const columns = headers.map(header => ({
    key: typeof header === 'string' ? header : header.key,
    label: typeof header === 'string' ? header : (header.label || header.key)
  }))
  const lines = [
    columns.map(column => csvCell(column.label)).join(','),
    ...rows.map(row => columns.map(column => csvCell(row?.[column.key])).join(','))
  ]
  return `\uFEFF${lines.join('\r\n')}`
}
