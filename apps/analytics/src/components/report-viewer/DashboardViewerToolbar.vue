<script setup lang="ts">
/**
 * 大屏查看工具栏（返回、刷新、编辑、全屏 + 连接状态指示）。
 * 路由：无独立路由，由 ReportViewer（/report/view/:id）渲染。
 * 依赖接口：纯展示组件，无状态无请求；所有动作以事件上抛（back/refresh/edit/fullscreen）。
 * 关键交互：加载中或读取失败时禁用刷新；"编辑大屏"按钮仅在 canEdit 时出现（父组件传 dashboards.edit 判定结果）。
 * 约束：工具栏按钮均有 sr-only 文案与 aria-label（无障碍要求），状态指示用 title 说明刷新间隔，不得改成纯颜色提示。
 */
defineProps<{
  canEdit: boolean
  loading: boolean
  hasError: boolean
  refreshSeconds: number
  fullscreen: boolean
  referenceSampleCount?: number
}>()

const emit = defineEmits<{
  back: []
  refresh: []
  edit: []
  fullscreen: []
}>()
</script>

<template>
  <nav class="viewer-command viewer-tools" aria-label="数字大屏操作">
    <span v-if="referenceSampleCount" class="reference-sample-label" title="参考图示例未绑定企业数据，可进入编辑器替换数据">参考示例</span>
    <span
      class="viewer-health"
      :class="{ loading, error: hasError }"
      :title="hasError ? '报表读取失败' : (loading ? '正在读取报表' : (refreshSeconds ? `已连接，每 ${refreshSeconds} 秒刷新` : '数据已连接'))"
      aria-hidden="true"
    ></span>
    <button type="button" title="返回报表列表" aria-label="返回报表列表" @click="emit('back')">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 18l-6-6 6-6M9 12h10" /></svg>
      <span class="sr-only">返回报表列表</span>
    </button>
    <i aria-hidden="true"></i>
    <button type="button" title="刷新数据" aria-label="刷新数据" :disabled="loading || hasError" @click="emit('refresh')">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 7v5h-5M4 17v-5h5M6.1 8.2A7 7 0 0118.8 9M17.9 15.8A7 7 0 015.2 15" /></svg>
      <span class="sr-only">刷新数据</span>
    </button>
    <button v-if="canEdit" type="button" title="编辑大屏" aria-label="编辑大屏" @click="emit('edit')">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 20h4l10.5-10.5a2.1 2.1 0 00-4-3L4 17v3zM13.5 7.5l3 3" /></svg>
      <span class="sr-only">编辑</span>
    </button>
    <button type="button" :title="fullscreen ? '退出全屏' : '进入全屏'" :aria-label="fullscreen ? '退出全屏' : '进入全屏'" @click="emit('fullscreen')">
      <svg v-if="!fullscreen" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 3H3v5M16 3h5v5M8 21H3v-5M16 21h5v-5" /></svg>
      <svg v-else viewBox="0 0 24 24" aria-hidden="true"><path d="M3 8h5V3M21 8h-5V3M3 16h5v5M21 16h-5v5" /></svg>
      <span v-if="fullscreen" class="sr-only">退出全屏</span>
      <span v-else class="sr-only">进入全屏</span>
    </button>
  </nav>
</template>

<style scoped>
.viewer-command { position: fixed; top: 14px; left: 0; z-index: 300; display: flex; align-items: center; gap: 3px; height: 38px; padding: 0 5px 0 9px; border: 1px solid rgba(96, 190, 230, .22); border-left: 0; border-radius: 0 8px 8px 0; background: rgba(2, 11, 25, .72); box-shadow: 0 10px 34px rgba(0, 0, 0, .24); opacity: .72; backdrop-filter: blur(13px) saturate(125%); transform: translateX(0); transition: opacity 150ms ease, border-color 150ms ease, background 150ms ease; }
.viewer-command:hover, .viewer-command:focus-within { border-color: rgba(77, 207, 255, .34); background: rgba(2, 11, 25, .92); opacity: 1; transform: translateX(0); }
.viewer-command > i { width: 1px; height: 17px; margin: 0 2px; background: rgba(146, 204, 228, .18); }
.viewer-health { width: 6px; height: 6px; margin-right: 3px; border-radius: 50%; background: #42e5a5; box-shadow: 0 0 9px rgba(66, 229, 165, .82); }
.viewer-health.loading { background: #43b9ff; box-shadow: 0 0 9px rgba(67, 185, 255, .82); animation: health-pulse 1.1s ease-in-out infinite alternate; }
.viewer-health.error { background: #ff765f; box-shadow: 0 0 9px rgba(255, 118, 95, .82); animation: none; }
button { display: grid; width: 31px; height: 31px; place-items: center; padding: 0; border: 0; border-radius: 5px; color: #b8d2df; background: transparent; cursor: pointer; }
button:hover { color: #f4fcff; background: rgba(55, 184, 235, .14); }
button:focus-visible { outline: 2px solid #4ed6ff; outline-offset: 1px; color: #fff; }
button:disabled { opacity: .35; cursor: not-allowed; }
svg { width: 17px; height: 17px; fill: none; stroke: currentColor; stroke-width: 1.75; stroke-linecap: round; stroke-linejoin: round; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
.reference-sample-label { color: #ffc66e; font-size: 10px; white-space: nowrap; padding-right: 4px; }
@keyframes health-pulse { to { opacity: .35; transform: scale(.78); } }
@media (max-width: 640px) { .viewer-command { top: 8px; opacity: .36; } }
@media (prefers-reduced-motion: reduce) { .viewer-command, .viewer-health { transition: none; animation: none; } }
</style>
