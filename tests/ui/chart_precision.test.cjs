/* 纯 Node 复现真实图表装配与 SmartChart 函数；不启动浏览器、服务或业务查询。 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { test } = require('node:test');
const root = path.resolve(__dirname, '../..');
const source = name => path.join(root, 'apps/analytics/src', name);
const load = name => import(pathToFileURL(source('utils/' + name + '.js')).href);

async function smart(rows) {
  const ts = require(path.join(root, 'apps/analytics/node_modules/typescript'));
  const precision = fs.existsSync(source('utils/chartPrecision.js')) ? await load('chartPrecision') : {};
  const body = fs.readFileSync(source('components/SmartChart.vue'), 'utf8').match(/<script setup>([\s\S]*?)<\/script>/)[1];
  const compiled = ts.transpileModule(body, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  const imports = name => {
    if (name === 'vue') return { ref: value => ({ value }), shallowRef: value => ({ value }), watch() {}, onMounted() {}, onUnmounted() {} };
    if (name === 'echarts') return { graphic: { LinearGradient: class {} } };
    if (name.endsWith('chartPrecision')) return precision;
    if (name.endsWith('safeContent')) return { safeChartOption: value => value };
    return {};
  };
  return new Function('require', 'exports', 'defineProps', 'defineEmits', compiled + '\nreturn { generateBarChartOption, generatePieChartOption, generateLineChartOption, generateScatterChartOption, isNumericColumn };')(
    imports, {}, () => ({ data: rows, columns: [] }), () => () => {});
}
const analysis = { categoryColumns: [{ name: 'category', comment: '类别' }], numericColumns: [{ name: 'amount', comment: '金额' }] };
const exact = (series, index) => series.data[index]?.exactValue ?? series.exactValues?.[index];
const coordinate = value => value && typeof value === 'object' ? value.value : value;

async function jimu(widget, rows) {
  const ts = require(path.join(root, 'apps/analytics/node_modules/typescript'));
  const relative = 'apps/analytics/src/components/jimu-screen/JimuChart.vue';
  const baseline = process.env.TEST5_CHART_COMPONENT_BASELINE;
  const content = baseline ? require('node:child_process').execFileSync('git', ['show', baseline + ':' + relative], { cwd: root, encoding: 'utf8' })
    : fs.readFileSync(path.join(root, relative), 'utf8');
  const body = content.match(/<script setup>([\s\S]*?)<\/script>/)[1].replaceAll('import.meta.env.BASE_URL', "'/'");
  const compiled = ts.transpileModule(body, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  const modules = {};
  for (const name of ['dashboardData', 'dashboardTheme', 'aiChartOption', 'snapshotChartOption', 'chartPrecision']) modules[name] = await load(name);
  const imports = name => name === 'vue' ? { ref: value => ({ value }), onMounted() {}, onBeforeUnmount() {}, watch() {} }
    : name === 'echarts' ? { graphic: { LinearGradient: class {} } }
    : name.endsWith('safeContent') ? { safeChartOption: value => value } : modules[name.split('/').at(-1)];
  return new Function('require', 'exports', 'defineProps', 'defineEmits', 'defineExpose', compiled + '\nreturn baseOption;')(
    imports, {}, () => ({ widget, data: rows }), () => () => {}, () => {})(widget);
}

test('快照同时保留精确原文、有限绘图近似值和未知值', async () => {
  const { snapshotChartOption } = await load('snapshotChartOption');
  const rows = ['9007199254740993', '+123.4500', '1.2300e-20', null, ' ', true, Infinity].map(amount => ({ category: '<img src=x>', amount }));
  const before = structuredClone(rows);
  const option = snapshotChartOption({ component: 'JBar', config: { snapshotBinding: { labelField: 'category', valueField: 'amount' } } }, {}, rows);
  assert.equal(exact(option.series[0], 0), '9007199254740993');
  assert.equal(exact(option.series[0], 1), '+123.4500');
  assert.equal(exact(option.series[0], 2), '1.2300e-20');
  assert.deepEqual(option.series[0].data.slice(3).map(coordinate), [null, null, null, null]);
  assert.equal(typeof option.tooltip.formatter, 'function');
  assert.match(option.tooltip.formatter([{ seriesIndex: 0, dataIndex: 0, name: '<img src=x>', seriesName: '金额' }]), /9007199254740993/);
  assert.deepEqual(rows, before);
});

test('公共归一化保留科学计数与尾零的独立原文通道', async () => {
  const { normalizeSeriesRows, inferSeriesSchema } = await load('dashboardData');
  const rows = [{ label: '甲', amount: '9007199254740993' }, { label: '乙', amount: '1.2300e-20' }, { label: '丙', amount: null }];
  const normalized = normalizeSeriesRows(rows, { labelField: 'label', valueField: 'amount' });
  assert.equal(normalized[0].exactValue, rows[0].amount);
  assert.equal(normalized[1].exactValue, rows[1].amount);
  const schema = inferSeriesSchema(rows);
  assert.deepEqual(schema.series[0].exactValues, [rows[0].amount, rows[1].amount, null]);
});

test('AI仪表与雷达未知值不补零，原文可供精确tooltip读取', async () => {
  const { buildAiAuthoritativeChartOption: build } = await load('aiChartOption');
  const gauge = build({ widget: { component: 'JGauge' }, data: [{ name: '未知', value: null }] });
  assert.equal(gauge.series[0].data[0].value, null);
  const radar = build({ widget: { component: 'JRadar' }, data: [{ name: '甲', value: '9007199254740993' }, { name: '乙', value: null }] });
  assert.deepEqual(radar.series[0].data[0].exactValue, ['9007199254740993', null]);
  assert.equal(radar.series[0].data[0].value[1], null);
});

test('SmartChart柱饼两路大整数抵消及十进制求和一致', async () => {
  const rows = [{ category: '甲', amount: '9007199254740993' }, { category: '甲', amount: '-9007199254740992' },
    { category: '乙', amount: '0.10' }, { category: '乙', amount: '0.20' }];
  const component = await smart(rows);
  for (const create of [component.generateBarChartOption, component.generatePieChartOption]) {
    const series = create(analysis).series[0];
    assert.deepEqual(series.data.map(coordinate), [1, 0.3]);
    assert.deepEqual(series.data.map((_, index) => exact(series, index)), ['1', '0.30']);
  }
});

test('SmartChart特殊类别、空类别与零各自独立，未知不冒充零', async () => {
  const names = ['__proto__', 'constructor', 'toString', '', '正常', '未知'];
  const component = await smart(names.map((category, index) => ({ category, amount: index === 5 ? null : String(index) })));
  const bar = component.generateBarChartOption(analysis);
  assert.deepEqual(bar.xAxis.data, names);
  assert.deepEqual(bar.series[0].data.map(coordinate), [0, 1, 2, 3, 4, null]);
  const pie = component.generatePieChartOption(analysis);
  assert.deepEqual(pie.series[0].data.map(item => item.name), names);
  assert.deepEqual(pie.series[0].data.map(coordinate), [0, 1, 2, 3, 4, null]);
});

test('SmartChart空白、布尔、非有限数不能被识别为数值列', async () => {
  const component = await smart([]);
  for (const values of [['', ' '], [true, false], [Infinity, -Infinity], ['NaN', 'Infinity']]) assert.equal(component.isNumericColumn(values), false);
  assert.equal(component.isNumericColumn(['0', '-1.25', '1e3']), true);
});

test('Jimu负值图保持真实符号，不按行号翻转', async () => {
  const rows = [{ name: '甲', value: '5.00' }, { name: '乙', value: '7.00' }, { name: '丙', value: '-3.00' }];
  const option = await jimu({ component: 'JNegativeBar', w: 450, config: {} }, rows);
  assert.deepEqual(option.series[0].data.map(coordinate), [5, 7, -3]);
});

for (const component of ['JStackBar', 'JPercentBar', 'JMixLineBar']) {
  test(`${component}只使用实际系列，缺少系列不制造固定比例`, async () => {
    const rows = [{ name: '甲', amount: '5.00' }, { name: '乙', amount: '7.00' }];
    const option = await jimu({ component, w: 450, config: {} }, rows);
    assert.equal(option.series.length, 1, component);
    assert.deepEqual(option.series[0].data.map(coordinate), [5, 7], component);
    const multi = await jimu({ component, w: 450, config: {} }, rows.map(row => ({ ...row, count: '2' })));
    assert.equal(multi.series.length, 2, component);
    assert.deepEqual(multi.series.map(series => series.data.map(coordinate)), [[5, 7], [2, 2]], component);
  });
}

test('受控十进制运算保留负数、科学计数、部分和及资源边界', async () => {
  const { sumChartDecimals, chartPoint, chartDecimalDifference, withExactChartTooltip } = await load('chartPrecision');
  assert.equal(sumChartDecimals(['1.20e2', '-119.9900']).exactValue, '0.0100');
  assert.equal(sumChartDecimals(['-0.10', '0.1']).exactValue, '0.00');
  assert.equal(chartDecimalDifference('9007199254740993', '9007199254740992').exactValue, '1');
  for (const value of [null, '', ' ', true, false, NaN, Infinity, '1,2', '1e999999999', '9'.repeat(5000)]) assert.equal(chartPoint(value).exactValue, null);
  for (const value of ['1e400', '1e-400']) {
    assert.equal(chartPoint(value).exactValue, value);
    assert.equal(chartPoint(value).value, null);
  }
  const partial = sumChartDecimals(['3.00', null, true]);
  assert.equal(partial.exactValue, '3.00');
  assert.deepEqual([partial.validCount, partial.missingCount], [1, 2]);
  const option = withExactChartTooltip({ series: [{ data: [partial, chartPoint(Number.MAX_SAFE_INTEGER + 1)] }] });
  assert.match(option.tooltip.formatter({ dataIndex: 0 }), /有效 1\/3 条/);
  assert.match(option.tooltip.formatter({ dataIndex: 1 }), /约 9007199254740992/);
});

test('AI普通系列、饼图、漏斗与散点全保留原值和未知', async () => {
  const { buildAiAuthoritativeChartOption: build } = await load('aiChartOption');
  const rows = [{ name: '<img src=x onerror=alert(1)>', amount: '9007199254740993', count: '1.2300e-20' },
    { name: '未知', amount: null, count: null }];
  for (const [component, type] of [['JBar', 'bar'], ['JPie', 'pie'], ['JFunnel', 'funnel']]) {
    const option = build({ widget: { component }, source: { series: [{ type }] }, data: rows });
    assert.equal(exact(option.series[0], 0), rows[0].amount);
    assert.equal(coordinate(option.series[0].data[1]), null);
    assert.equal(option.tooltip.renderMode, 'richText');
    assert.match(option.tooltip.formatter({ seriesIndex: 0, dataIndex: 0, name: rows[0].name }), /9007199254740993/);
  }
  const scatter = build({ widget: { component: 'JScatter' }, data: rows });
  assert.deepEqual(scatter.series[0].data[0].exactValue, [rows[0].amount, rows[0].count]);
  assert.deepEqual(scatter.series[0].data[1].value, [null, null]);
});

test('绘图库保留字内部标识可逆且不改业务数据或普通类别', async () => {
  const { withExactChartTooltip, chartBusinessName, chartLegendSelection } = await load('chartPrecision');
  const names = ['__proto__', 'constructor', 'toString', '__defineGetter__', '__chart_business_0__', '正常中文'];
  const source = names.map(name => ({ name, value: 1, exactValue: '1' }));
  const option = withExactChartTooltip({ legend: {}, series: [{ type: 'pie', data: source }] });
  const encoded = option.series[0].data.map(item => item.name);
  assert.equal(new Set(encoded).size, names.length);
  assert.deepEqual(encoded.map(name => chartBusinessName(option, name)), names);
  assert.deepEqual(source.map(item => item.name), names);
  assert.equal(encoded[4], names[4]); assert.equal(encoded[5], names[5]);
  const event = chartLegendSelection(option, { name: encoded[0], selected: { [encoded[0]]: false, [encoded[5]]: true } });
  assert.equal(event.name, '__proto__'); assert.equal(Object.hasOwn(event.selected, '__proto__'), true);
  assert.equal(event.selected.__proto__, false); assert.equal(event.selected['正常中文'], true);
});

module.exports = { smart, jimu };
