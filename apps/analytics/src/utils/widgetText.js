/**
 * widgetText —— 文本类组件（JText / JDragEditor）配置的归一化。
 *
 * 位置：JimuWidgetRenderer 与 JimuPropertyPanel 读取文本组件样式时调用。
 * 关键约束：
 * - 历史数据把文本存在多处（config.text / option.text / widget.content / widget.text），
 *   归一化后必须删除已消费的别名键，否则后续显式编辑会被旧值"复活"；
 * - AI 生成时把组件名写进了 body.text 作为占位，若用户未编辑过（config.textEdited 为假），
 *   首次归一化要用真实文本替换该占位；
 * - 扁平样式键（color/fontSize/...）统一收敛到 option.body，并删除扁平副本，保持单一来源；
 * - 传入 edit.text 表示这是一次用户编辑，需要置 config.textEdited 以保护后续不被覆盖。
 */
const object = value => value && typeof value === 'object' && !Array.isArray(value) ? value : {}
const scalar = value => typeof value === 'string' || typeof value === 'number'
const styleKeys = ['color', 'fontSize', 'fontFamily', 'fontWeight', 'letterSpacing', 'textAlign', 'lineHeight', 'textShadow']

/** 是否为文本类组件（决定是否走文本归一化与专用属性面板）。 */
export const isTextWidget = widget => ['JText', 'JDragEditor'].includes(widget?.component)

/** Read legacy AI text once into the same contract used by the editor. */
export function normalizeTextConfig(widget = {}, edit = {}) {
  const config = { ...object(widget.config) }
  if (!isTextWidget(widget)) return config
  const option = { ...object(config.option ?? widget.echartsOption) }
  const body = { ...object(option.body) }
  const style = object(widget.style)
  const legacyText = [config.text, option.text, widget.content, widget.text].find(scalar)
  // Older adapters copied the layer name into body.text and ignored config.text.
  // Remove consumed aliases below so future explicit edits cannot be resurrected.
  const oldAiPlaceholder = !config.textEdited && widget.visualMode === 'ai' && body.text === widget.componentName
    && scalar(legacyText) && legacyText !== body.text
  body.text = scalar(body.text) && !oldAiPlaceholder
    ? body.text
    : (legacyText ?? widget.componentName ?? widget.title ?? '')
  for (const key of styleKeys) {
    const flatValue = key === 'color' ? (config.fontColor ?? config.color) : config[key]
    body[key] = body[key] ?? flatValue ?? option[key] ?? style[key]
    delete config[key]
    delete option[key]
  }
  delete config.text
  delete config.fontColor
  delete option.text
  if (Object.prototype.hasOwnProperty.call(edit, 'text')) config.textEdited = true
  return { ...config, option: { ...option, body: { ...body, ...edit } } }
}
