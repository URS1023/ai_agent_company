<script setup lang="ts">
/**
 * 产品目录待处理事项提示条（质检工作台顶部）。
 * 路由：无独立路由，由 QualityProductWorkspace 渲染，数据来自 api/qualityProducts.ts 的目录快照。
 * 交互约束：纯展示组件（无请求、无状态）；issues 与 pendingEntries 都为空时整块不渲染；
 *   文案强调"待确认记录仍保留在目录中"，避免用户误以为这些产品被丢弃，且不改动已配置流程的可用性。
 */
import type { QualityProductCatalogue } from '../../api/qualityProducts'

defineProps<{ catalogue: QualityProductCatalogue }>()
</script>

<template>
  <aside v-if="catalogue.issues?.length || catalogue.pendingEntries?.length" class="quality-feedback catalogue-notice" aria-label="产品目录待处理事项" role="status">
    <h2>产品目录需要处理</h2>
    <p v-for="issue in catalogue.issues" :key="issue.code">{{ issue.message }}</p>
    <ul v-if="catalogue.pendingEntries?.length">
      <li v-for="(entry, index) in catalogue.pendingEntries" :key="entry.key || index">
        <strong>{{ entry.templateName }}</strong>：{{ entry.reason }}（{{ entry.formCount }} 张历史单据）
      </li>
    </ul>
    <p>其余已配置且有权限访问的流程仍可使用。待确认记录保留在目录中，确认分类和检测要求后再配置流程。</p>
  </aside>
</template>

<style scoped>
.catalogue-notice { text-align: left; overflow-wrap: anywhere; border: 1px solid #a16207; background: #fffbeb; color: #713f12; }
</style>
