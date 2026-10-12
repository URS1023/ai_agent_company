<!--
  ScrollingBoard.vue —— 大屏滚动榜单/表格。

  职责：以表头 + 滚动行的形式展示排行或明细列表，支持紧凑模式。
  约束：行数据来自真实查询结果；不得在无数据时渲染空行占位。
-->
<template>
  <div class="scroll-board" :class="{ compact }">
    <div v-if="headers.length" class="scroll-board__head">
      <span v-for="header in headers" :key="header" class="scroll-board__cell">
        {{ header }}
      </span>
    </div>

    <div class="scroll-board__viewport">
      <div class="scroll-board__track" :class="{ animated }">
        <div v-for="(row, index) in renderedRows" :key="`${row.id || row.name}-${index}`" class="scroll-board__row">
          <span v-if="ranking" class="scroll-board__rank" :class="`top-${(index % rows.length) + 1}`">
            {{ (index % rows.length) + 1 }}
          </span>
          <span v-for="field in fields" :key="field" class="scroll-board__cell">
            {{ row[field] }}
          </span>
          <span v-if="row.progress !== undefined" class="scroll-board__progress">
            <i :style="{ width: row.progress + '%' }"></i>
          </span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
/**
 * ScrollingBoard —— 轮播表格（榜单/滚动列表），大屏表格类组件的渲染基座。
 *
 * 使用场景：JimuWidgetRenderer 的 Table/ScrollBoard/RankingBoard 类组件。
 * 关键约束：
 * - 滚动实现依赖"行数组翻倍"（renderedRows）：少于 4 行或未开启动画时不复制，
 *   因为行数太少时复制反而会出现视觉重复；
 * - ranking 模式下的名次按 `index % rows.length` 计算，因此必须与翻倍后的数组配合，
 *   不能改用 index 直接取模前的原始下标；
 * - fields 决定每行取哪些键，顺序即列顺序。
 */
import { computed } from 'vue'

const props = defineProps({
  headers: { type: Array, default: () => [] },
  rows: { type: Array, default: () => [] },
  fields: { type: Array, default: () => ['name', 'value'] },
  ranking: { type: Boolean, default: false },
  animated: { type: Boolean, default: true },
  compact: { type: Boolean, default: false }
})

/** 行数过少或未开启动画时不复制数据，避免滚动出现视觉重复。 */
const renderedRows = computed(() => {
  if (!props.animated || props.rows.length < 4) return props.rows
  return [...props.rows, ...props.rows]
})
</script>

<style scoped>
.scroll-board {
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: 100%;
  color: #d9e8ff;
}

.scroll-board__head,
.scroll-board__row {
  display: grid;
  grid-template-columns: var(--board-grid, 1fr 86px 92px);
  align-items: center;
  gap: 8px;
}

.scroll-board__head {
  flex: none;
  padding: 0 10px 8px;
  color: #6ed7ff;
  font-size: 11px;
  border-bottom: 1px solid rgba(77, 213, 255, 0.09);
}

.scroll-board__viewport {
  min-height: 0;
  overflow: hidden;
}

.scroll-board__track {
  display: flex;
  flex-direction: column;
  gap: 7px;
  padding-top: 9px;
}

.scroll-board__track.animated {
  animation: boardScroll 18s linear infinite;
}

.scroll-board:hover .scroll-board__track.animated {
  animation-play-state: paused;
}

.scroll-board__row {
  min-height: 34px;
  padding: 7px 10px;
  border: 1px solid rgba(77, 213, 255, 0.08);
  border-radius: 6px;
  background: linear-gradient(90deg, rgba(77, 213, 255, 0.07), rgba(77, 213, 255, 0.02));
  font-size: 12px;
}

.compact .scroll-board__row {
  min-height: 28px;
  padding: 5px 8px;
}

.scroll-board__cell {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.scroll-board__rank {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: 5px;
  background: rgba(91, 109, 137, 0.42);
  color: #c8d8ef;
  font-family: Consolas, Monaco, monospace;
  font-weight: 800;
}

.scroll-board__rank.top-1 {
  color: #071221;
  background: linear-gradient(135deg, #facc15, #f59e0b);
}

.scroll-board__rank.top-2 {
  color: #071221;
  background: linear-gradient(135deg, #dbeafe, #94a3b8);
}

.scroll-board__rank.top-3 {
  color: #071221;
  background: linear-gradient(135deg, #fb923c, #f97316);
}

.scroll-board__progress {
  position: relative;
  display: block;
  height: 7px;
  border-radius: 999px;
  background: rgba(148, 163, 184, 0.14);
  overflow: hidden;
}

.scroll-board__progress i {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, #0ea5e9, #67e8f9);
  box-shadow: 0 0 12px rgba(103, 232, 249, 0.55);
}

@keyframes boardScroll {
  0% { transform: translateY(0); }
  100% { transform: translateY(-50%); }
}

@media (prefers-reduced-motion: reduce) {
  .scroll-board__track.animated {
    animation: none;
  }
}
</style>
