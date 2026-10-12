/*
 * 中文说明（工作流连线路由的纯前端单元测试）：
 * 验证行为：routeWorkflowEdge 在不同端口位置下生成的贝塞尔/正交路径与中点计算正确。
 * 运行方式：node --test tests/ui/workflow_edge_routing.mjs，依赖
 *   apps/analytics/src/utils/workflowEdgeRouting.js。
 * 边界：纯几何函数测试，不连后端、不需要浏览器。
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { routeWorkflowEdge } from '../../apps/analytics/src/utils/workflowEdgeRouting.js';

const cubic = (a, b) => [`M${a.x},${a.y} C${(a.x + b.x) / 2},${a.y} ${(a.x + b.x) / 2},${b.y} ${b.x},${b.y}`, (a.x + b.x) / 2, (a.y + b.y) / 2];
function route(nodes, source, target, extra = {}) {
  return routeWorkflowEdge({ nodes, source, target, sourceId: 'source', targetId: 'target',
    sourceSide: 'right', targetSide: 'left', bezier: cubic(source, target), ...extra });
}
function avoidsCards(result, nodes) {
  assert.equal(result.rerouted, true);
  for (let i = 1; i < result.points.length; i++) {
    const a = result.points[i - 1], b = result.points[i];
    assert.ok(a.x === b.x || a.y === b.y, 'detours must stay orthogonal');
    const samples = Math.max(1, Math.ceil(Math.hypot(a.x - b.x, a.y - b.y) / 2));
    for (let j = 0; j <= samples; j++) {
      const x = a.x + (b.x - a.x) * j / samples, y = a.y + (b.y - a.y) * j / samples;
      assert.equal(nodes.some(n => x > n.x && x < n.x + n.width && y > n.y && y < n.y + n.height), false,
        `route intersects a card at ${x},${y}`);
    }
  }
}

test('unobstructed short connections retain their original Dify curve', () => {
  const source = { x: 126, y: 50 }, target = { x: 300, y: 50 };
  const result = route([], source, target);
  assert.equal(result.rerouted, false);
  assert.equal(result.path, cubic(source, target)[0]);
});

test('a long output-to-merge connection cannot pass through intermediate output cards', () => {
  const nodes = [
    { id: 'source', x: 1000, y: 230, width: 270, height: 120 },
    { id: 'other-output-1', x: 1330, y: 230, width: 270, height: 120 },
    { id: 'other-output-2', x: 1660, y: 230, width: 270, height: 120 },
    { id: 'other-output-3', x: 1990, y: 230, width: 270, height: 120 },
    { id: 'target', x: 2670, y: 0, width: 270, height: 100 },
  ];
  const source = { x: 1276, y: 280 }, target = { x: 2664, y: 40 };
  const result = route(nodes, source, target);
  avoidsCards(result, nodes);
  assert.deepEqual(result.points[0], source);
  assert.deepEqual(result.points.at(-1), target);
  assert.ok(result.path.includes(' Q'), 'corners must remain rounded');
  const otherLane = route(nodes, source, target, { lane: 1 });
  avoidsCards(otherLane, nodes);
  assert.notEqual(result.path, otherLane.path, 'incoming branches use separate detour lanes');
  const approach = path => path.points.slice(1).flatMap((b, i) => {
    const a = path.points[i];
    return a.x === b.x && a.x > 2400 && Math.abs(a.y - b.y) > 28 ? [a.x] : [];
  });
  const thirdLane = route(nodes, source, target, { lane: 2 });
  avoidsCards(thirdLane, nodes);
  assert.ok(approach(otherLane).length && approach(thirdLane).length);
  assert.notEqual(approach(otherLane).at(-1), approach(thirdLane).at(-1), 'outputs must not share a long approach leg into the merge');
});

test('rerouting follows all sixteen source/target side combinations', () => {
  const nodes = [
    { id: 'source', x: 0, y: 0, width: 120, height: 100 },
    { id: 'blocker', x: 260, y: -140, width: 140, height: 360 },
    { id: 'target', x: 600, y: 0, width: 120, height: 100 },
  ];
  const ports = { left: [-6, 50], right: [126, 50], top: [60, -6], bottom: [60, 106] };
  const vectors = { left: [-1, 0], right: [1, 0], top: [0, -1], bottom: [0, 1] };
  for (const [sourceSide, [sx, sy]] of Object.entries(ports)) {
    for (const [targetSide, [tx, ty]] of Object.entries(ports)) {
      const source = { x: sx, y: sy }, target = { x: tx + 600, y: ty };
      for (const lane of [0, 1, 4]) {
        const result = route(nodes, source, target, { sourceSide, targetSide, lane });
        avoidsCards(result, nodes);
        const first = result.points[1], last = result.points.at(-2);
        const direction = (a, b) => [Math.sign(b.x - a.x) || 0, Math.sign(b.y - a.y) || 0];
        assert.deepEqual(direction(source, first), vectors[sourceSide]);
        assert.deepEqual(direction(target, last), vectors[targetSide]);
      }
    }
  }
});

test('moving a blocking card away restores the short curve; moving it back reroutes again', () => {
  const blocker = { id: 'other', x: 200, y: 0, width: 100, height: 100 };
  const a = { x: 100, y: 50 }, b = { x: 400, y: 50 };
  avoidsCards(route([blocker], a, b), [blocker]);
  assert.equal(route([{ ...blocker, y: 500 }], a, b).rerouted, false);
  avoidsCards(route([blocker], a, b), [blocker]);
});

test('staggered obstacles requiring multiple turns still have a clear route', () => {
  const nodes = [
    { id: 'a', x: 160, y: -120, width: 70, height: 220 },
    { id: 'b', x: 300, y: 20, width: 70, height: 220 },
    { id: 'c', x: 20, y: 100, width: 180, height: 50 },
    { id: 'd', x: 360, y: -60, width: 180, height: 50 },
  ];
  avoidsCards(route(nodes, { x: 90, y: 50 }, { x: 480, y: 50 }), nodes);
});
