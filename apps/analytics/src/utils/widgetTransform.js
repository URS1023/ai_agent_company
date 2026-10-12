/**
 * widgetTransform —— 大屏组件的几何变换（旋转态缩放、角度归一化）。
 *
 * 位置：JimuCanvas 拖拽缩放组件时调用。
 * 关键约束：
 * - 组件缩放必须在其**自身旋转坐标系**内计算并保持对边固定，否则旋转过的组件会"漂移"；
 * - 尺寸下限（宽 44、高 30）保证组件始终可点选；
 * - 角度归一化到 (-180, 180]，保证同一朝向只有一个表示形式，便于比较与存储。
 */

/** Resize in the component's local axes while keeping the opposite edge fixed. */
export function resizeRotatedWidget(origin, side, dx, dy) {
  const radians = Number(origin.rotation || 0) * Math.PI / 180
  const cos = Math.cos(radians), sin = Math.sin(radians)
  const localX = dx * cos + dy * sin
  const localY = -dx * sin + dy * cos
  const horizontal = side.includes('right') ? 1 : side.includes('left') ? -1 : 0
  const vertical = side.includes('bottom') ? 1 : side.includes('top') ? -1 : 0
  const width = horizontal ? Math.max(44, origin.w + horizontal * localX) : origin.w
  const height = vertical ? Math.max(30, origin.h + vertical * localY) : origin.h
  const shiftX = horizontal * (width - origin.w) / 2
  const shiftY = vertical * (height - origin.h) / 2
  return {
    x: Math.round(origin.x + origin.w / 2 + shiftX * cos - shiftY * sin - width / 2),
    y: Math.round(origin.y + origin.h / 2 + shiftX * sin + shiftY * cos - height / 2),
    w: Math.round(width),
    h: Math.round(height),
  }
}

/**
 * 角度归一化到 (-180, 180] 的整数度：负数角度也要正确取模（先 +180 再取模再 -180）。
 * @param {number} angle 任意角度
 * @returns {number} 归一化后的整数角度
 */
export function normalizeRotation(angle) {
  return Math.round(((angle + 180) % 360 + 360) % 360 - 180)
}
