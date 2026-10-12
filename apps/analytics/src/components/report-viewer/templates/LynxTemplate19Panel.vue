<script setup lang="ts">
/**
 * LynxTemplate19 大屏面板外壳（标题 + 角装饰 + 默认插槽内容）。
 * 路由：无独立路由，由 components/report-viewer/templates 下的大屏模板复用（Template19 系列布局）。
 * 依赖接口：纯展示组件（无状态、无请求）；variant 只影响样式类名，不改变 DOM 语义。
 * 约束：与工作流/大屏域重叠的模板组件，本文件仅补充注释，不改动模板结构。
 */
defineProps<{
  title: string
  variant?: 'ranking' | 'table' | 'donut' | 'trend'
}>()

defineSlots<{
  default(): any
}>()
</script>

<template>
  <section class="lynx-t19-panel" :class="`variant-${variant || 'table'}`">
    <header class="panel-heading">
      <span aria-hidden="true"><i></i></span>
      <h2>{{ title }}</h2>
      <b aria-hidden="true"><i></i><i></i><i></i></b>
    </header>
    <div v-if="$slots.default" class="panel-body">
      <slot />
    </div>
    <span class="corner corner-tl" aria-hidden="true"></span>
    <span class="corner corner-tr" aria-hidden="true"></span>
    <span class="corner corner-bl" aria-hidden="true"></span>
    <span class="corner corner-br" aria-hidden="true"></span>
  </section>
</template>

<style scoped>
.lynx-t19-panel {
  position: absolute;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  border: 1px solid rgba(21, 160, 255, .46);
  color: #e9f8ff;
  background:
    linear-gradient(180deg, rgba(8, 37, 83, .975), rgba(2, 17, 52, .985)),
    repeating-linear-gradient(0deg, transparent 0 28px, rgba(36, 154, 255, .035) 28px 29px);
  box-shadow:
    inset 0 0 30px rgba(24, 116, 218, .1),
    0 0 17px rgba(1, 112, 220, .12);
}
.lynx-t19-panel::before {
  position: absolute;
  top: 0;
  right: 21%;
  left: 21%;
  height: 1px;
  background: linear-gradient(90deg, transparent, #21cfff 18%, #2c80ff 82%, transparent);
  box-shadow: 0 0 8px rgba(33, 207, 255, .65);
  content: '';
}
.panel-heading {
  position: relative;
  z-index: 2;
  display: flex;
  height: 41px;
  align-items: center;
  padding: 0 16px;
  border-bottom: 1px solid rgba(39, 126, 255, .15);
  background: linear-gradient(90deg, rgba(18, 87, 164, .25), transparent 72%);
}
.panel-heading > span {
  position: relative;
  width: 20px;
  height: 20px;
  flex: 0 0 auto;
  margin-right: 8px;
  border: 1px solid rgba(42, 186, 255, .7);
  border-radius: 50%;
  box-shadow: 0 0 10px rgba(27, 181, 255, .44);
}
.panel-heading > span::before,
.panel-heading > span::after,
.panel-heading > span i {
  position: absolute;
  border-radius: 50%;
  content: '';
}
.panel-heading > span::before { inset: 4px; border: 1px solid #39d8ff; }
.panel-heading > span::after { inset: 8px; background: #52e4ff; box-shadow: 0 0 8px #2cc9ff; }
.panel-heading > span i { top: 1px; left: 8px; width: 3px; height: 7px; background: #a6f3ff; }
.panel-heading h2 {
  min-width: 0;
  margin: 0;
  overflow: hidden;
  color: #effaff;
  font: 600 17px/1 'Microsoft YaHei UI', sans-serif;
  letter-spacing: .035em;
  text-overflow: ellipsis;
  text-shadow: 0 0 9px rgba(42, 185, 255, .48);
  white-space: nowrap;
}
.panel-heading > b {
  display: flex;
  gap: 7px;
  margin-left: auto;
  transform: skewX(28deg);
}
.panel-heading > b i {
  width: 20px;
  height: 5px;
  background: linear-gradient(90deg, rgba(40, 185, 255, .12), rgba(40, 185, 255, .7));
}
.panel-body {
  position: absolute;
  inset: 42px 12px 12px;
  z-index: 1;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}
.corner {
  position: absolute;
  z-index: 3;
  width: 35px;
  height: 35px;
  pointer-events: none;
}
.corner::before,
.corner::after { position: absolute; background: #22c7ff; box-shadow: 0 0 7px rgba(34, 199, 255, .5); content: ''; }
.corner::before { width: 100%; height: 2px; }
.corner::after { width: 2px; height: 100%; }
.corner-tl { top: 4px; left: 4px; }
.corner-tr { top: 4px; right: 4px; transform: scaleX(-1); }
.corner-bl { bottom: 4px; left: 4px; transform: scaleY(-1); }
.corner-br { right: 4px; bottom: 4px; transform: scale(-1); }
</style>
