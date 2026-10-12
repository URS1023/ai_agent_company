/**
 * safeContent —— 浏览器侧的内容安全边界（与后端校验互补，不能互相替代）。
 *
 * 位置：大屏资源地址（背景图/图片组件）、ECharts option 输出前的统一把关。
 * 关键约束：
 * - 资源地址只允许 http/https 与受控的 data:image 内联图；带用户名密码、协议相对（//）、
 *   含反斜杠或控制字符的地址一律拒绝（返回空串，由调用方决定不渲染），防开放重定向与协议走私；
 * - safeChartOption 会把 tooltip 强制为 richText 渲染模式：HTML 格式化器会把不可信数据库字符串
 *   插值进 DOM，是真实的 XSS 面；
 * - safeChartOption 会就地规范化 title（字符串 → { text }），因为 ECharts 合并坐标轴时会因类型不符崩溃。
 */

/** Browser-side content boundaries complement server validation. */
export function safeAssetUrl(value: unknown): string {
  const raw = String(value || '').trim()
  const compact = raw.replace(/[\u0000-\u0020]/g, '')
  if (!raw || compact.includes('\\') || compact.startsWith('//')) return ''
  if (/^data:image\/(?:png|jpeg|webp|gif);base64,/i.test(compact)) return raw
  try {
    const url = new URL(compact, location.origin)
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) return ''
    return raw
  } catch { return '' }
}

/**
 * 把抓取下来的积木素材地址解析到 analytics 应用的 Vite base 下（仅处理 /jimu-screen/ 前缀）。
 * 先经 safeAssetUrl 校验，非法地址原样返回空串。
 */

/** Resolve captured Jimu assets under the analytics application's Vite base. */
export function scopedAssetUrl(value: unknown, baseUrl = ''): string {
  const safe = safeAssetUrl(value)
  if (!safe || !baseUrl || !safe.startsWith('/jimu-screen/')) return safe
  return `${baseUrl.replace(/\/?$/, '/')}${safe.slice(1)}`
}

/**
 * 规范化并加固 ECharts option（就地修改并返回同一对象）：
 * - title 非对象时统一转成 { text } 或删除，避免 ECharts 合并坐标轴时崩溃；
 * - tooltip（含 series 级）强制 renderMode: 'richText'，防止 HTML 格式化器插值不可信数据导致 XSS。
 * 注意：本函数不做主题美化，也不校验数据内容。
 */
export function safeChartOption(option: Record<string, any>) {
  // ECharts component options must be objects (or arrays of objects). Model-
  // generated drafts created before server normalization may contain a plain
  // title string, which otherwise crashes ECharts while merging coordinates.
  if (typeof option.title === 'string' || typeof option.title === 'number') {
    option.title = { text: String(option.title) }
  } else if (Array.isArray(option.title)) {
    option.title = option.title
      .map(item => typeof item === 'string' || typeof item === 'number'
        ? { text: String(item) }
        : item)
      .filter(item => item && typeof item === 'object' && !Array.isArray(item))
  } else if (option.title != null && typeof option.title !== 'object') {
    delete option.title
  }
  // ECharts HTML tooltip formatters can interpolate untrusted database strings.
  const tooltip = option.tooltip && typeof option.tooltip === 'object' && !Array.isArray(option.tooltip)
    ? option.tooltip
    : {}
  option.tooltip = { ...tooltip, renderMode: 'richText' }
  for (const series of Array.isArray(option.series) ? option.series : [option.series]) {
    if (series?.tooltip) series.tooltip = { ...series.tooltip, renderMode: 'richText' }
  }
  return option
}
