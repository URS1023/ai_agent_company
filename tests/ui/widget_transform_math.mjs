/*
 * 中文说明（组件旋转/缩放几何计算的纯前端单元测试）：
 * 验证行为：normalizeRotation 与 resizeRotatedWidget 在旋转态下的锚点与尺寸计算正确。
 * 运行方式：node --test tests/ui/widget_transform_math.mjs，依赖
 *   apps/analytics/src/utils/widgetTransform.js。
 * 边界：纯数学函数测试（含角度归一与负旋转），不访问后端与浏览器。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { normalizeRotation, resizeRotatedWidget } from '../../apps/analytics/src/utils/widgetTransform.js'

function oppositeAnchor(widget, side) {
  const angle = widget.rotation * Math.PI / 180
  const x = side.includes('right') ? -widget.w / 2 : side.includes('left') ? widget.w / 2 : 0
  const y = side.includes('bottom') ? -widget.h / 2 : side.includes('top') ? widget.h / 2 : 0
  return [widget.x + widget.w / 2 + x * Math.cos(angle) - y * Math.sin(angle),
    widget.y + widget.h / 2 + x * Math.sin(angle) + y * Math.cos(angle)]
}

for (const rotation of [0, 45, 90, -135]) {
  test(`all resize handles preserve the opposite anchor at ${rotation} degrees`, () => {
    const origin = { x: 180, y: 240, w: 200, h: 120, rotation }
    for (const side of ['top', 'top-right', 'right', 'bottom-right', 'bottom', 'bottom-left', 'left', 'top-left']) {
      const patch = resizeRotatedWidget(origin, side, 37, 29)
      const before = oppositeAnchor(origin, side)
      const after = oppositeAnchor({ ...origin, ...patch }, side)
      assert.ok(Math.hypot(after[0] - before[0], after[1] - before[1]) < 1.1, side)
    }
  })
}

test('a quarter-turn right handle grows width when dragged down', () => {
  assert.deepEqual(resizeRotatedWidget({ x: 100, y: 100, w: 120, h: 60, rotation: 90 }, 'right', 0, 50),
    { x: 75, y: 125, w: 170, h: 60 })
})

test('minimum dimensions stop at the opposite corner', () => {
  const origin = { x: 100, y: 100, w: 120, h: 60, rotation: 0 }
  assert.deepEqual(resizeRotatedWidget(origin, 'top-left', 1000, 1000), { x: 176, y: 130, w: 44, h: 30 })
})

test('rotation wraps into the same range as the property slider', () => {
  assert.equal(normalizeRotation(359), -1)
  assert.equal(normalizeRotation(-361), -1)
  assert.equal(normalizeRotation(721), 1)
  assert.equal(normalizeRotation(-270), 90)
})
