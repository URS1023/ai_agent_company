"use strict";
const assert = require("node:assert/strict");
const test = require("node:test");
const { assertSnapshotChart } = require("./render_saved_dashboard.js");

const widget = { id: "paid-months", type: "bar", config: { snapshotBinding: { labelField: "month", valueField: "paid" } },
  data: [{ month: "2026-08", count: 70, paid: "1284992.44" }, { month: "2026-09", count: 9, paid: "72000.00" }] };
const option = () => ({ xAxis: [{ type: "category", data: ["2026-08", "2026-09"] }],
  yAxis: [{ type: "value", min: 0 }], series: [{ type: "bar", data: [1284992.44, 72000] }] });

test("快照验收识别金额与笔数取错列，即使二者都能画出非空柱图", () => {
  assert.deepEqual(assertSnapshotChart(widget, { option: option() }).actual_values, [1284992.44, 72000]);
  const wrong = option(); wrong.series[0].data = [70, 9];
  assert.throws(() => assertSnapshotChart(widget, { option: wrong }), /实际系列必须逐项等于快照/);
});

test("快照验收拒绝分类轴冒充金额轴、旧模板样例系列和截断范围", () => {
  const category = option(); category.yAxis[0].type = "category";
  assert.throws(() => assertSnapshotChart(widget, { option: category }), /分类轴/);
  const extra = option(); extra.series.push({ type: "bar", data: [120, 200] });
  assert.throws(() => assertSnapshotChart(widget, { option: extra }), /真实系列/);
  const truncated = option(); truncated.yAxis[0].max = 100;
  assert.throws(() => assertSnapshotChart(widget, { option: truncated }), /截断真实金额/);
});

test("快照验收要求同序类目，不接受正确金额对应错误月份", () => {
  const reversed = option(); reversed.xAxis[0].data.reverse();
  assert.throws(() => assertSnapshotChart(widget, { option: reversed }), /实际类目必须与原始快照逐行对应/);
});

test("快照验收拒绝旧模板边距把150px组件绘图区压成20px", () => {
  const compressed = option(); compressed.grid = [{ top: 60, bottom: 70 }];
  assert.throws(() => assertSnapshotChart(widget, { option: compressed, chart_size: { width: 300, height: 150 } }), /压缩到20px/);
});
