<script setup lang="ts">
/**
 * 数据分析模块的外层布局（路由 /analytics 及其子路由，权限 analytics.view）。
 * 职责：统一标题区 + 子模块页签（智能问数 / SQL 查询 / 查询历史 / 数据质量），并渲染子路由出口。
 * 约束：sections 的 path 必须与 router.ts 中 /analytics 的子路由完全一致；
 *   页签高亮用 $route.path 精确匹配，子页面内部跳转时不会误亮其它页签。
 */
const sections = [
  { path: '/analytics', label: '智能问数' }, { path: '/analytics/sql', label: 'SQL 查询' },
  { path: '/analytics/history', label: '查询历史' }, { path: '/analytics/quality', label: '数据质量' },
]
</script>
<template>
  <div class="business-page analysis-layout">
    <header class="business-heading"><div><p class="eyebrow">DATA ANALYTICS</p><h1>数据分析</h1><p>查询已授权数据，查看统计、图表和洞察，并保存可追溯的分析结果。</p></div></header>
    <nav class="module-tabs" aria-label="数据分析子模块">
      <RouterLink v-for="section in sections" :key="section.path" :to="section.path" :class="{ selected: $route.path === section.path }">{{ section.label }}</RouterLink>
    </nav>
    <RouterView />
  </div>
</template>
