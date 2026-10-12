<script setup lang="ts">
/**
 * WorkflowAppRail —— 工作流沉浸式外壳的左侧导航栏（WorkflowWorkbench 在 designer 模式下渲染）。
 *
 * 所在路由：`/workflows/:id` 及其 section 子路由（access-point / logs / overview / run），
 * 设备入口 `/alerts|/quality/devices/:deviceId/workflow/...` 同样复用本组件。
 * 依赖：无接口调用，仅接收 active/title/domain/returnLabel 并派发 navigate / leave 事件。
 * 关键约束：
 * - 导航项只覆盖 4 个 section；`run`（Web 应用页）不在列表中，需按 access-point 高亮，
 *   避免用户在 Web 应用页时左侧没有任何选中项；
 * - 图标按 domain 区分（预警/质检/通用），返回按钮文案取自父级 returnLabel，不在组件内写死路由。
 */
defineProps<{
  active: 'workflow' | 'access-point' | 'logs' | 'overview' | 'run'
  title: string
  domain?: 'alert' | 'quality'
  returnLabel?: string
}>()
defineEmits<{
  navigate: [section: 'workflow' | 'access-point' | 'logs' | 'overview']
  leave: []
}>()

const navigation = [
  { key: 'workflow', label: '编排', icon: 'fa-window-maximize' },
  { key: 'access-point', label: '访问点', icon: 'fa-burst' },
  { key: 'logs', label: '日志', icon: 'fa-file-lines' },
  { key: 'overview', label: '监测', icon: 'fa-gauge-high' },
] as const
</script>

<template>
  <aside class="wf-app-rail" aria-label="工作流应用导航">
    <button class="wf-app-rail-home" type="button" :title="returnLabel || '返回流程列表'" :aria-label="returnLabel || '返回流程列表'" @click="$emit('leave')">
      <i class="fa-regular fa-square-caret-left" aria-hidden="true" />
    </button>
    <button class="wf-app-rail-identity" type="button" :title="title" aria-label="当前工作流" @click="$emit('navigate', 'workflow')">
      <i class="fa-solid" :class="domain === 'alert' ? 'fa-robot' : domain === 'quality' ? 'fa-shield-halved' : 'fa-diagram-project'" aria-hidden="true" />
    </button>
    <nav>
      <button
        v-for="item in navigation"
        :key="item.key"
        type="button"
        :class="{ active: active === item.key || (active === 'run' && item.key === 'access-point') }"
        :title="item.label"
        :aria-label="item.label"
        :aria-current="active === item.key ? 'page' : undefined"
        @click="$emit('navigate', item.key)"
      >
        <i class="fa-solid" :class="item.icon" aria-hidden="true" />
        <span>{{ item.label }}</span>
      </button>
    </nav>
    <footer>
      <a href="/docs" target="_blank" rel="noopener" title="接口文档" aria-label="接口文档"><i class="fa-regular fa-circle-question" aria-hidden="true" /></a>
      <span class="wf-app-rail-user" title="当前用户" aria-label="当前用户">D</span>
    </footer>
  </aside>
</template>
