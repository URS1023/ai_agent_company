/**
 * widgetSizing —— 大屏组件默认尺寸（组件库加入画布与 AI 生成落图时使用）。
 *
 * 关键约束：
 * - 450×300 是历史占位尺寸（旧模板里所有组件都用它），必须视为"未指定"而不是真实尺寸，
 *   否则新组件会全部挤成同一大小；
 * - 尺寸下限（宽 ≥80、高 ≥36）用于保证组件仍可被点选与拖动；
 * - 两套默认表分别服务组件库（resolveWidgetSize）与 AI 生成（GENERATED_DEFAULTS），
 *   调整其一需确认另一处是否需要同步，避免同一组件在两条路径上尺寸差异过大。
 */

/**
 * 组件库条目的默认尺寸。
 * @param {{width?: number, height?: number, compType?: string}} item 组件库条目
 * @returns {{width: number, height: number}} 画布像素尺寸
 */
export function resolveWidgetSize(item = {}) {
  const explicitWidth = Number(item.width)
  const explicitHeight = Number(item.height)
  const isLegacyPlaceholder = explicitWidth === 450 && explicitHeight === 300
  if (!isLegacyPlaceholder && (explicitWidth > 0 || explicitHeight > 0)) {
    return {
      width: Math.max(80, explicitWidth || 450),
      height: Math.max(36, explicitHeight || 300)
    }
  }

  const type = String(item.compType || '')
  if (/CurrentTime/i.test(type)) return { width: 300, height: 56 }
  if (/Text|Title/i.test(type)) return { width: 560, height: 72 }
  if (/StatsSummary|CountTo|Number/i.test(type)) return { width: 300, height: 116 }
  if (/Table|ScrollBoard|RankingBoard|List/i.test(type)) return { width: 620, height: 320 }
  if (/Map|Earth/i.test(type)) return { width: 720, height: 520 }
  if (/Border/i.test(type)) return { width: 760, height: 420 }
  if (/Decoration/i.test(type)) return { width: 620, height: 72 }
  if (/Gauge|Pie|Ring|Radar/i.test(type)) return { width: 420, height: 340 }
  if (/Video|Iframe/i.test(type)) return { width: 640, height: 360 }
  return { width: 560, height: 340 }
}

const GENERATED_DEFAULTS = [
  [/StatsSummary|CountTo|Number/i, { w: 300, h: 112 }],
  [/CurrentTime/i, { w: 286, h: 56 }],
  [/Text|Title/i, { w: 720, h: 60 }],
  [/Table|ScrollBoard|RankingBoard|List/i, { w: 620, h: 300 }],
  [/Map|Earth/i, { w: 720, h: 500 }],
  [/Border/i, { w: 760, h: 420 }],
  [/Decoration/i, { w: 620, h: 72 }],
  [/Gauge|Pie|Ring|Radar/i, { w: 420, h: 340 }],
  [/Video|Iframe/i, { w: 640, h: 360 }]
]

function generatedDefault(component) {
  return GENERATED_DEFAULTS.find(([pattern]) => pattern.test(component))?.[1] || { w: 560, h: 320 }
}

/**
 * AI 生成组件的尺寸：优先取模型给出的 w/h，缺失时按组件类型回退 GENERATED_DEFAULTS。
 * @returns {{w: number, h: number}}
 */
export function resolveGeneratedWidgetSize(component = '', widget = {}, position = {}) {
  const width = Number(widget.w ?? position.width)
  const height = Number(widget.h ?? position.height)
  const fallback = generatedDefault(String(component))

  if (!(width > 0) || !(height > 0)) {
    return { ...fallback }
  }

  return {
    w: width,
    h: height
  }
}
