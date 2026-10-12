/*
 * 中文说明（导航契约的纯前端单元测试）：
 * 验证行为：企业导航的别名解析、权限过滤、排序偏好与模块边界（areaFor/areaHref/entriesForArea
 *   /navigationEntry/platformName）在给定数据下输出正确。
 * 运行方式：node --test tests/ui/navigation_contracts.mjs；直接加载后端下发的
 *   apps/web/assets/platform/enterprise-navigation.js（需为它准备 globalThis.location）。
 * 边界：纯函数测试，不连后端、不启动浏览器。
 */
/** Pure navigation contracts: aliases, permissions, sort preferences and module boundaries. */
import assert from 'node:assert/strict';
import { BUSINESS_AREAS, areaFor, areaHref, availableAreas, entriesForArea, navigationEntry, platformName } from '../../apps/web/assets/platform/enterprise-navigation.js';
globalThis.location = { pathname: '/', search: '' };
const items = [
  { id: 'home', path: '/', available: true, sort_order: 0 },
  { id: 'chat', path: '/ai', available: true, sort_order: 10 },
  { id: 'datasources', path: '/datasources', available: true, sort_order: 30 },
  { id: 'analytics', path: '/analytics', available: true, sort_order: 40 },
  { id: 'dashboards', path: '/dashboards', available: true, sort_order: 20 },
  { id: 'users', path: '/system/users', available: true, sort_order: 100 },
  { id: 'system-development', path: '/system/development', available: true, sort_order: 110 },
  { id: 'models', path: '/system/models', available: true, sort_order: 120 },
  { id: 'quality', path: '/quality', available: false, sort_order: 150 },
];
const data = BUSINESS_AREAS.find(area => area.id === 'data');
const ai = BUSINESS_AREAS.find(area => area.id === 'ai');
assert.equal(platformName('旧产品名称'), '智慧云脑 Cloud Brain');
assert.equal(platformName('用户自定义名称'), '智慧云脑 Cloud Brain');
assert.deepEqual(entriesForArea(data, items).map(item => item.id), ['dashboards', 'datasources', 'analytics']);
assert.equal(areaHref(data, items), '/analytics', 'Semantic entry does not override customized sidebar order');
assert.equal(areaHref(ai, items), '/ai');
assert.equal(areaHref(BUSINESS_AREAS.find(area => area.id === 'business'), items), '/?area=business');
for (const [path, expected] of [
  ['/report/designer/abc', 'dashboards'], ['/report/view/abc', 'dashboards'], ['/analytics/history', 'analytics'],
  ['/system/users/abc', 'users'], ['/admin-models.html', 'models'], ['/admin-models', 'models'],
  ['/ai', 'chat'], ['/jimu-screen', 'dashboards'], ['/app', 'home'],
  ['/system/api', 'system-development'],
]) assert.equal(navigationEntry(items, path)?.id, expected, path);
assert.equal(navigationEntry(items, '/nonexistent'), null);
const restricted = items.filter(item => ['home', 'dashboards', 'datasources'].includes(item.id));
assert.deepEqual(availableAreas(restricted).map(area => area.id), ['home', 'data']);
assert.equal(entriesForArea(ai, restricted).length, 0, 'No entry is synthesized for missing permissions');
assert.equal(areaFor(items[1], '').id, 'ai');
assert.equal(areaFor(items[0], 'business').id, 'business');
assert.equal(areaFor(items[0], 'unknown').id, 'home');
const workflowItems = [...items, { id: 'workflows', path: '/workflows', available: true, sort_order: 200 }];
assert.equal(areaHref(BUSINESS_AREAS.find(area => area.id === 'business'), workflowItems), '/workflows');
assert.equal(navigationEntry(workflowItems, '/workflows/new')?.id, 'workflows');
assert.equal(navigationEntry(workflowItems, '/workflows/abc')?.id, 'workflows');
const legacyItems = [
  ...workflowItems,
  { id: 'operations', path: '/operations', available: true, sort_order: 210 },
  { id: 'system-operations', path: '/system/operations', available: true, sort_order: 220 },
];
for (const [path, expected] of [
  ['/runs', 'operations'], ['/admin-ops.html', 'system-operations'], ['/admin-audit.html', 'system-operations'],
]) assert.equal(navigationEntry(legacyItems, path)?.id, expected, path);
console.log('PASS navigation contracts: aliases, area boundaries, custom menu order and permission-filtered discovery');
const alertItems = [...workflowItems, { id: 'alerts', path: '/alerts', available: true, sort_order: 210 }];
for (const page of ['devices', 'points', 'advice', 'rules', 'reports', 'problems']) {
  assert.equal(navigationEntry(alertItems, '/alerts/' + page)?.id, 'alerts');
}
assert.equal(areaFor(navigationEntry(alertItems, '/alerts/reports')).id, 'business');
for (const page of ['metrics', 'templates', 'inspections']) {
  assert.equal(navigationEntry(items, '/quality/' + page)?.id, 'quality');
  assert.equal(areaFor(navigationEntry(items, '/quality/' + page)).id, 'business');
}
