import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import { createRequire } from 'node:module'
import { buildAiAuthoritativeChartOption, emptyChartOption } from '../../apps/analytics/src/utils/aiChartOption.js'
import { parseDashboardAdjustment, applyDashboardAdjustment } from '../../apps/analytics/src/utils/dashboardAdjustment.js'
import { normalizeSeriesRows } from '../../apps/analytics/src/utils/dashboardData.js'

const require = createRequire(new URL('../../apps/analytics/package.json', import.meta.url))
const ts = require('typescript')
const { shallowRef, watch } = require('vue')
const read = path => fs.readFileSync(new URL('../../' + path, import.meta.url), 'utf8')

test('empty results never reuse chart samples, including gauges and datasets; zero remains data', () => {
  for (const component of ['JBar', 'JLine', 'JPie', 'JGauge', 'JRadar', 'JProgress', 'JMap', 'JWordCloud']) {
    const source = { series: [{ type: 'bar', data: [42, 68] }], dataset: { source: [{ value: 999 }] } }
    const result = buildAiAuthoritativeChartOption({ widget: { component }, source, data: [] })
    assert.deepEqual(result.series, [])
    assert.deepEqual(result.dataset, [])
    assert.equal(result.graphic[0].style.text, '暂无数据')
    assert.deepEqual(source.series[0].data, [42, 68], 'rendering must not mutate saved template')
  }
  const result = buildAiAuthoritativeChartOption({ widget: { component: 'JGauge' }, data: [{ name: '真实零值', value: 0 }] })
  assert.equal(result.series[0].data[0].value, 0)
  assert.equal(result.series[0].data[0].name, '真实零值')
})

test('ordinary chart renderer also handles an empty query without fallback values', () => {
  const source = read('apps/analytics/src/components/jimu-screen/JimuChart.vue').match(/<script setup>([\s\S]*?)<\/script>/)[1]
  const ast = ts.createSourceFile('chart.js', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS)
  const fn = ast.statements.find(node => ts.isFunctionDeclaration(node) && node.name.text === 'baseOption').getText(ast)
  const context = vm.createContext({ chartData: () => [], clone: x => structuredClone(x), sanitizeChartOption: x => x,
    normalizeSeriesRows, emptyChartOption })
  vm.runInContext(fn + '\nresult = baseOption({component:"JBar",config:{option:{series:[{data:[42,68]}]}}})', context)
  assert.equal(context.result.series.length, 0)
})

test('negative style clauses cannot change or recolor the selected chart', () => {
  const widgets = [{ id: 'one', type: 'chart', component: 'JBar', config: { option: { series: [{ type: 'bar' }] } } }]
  for (const prompt of ['不要改成饼图，只调整当前组件圆角', '不需要绿色，当前组件圆角', '保持柱状图，但调整圆角']) {
    const intent = parseDashboardAdjustment(prompt, { selectedId: 'one', widgets })
    const result = applyDashboardAdjustment({}, widgets, intent)
    assert.equal(result.widgets[0].component, 'JBar')
    assert.equal(result.widgets[0].style.radius, 10)
    assert.equal(intent.theme, null)
  }
  assert.equal(parseDashboardAdjustment('不要改成饼图', { selectedId: 'one', widgets }).handled, false)
  assert.equal(parseDashboardAdjustment('不要饼图，改成折线图', { selectedId: 'one', widgets }).chart.component, 'JLine')
  assert.equal(parseDashboardAdjustment('把当前组件改成饼图', { selectedId: 'one', widgets }).chart.component, 'JPie')
})

function transferHarness() {
  const source = read('apps/analytics/src/components/datasources/DataSourceTransferDialog.vue')
    .match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm, '')
  const code = ts.transpileModule(source + '\nglobalThis.subject={selectFile,validateImport,applyImport,preview,bundle,conflictMode,open,checking,importing};',
    { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None } }).outputText
  const calls = [], requests = [], messages = []
  const context = vm.createContext({ shallowRef, watch, onBeforeUnmount() {}, defineProps: () => ({ canImport: true }),
    defineModel: () => shallowRef(true), defineEmits: () => () => {},
    ElMessage: { success: x => messages.push(x), error: x => messages.push(x) },
    post: (_url, body) => { calls.push(structuredClone(body)); return new Promise((resolve, reject) => requests.push({ resolve, reject })) },
  })
  vm.runInContext(code, context)
  const s = context.subject
  const select = (name, text = async () => JSON.stringify({ formatVersion: 1, items: [{ name }] })) =>
    s.selectFile({ target: { files: [{ name, size: 100, text }] } })
  const finish = (i, dryRun = true) => requests[i].resolve({ data: { dryRun, created: 1, updated: 0, skipped: 0, items: [] } })
  return { s, calls, requests, select, finish, messages }
}

test('switching files invalidates in-flight preview; only newly checked file can be imported once', async () => {
  const { s, calls, select, finish } = transferHarness()
  await select('A')
  const old = s.validateImport()
  await select('B')
  finish(0); await old
  assert.equal(s.preview.value, null)
  await s.applyImport()
  assert.equal(calls.length, 1)
  const current = s.validateImport(); finish(1); await current
  const importing = s.applyImport()
  await s.applyImport()
  assert.equal(calls.length, 3)
  assert.equal(calls[2].items[0].name, 'B')
  finish(2, false); await importing
  await s.applyImport()
  assert.equal(calls.length, 3)
})

test('policy changes and closing dialog reject late previews', async () => {
  for (const change of [s => { s.conflictMode.value = 'update' }, s => { s.open.value = false }]) {
    const { s, calls, select, finish } = transferHarness()
    await select('A'); const pending = s.validateImport()
    change(s); finish(0); await pending
    assert.equal(s.preview.value, null)
    await s.applyImport(); assert.equal(calls.length, 1)
  }
})

test('a slowly read file cannot overwrite the last selected file', async () => {
  const { s, select } = transferHarness()
  let finish
  const old = select('A', () => new Promise(resolve => { finish = resolve }))
  await select('B')
  finish(JSON.stringify({ formatVersion: 1, items: [{ name: 'A' }] })); await old
  assert.equal(s.bundle.value.items[0].name, 'B')
})
