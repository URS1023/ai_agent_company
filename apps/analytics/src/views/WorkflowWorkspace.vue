<script setup lang="ts">
/**
 * 工作流页面（路由 /workflows/:id 与 /workflows/:id/:section(access-point|logs|overview|run)，权限 workflows.view）。
 * 职责：作为薄壳把 id 交给 WorkflowWorkbench；当来源是质检模块（?from=quality）时附带返回上下文。
 * 关键约束：质检上下文里的 qualitySearch 必须编码后再拼回返回路径，保证返回产品目录时能恢复搜索词；
 *   title/summary 留空表示由工作台自行根据工作流数据显示标题。
 */
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import WorkflowWorkbench from '../components/workflows/WorkflowWorkbench.vue'
const route = useRoute()
const qualityContext = computed(() => route.query.from === 'quality' ? {
  title: '', summary: '', domain: 'quality' as const,
  returnLabel: '返回质量检查',
  returnPath: '/quality' + (typeof route.query.qualitySearch === 'string' ? '?q=' + encodeURIComponent(route.query.qualitySearch) : ''),
} : undefined)
</script>
<template><WorkflowWorkbench :id="String(route.params.id || '')" :context="qualityContext" /></template>
