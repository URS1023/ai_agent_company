"use strict";
// 快照转换只接受保存数据，覆盖空结果、缺失值、损坏数据与输入不可变性。
const assert = require("node:assert/strict");
const test = require("node:test");
const { pathToFileURL } = require("node:url");
const path = require("node:path");
const modulePath = pathToFileURL(path.resolve(__dirname, "../../apps/analytics/src/utils/chatReportSnapshot.js")).href;

test("聊天快照关闭远程绑定并保留已保存行、空结果与空值", async () => {
  const { chatReportSnapshot } = await import(modulePath);
  const source = { refreshSeconds: 10, widgets: [
    { id: "stock", data: [{ 库存: 0, 安全库存: null }], config: { dataType: 3, sql: "SELECT stock", chartDataParsed: [{ 库存: 0, 安全库存: null }], drilldown: { enabled: true }, linkageConfig: ["remote"] } },
    { id: "empty", config: { dataType: 2, chartData: "[]" } },
  ] };
  const before = JSON.stringify(source);
  const output = chatReportSnapshot(source);
  assert.equal(JSON.stringify(source), before);
  assert.equal(output.missing, 0);
  assert.equal(output.screen.refreshSeconds, 0);
  assert.deepEqual(output.screen.widgets.map(widget => widget.config.dataType), [1, 1]);
  assert.deepEqual(output.screen.widgets[0].config.chartDataParsed, [{ 库存: 0, 安全库存: null }]);
  assert.deepEqual(output.screen.widgets[1].config.chartDataParsed, []);
  assert.equal(output.screen.widgets[0].config.drilldown, undefined);
  assert.equal(output.screen.widgets[0].config.linkageConfig, undefined);
});

test("缺失或损坏快照显示缺失提示，不把适配器的默认空数组误认为已保存", async () => {
  const { chatReportSnapshot } = await import(modulePath);
  const output = chatReportSnapshot({ sourceContent: { widgets: [
    { id: "missing", config: { dataType: 3 } },
    { id: "broken", config: { dataType: 2, chartData: "bad-json" } },
  ] }, widgets: [
    { id: "missing", componentName: "应付金额", config: { dataType: 3, chartDataParsed: [] } },
    { id: "broken", config: { dataType: 2, chartDataParsed: [] } },
  ] });
  assert.equal(output.missing, 2);
  output.screen.widgets.forEach(widget => {
    assert.equal(widget.component, "JText");
    assert.match(widget.config.option.body.text, /未保存预览数据/);
    assert.equal(widget.config.dataType, 1);
  });
});

test("兼容旧图表dataset快照，内嵌网页不会因打开聊天自动加载", async () => {
  const { chatReportSnapshot } = await import(modulePath);
  const output = chatReportSnapshot({ sourceContent: { widgets: [
    { id: "legacy", echartsOption: { dataset: { source: [{ 名称: "已保存", 数量: 2 }] } } },
  ] }, widgets: [
    { id: "legacy", component: "JBar", config: { dataType: 3 } },
    { id: "embed", component: "JIframe", config: { dataType: 1, url: "https://external.invalid/live-report" } },
  ] });
  assert.deepEqual(output.screen.widgets[0].config.chartDataParsed, [{ 名称: "已保存", 数量: 2 }]);
  assert.equal(output.screen.widgets[1].component, "JText");
  assert.equal(output.screen.widgets[1].config.url, undefined);
  assert.match(output.screen.widgets[1].title, /网页组件请独立打开查看/);
  assert.equal(output.missing, 1);
});
