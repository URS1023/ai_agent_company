/*
 * 中文说明（指标数据映射的纯前端单元测试）：
 * 验证行为：metricRows 在给定 dataMapping 时按配置字段取值（配置的比值字段优先于首个数值列），
 *   并覆盖常规取值与边界情况。
 * 运行方式：node --test tests/ui/metric_data_mapping.mjs（依赖 apps/analytics/src/utils/metricData.js）。
 * 边界：纯函数测试，不访问后端、不连数据库，也不需要浏览器。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { metricRows, widgetResultFields } from '../../apps/analytics/src/utils/metricData.js'

test('configured ratio wins over first numeric column and conventional value', () => {
  const result = metricRows([{ value: 27, count: 27, ratio: '88.2500', region: '华东' }], { dataMapping: { valueField: 'ratio', labelField: 'region' } })
  assert.equal(result.error, '')
  assert.equal(result.rows[0].value, '88.2500')
  assert.equal(result.rows[0].name, '华东')
})
test('missing mapped fields never silently select a different metric', () => {
  assert.match(metricRows([{ count: 27 }], { dataMapping: { valueField: 'ratio' } }).error, /缺少数值字段/)
  assert.match(metricRows([{ value: 27 }], { dataMapping: { labelField: 'region' } }).error, /缺少维度字段/)
})
test('null mapped value stays null, zero is preserved', () => {
  const { rows, error } = metricRows([{ count: 27, ratio: null }, { count: 27, ratio: 0 }], { dataMapping: { valueField: 'ratio' } })
  assert.equal(error, '')
  assert.deepEqual(rows.map(row => row.value), [null, 0])
})
test('ambiguous automatic mapping asks for a choice', () => {
  const result = metricRows([{ count: 27, ratio: '88.25' }])
  assert.match(result.error, /多个数值字段/)
  assert.equal(result.rows.length, 0)
})
test('simple metrics and legacy configured fields remain usable', () => {
  assert.equal(metricRows([{ id: 321, count: 27 }]).rows[0].value, 27)
  assert.equal(metricRows([{ value: '99999999999999999.99', name: '金额' }]).rows[0].value, '99999999999999999.99')
  assert.equal(metricRows([{ count: 27, ratio: 88.25 }], { valueField: 'ratio' }).rows[0].value, 88.25)
  assert.deepEqual(metricRows([0, 27]).rows.map(row => row.value), [0, 27])
})
test('mapping controls work from saved query rows without repeating the query', () => {
  assert.deepEqual(widgetResultFields({ chartData: '[{"count":27,"ratio":88.25}]' }), ['count', 'ratio'])
  assert.deepEqual(widgetResultFields({ resultColumns: [], chartDataParsed: [{ first: 1 }, { second: 2 }] }), ['first', 'second'])
  assert.deepEqual(widgetResultFields({ resultColumns: ['verified'], chartData: '[{"old":0}]' }), ['verified'])
  assert.deepEqual(widgetResultFields({ chartData: '{broken' }), [])
})
