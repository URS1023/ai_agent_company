/*
 * 中文说明（工作流 JSON 编辑契约的纯前端单元测试）：
 * 验证行为：parseWorkflowJson / parseRuleConstant / formatRuleConstant 对 JSON 标量、安全小数与
 *   嵌套值的类型保持（解析后再格式化不改语义）。
 * 运行方式：node --test tests/ui/workflow_json_contracts.mjs，依赖
 *   apps/analytics/src/utils/workflowJson.js。
 * 边界：纯函数测试；不连后端、不需要浏览器。
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { parseWorkflowJson, parseRuleConstant, formatRuleConstant } from '../../apps/analytics/src/utils/workflowJson.js';

test('JSON scalars, safe decimals and complete nested values retain their types', () => {
  for (const source of ['null', 'true', 'false', '0', '-0', '0.1', '41.2500', '1.25e-12', '9007199254740991', '[1, "2", false]', '{"amount":82.5,"parts":[null,{"编号":"001"}]}']) {
    assert.deepEqual(parseWorkflowJson(source), JSON.parse(source), source);
  }
});

test('unsafe integers and long decimal tokens are rejected instead of rounded', () => {
  for (const number of ['9007199254740993', '9007199254740992', '-9007199254740993', '0.10000000000000000001', '41.250000000000000001', '1e309', '1e-999', '1.234567890123456789e-15']) {
    for (const source of [number, '[' + number + ']', '{"args":[{"value":' + number + '}]}']) assert.throws(() => parseWorkflowJson(source), RangeError, source);
    assert.throws(() => parseRuleConstant(number), RangeError, 'Precision errors must not become unquoted text');
  }
});

test('quoted exact amounts, numeric identifiers and escaped strings are untouched', () => {
  const record = { amount: '0.10000000000000000001', id: '9007199254740993', escaped: '"9007199254740993"\\name', literal: 'false' };
  assert.deepEqual(parseWorkflowJson(JSON.stringify(record)), record);
  for (const value of Object.values(record)) assert.equal(parseRuleConstant(formatRuleConstant(value)), value);
  assert.equal(parseRuleConstant('设备 A'), '设备 A');
  assert.equal(parseRuleConstant('"123"'), '123');
  assert.equal(formatRuleConstant('123'), '"123"');
});

test('invalid JSON and over-budget input cannot reach workflow requests', () => {
  for (const source of ['{', '[1,]', '{"x": NaN}', '{"x":01}', 'undefined']) assert.throws(() => parseWorkflowJson(source), SyntaxError);
  assert.throws(() => parseWorkflowJson(' '.repeat(1024 * 1024 + 1)), RangeError);
});
