"use strict";
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const root = path.resolve(__dirname, '../..');
const catalog = JSON.parse(fs.readFileSync(path.join(root, 'backend/app/modules/reports/resources/hifi_templates.json'), 'utf8')).templates;
const modulePromise = import(pathToFileURL(path.join(root, 'apps/analytics/src/utils/referenceScreen.js')));

test('所有参考示例都有有效指纹；真实绑定、快照及编辑不显示截图中的旧值', async () => {
  const { isReferenceSample } = await modulePromise;
  let count = 0;
  for (const template of catalog) for (const widget of template.content.widgets.filter(widget => widget.referenceSkin.kind !== 'backdrop')) {
    const data = widget.config.chartDataParsed;
    assert(isReferenceSample(widget, data), widget.id);
    assert(!isReferenceSample(widget, data, true), '远程结果不得复用图片示例');
    assert(!isReferenceSample({ ...widget, snapshotContext: {} }, data));
    assert(!isReferenceSample({ ...widget, config: { ...widget.config, referenceSample: false } }, data));
    assert(!isReferenceSample({ ...widget, config: { ...widget.config, dataStatus: 'verified' } }, data));
    assert(!isReferenceSample(widget, []));
    assert(!isReferenceSample(widget, [{ name: '修改后的数据', value: 999 }]));
    count++;
  }
  assert.equal(count, 342);
});

test('动态多系列只使用当前输入，保留负数与空值并且不修改原配置', async () => {
  const { referenceChartOption } = await modulePromise;
  const widget = catalog[0].content.widgets.find(widget => widget.referenceSkin.kind === 'bar');
  const original = JSON.stringify(widget);
  const data = [{ 部门: '部门A', 收入: 19.25, 支出: null }, { 部门: '部门B', 收入: -2.5, 支出: 14 }];
  const option = referenceChartOption(widget, data);
  assert.deepEqual(option.xAxis.data, ['部门A', '部门B']);
  assert.deepEqual(option.series.map(series => series.data), [[19.25, -2.5], [null, 14]]);
  assert.equal(option.yAxis.min, undefined);
  assert.equal(JSON.stringify(widget), original);
});
