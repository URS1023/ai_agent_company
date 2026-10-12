/*
 * 中文说明（组件文本配置契约的纯前端单元测试）：
 * 验证行为：normalizeTextConfig 对 AI 生成文本配置的归一（字号/颜色/对齐等）保持契约稳定。
 * 运行方式：node --test tests/ui/widget_text_contract.mjs，依赖
 *   apps/analytics/src/utils/widgetText.js。
 * 边界：纯函数测试，不连后端、不需要浏览器。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { normalizeTextConfig } from '../../apps/analytics/src/utils/widgetText.js'

const generated = () => ({ component: 'JText', componentName: '标题', visualMode: 'ai', config: {
  text: '销售金额地区分布大屏', fontSize: 36, fontColor: '#00e5ff', textAlign: 'left'
} })

test('flat AI text and font settings are readable through the editor contract without mutating input', () => {
  const widget = generated()
  const before = structuredClone(widget)
  const config = normalizeTextConfig(widget)
  assert.equal(config.option.body.text, '销售金额地区分布大屏')
  assert.equal(config.option.body.fontSize, 36)
  assert.equal(config.option.body.color, '#00e5ff')
  assert.equal(config.option.body.textAlign, 'left')
  assert.equal('text' in config, false)
  assert.deepEqual(widget, before)
})

test('repair old adapter placeholder once, then allow explicit layer-name text', () => {
  const widget = generated()
  widget.config.option = { body: { text: '标题' } }
  const repaired = normalizeTextConfig(widget)
  assert.equal(repaired.option.body.text, '销售金额地区分布大屏')
  const edited = normalizeTextConfig({ ...widget, config: repaired }, { text: '标题' })
  assert.equal(normalizeTextConfig({ ...widget, config: JSON.parse(JSON.stringify(edited)) }).option.body.text, '标题')
  assert.equal(edited.textEdited, true)
})

for (const text of ['', '  ', 0, '第一行\n第二行', '用户自己的标题']) {
  test(`explicit canonical text survives legacy aliases and save/reload: ${JSON.stringify(text)}`, () => {
    const widget = generated()
    widget.config.option = { body: { text } }
    const config = normalizeTextConfig(widget)
    assert.equal(config.option.body.text, text)
    assert.equal(normalizeTextConfig({ ...widget, config: JSON.parse(JSON.stringify(config)) }).option.body.text, text)
  })
}

test('native body styles win, and optional flat styles fill only missing properties', () => {
  const widget = generated()
  widget.config.option = { body: { text: '实际正文', fontSize: 18, color: '#ffffff', letterSpacing: 0 } }
  const body = normalizeTextConfig(widget).option.body
  assert.equal(body.fontSize, 18)
  assert.equal(body.color, '#ffffff')
  assert.equal(body.textAlign, 'left')
  assert.equal(body.letterSpacing, 0)
})

test('rich text and legacy content use the same contract; explicit blank remains blank', () => {
  const widget = { component: 'JDragEditor', componentName: '说明', content: '<b>口径说明</b>' }
  assert.equal(normalizeTextConfig(widget).option.body.text, widget.content)
  assert.equal(normalizeTextConfig(widget, { text: '' }).option.body.text, '')
  assert.equal(normalizeTextConfig({ component: 'JText', echartsOption: { body: { text: '导入正文' } } }).option.body.text, '导入正文')
})

test('chart configuration is untouched by text compatibility', () => {
  const widget = { component: 'JBar', config: { text: 'chart metadata', color: '#eee', option: { series: [] } } }
  assert.deepEqual(normalizeTextConfig(widget), widget.config)
})
