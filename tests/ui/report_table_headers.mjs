/*
 * 中文说明（报表表头推导的纯前端单元测试）：
 * 验证行为：deriveTableHeaders 的列名与单位后缀规则（展示单位后缀不能隐藏真实返回金额，0 值保留）。
 * 运行方式：node --test tests/ui/report_table_headers.mjs，依赖 apps/analytics/src/utils/dashboardData.js。
 * 边界：纯函数测试，不访问后端。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { deriveTableHeaders } from '../../apps/analytics/src/utils/dashboardData.js'

test('display unit suffix does not hide actual returned amounts', () => {
  const rows = [{ 客户名称: '客户甲', 应收金额: 120 }, { 客户名称: '客户乙', 应收金额: 0 }]
  const headers = deriveTableHeaders(rows, ['客户名称', '应收金额（元）'])
  assert.equal(headers[1].key, '应收金额')
  assert.equal(headers[1].label, '应收金额（元）')
  assert.deepEqual(rows.map(row => row[headers[1].key]), [120, 0])
})

test('explicit keys and exact aliases take precedence over labels', () => {
  const rows = [{ 应收金额: 120, '应收金额（元）': 80 }]
  assert.equal(deriveTableHeaders(rows, ['应收金额（元）'])[0].key, '应收金额（元）')
  assert.equal(deriveTableHeaders(rows, [{ key: 'missing', label: '应收金额（元）' }])[0].key, 'missing')
  assert.equal(deriveTableHeaders(rows, ['利润'])[0].key, '利润')
})

test('verified replacement binding renders real fields instead of empty template columns', () => {
  const rows = [{payee:'收款单位甲', amount:120}]
  const headers = deriveTableHeaders(rows, [{key:'old',label:'模板项目'}], ['old'], {actualFieldsOnly:true})
  assert.deepEqual(headers.map(header=>header.key), ['payee','amount'])
  assert.deepEqual(headers.map(header=>rows[0][header.key]), ['收款单位甲',120])
})
