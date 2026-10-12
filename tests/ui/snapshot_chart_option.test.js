"use strict";
// 核验模板中旧坐标轴和堆叠不能污染已成功查询的实际展示数值。
const assert = require("node:assert/strict");
const { test } = require("node:test");
const { pathToFileURL } = require("node:url");
const path = require("node:path");

test("快照按明确金额列生成唯一数值系列，去掉模板类别值轴和旧堆叠", async () => {
  const { snapshotChartOption } = await import(pathToFileURL(path.resolve(__dirname,
    "../../apps/analytics/src/utils/snapshotChartOption.js")).href);
  const widget = { component: "JBar", visualMode: "ai", metricName: "已付金额（元）",
    config: { snapshotBinding: { valueField: "paid_amount_total", labelField: "month", unit: "元" } } };
  const original = { dataset: { dimensions: ["old"], source: [{ old: 99 }] },
    xAxis: { type: "value", min: 100 }, yAxis: { type: "category", data: ["旧类别"], min: 0, max: 100 },
    series: [{ type: "bar", name: "旧系列", stack: "total", encode: { y: "old" }, data: [99] },
      { type: "line", data: [100] }] };
  const option = snapshotChartOption(widget, original,
    [{ month: "2026-08", count: 70, paid_amount_total: "1284992.44" },
      { month: "2026-09", count: 9, paid_amount_total: "72000.00" }]);
  assert.equal(option.series.length, 1);
  assert.deepEqual(option.series[0].data, [1284992.44, 72000]);
  assert.deepEqual(option.xAxis.data, ["2026-08", "2026-09"]);
  assert.equal(option.yAxis.type, "value");
  assert.equal(option.yAxis.name, "元");
  assert.equal(option.yAxis.max, undefined);
  assert.equal(option.series[0].stack, undefined);
  assert.equal(option.series[0].encode, undefined);
  assert.equal(option.series[0].showBackground, false);
  assert.deepEqual(option.dataset, []);
  assert.deepEqual(original.series[0].data, [99]);
});

test("未知金额保持null，不补零；未知分类明确显示未提供", async () => {
  const { snapshotChartOption } = await import(pathToFileURL(path.resolve(__dirname,
    "../../apps/analytics/src/utils/snapshotChartOption.js")).href);
  const option = snapshotChartOption({ component: "JLine", config: { snapshotBinding: {
    valueField: "amount", labelField: "department" } } }, {}, [{ department: null, amount: null }]);
  assert.deepEqual(option.series[0].data, [null]);
  assert.deepEqual(option.xAxis.data, ["未提供"]);
});

test("115×78横向条形槽位保留横向编码和可读绘图区，标签保留原始部门", async () => {
  const { snapshotChartOption } = await import(pathToFileURL(path.resolve(__dirname,
    "../../apps/analytics/src/utils/snapshotChartOption.js")).href);
  const rows = [{ department: "0000022-1e685022", amount: 1129244.8 }, { department: null, amount: null }];
  const option = snapshotChartOption({ component: "JHorizontalBar", type: "hbar", w: 115, h: 78,
    metricName: "未付金额", config: { snapshotBinding: { valueField: "amount", labelField: "department", unit: "元" } } }, {}, rows);
  assert.equal(option.xAxis.type, "value");
  assert.equal(option.yAxis.type, "category");
  assert.deepEqual(option.yAxis.data, ["0000022-1e685022", "未提供"]);
  assert.deepEqual(option.series[0].data, [1129244.8, null]);
  assert(78 - option.grid.top - option.grid.bottom >= 35);
  assert(option.title.textStyle.fontSize <= 10);
  assert.equal(option.title.text, "未付金额（元）");
});

test("宽而矮的图表保持真实序列，并为绘图区留下足够高度", async () => {
  const { snapshotChartOption } = await import(pathToFileURL(path.resolve(__dirname,
    "../../apps/analytics/src/utils/snapshotChartOption.js")).href);
  const option = snapshotChartOption({ component: "JHorizontalBar", w: 339, h: 153,
    snapshotContext: { layoutScale: 1920 / 652 }, config: { snapshotBinding: { valueField: "amount", labelField: "department" } } }, {},
    [{ department: "甲", amount: 100 }, { department: "乙", amount: 200 }]);
  assert(153 - option.grid.top - option.grid.bottom >= 60);
  assert(option.xAxis.axisLabel.fontSize >= 20);
  assert.deepEqual(option.series[0].data, [100, 200]);
});

test("26px高标题和80px宽金额卡使用可容纳字号，不裁切或改写金额", async () => {
  const { fitSnapshotFont } = await import(pathToFileURL(path.resolve(__dirname,
    "../../apps/analytics/src/utils/snapshotWidgetLayout.js")).href);
  assert(fitSnapshotFont("资金支付应付情况大屏", 359, 22, 34, 1.2, 3) <= 22 / 1.2);
  const size = fitSnapshotFont("3,685,447.8元", 68, 20, 26);
  assert(size <= 11 && size > 7);
});

test("部门编码短写保留差异段，金额格式化保留高精度尾数", async () => {
  const { snapshotCategoryLabel, exactSnapshotNumber } = await import(pathToFileURL(path.resolve(__dirname,
    "../../apps/analytics/src/utils/snapshotWidgetLayout.js")).href);
  assert.equal(snapshotCategoryLabel('0000022-1e685022-dead-1234', true), '1e…1234');
  assert.equal(snapshotCategoryLabel('0000022-ce985022-beef-4567', true), 'ce…4567');
  assert.equal(snapshotCategoryLabel('财务部门'), '财务部门');
  assert.equal(snapshotCategoryLabel(null), '未提供');
  assert.equal(exactSnapshotNumber('979366.800000'), '979,366.8');
  assert.equal(exactSnapshotNumber('979366.123456'), '979,366.123456');
  assert.equal(exactSnapshotNumber('98765432101234567890.123456789'), '98,765,432,101,234,567,890.123456789');
});

test("只有恰好一个未绑定槽时显示真实口径说明，查询计数去重且不改变原数据", async () => {
  const { snapshotScopeContext } = await import(pathToFileURL(path.resolve(__dirname,
    "../../apps/analytics/src/utils/snapshotWidgetLayout.js")).href);
  const bound = { id: 'amount', type: 'metric', config: { queryId: 'verified-1', snapshotOnly: true, chartDataParsed: [{ amount: 20 }] } };
  const empty = { id: 'empty', type: 'table', config: { chartDataParsed: [] } };
  const widgets = [bound, { ...bound, id: 'chart' }, empty], before = JSON.stringify(widgets);
  assert.deepEqual(snapshotScopeContext(widgets, '应收数据待补充，非实时余额。'),
    { queryCount: 1, scopeNote: '应收数据待补充，非实时余额。', scopeNoteWidgetId: 'empty' });
  assert.equal(snapshotScopeContext([...widgets, { ...empty, id: 'extra' }], '真实说明').scopeNote, '');
  assert.equal(snapshotScopeContext([empty], '真实说明').scopeNote, '');
  assert.equal(snapshotScopeContext(widgets, '').scopeNote, '');
  assert.equal(JSON.stringify(widgets), before);
});

test("旧指标卡保留字段语义，来源前缀不挤压可读字号", async () => {
  const { snapshotMetricLabel } = await import(pathToFileURL(path.resolve(__dirname,
    "../../apps/analytics/src/utils/snapshotWidgetLayout.js")).href);
  assert.equal(snapshotMetricLabel('资金支付汇总明细 · 未付金额（元）合计'), '未付金额（元）合计');
  assert.equal(snapshotMetricLabel('本次申请支付金额'), '本次申请支付金额');
});
