<script setup lang="ts">
/**
 * 目录卡片外壳（设备卡片、质检产品卡片共用的展示容器）。
 * 路由：无独立路由；由 BusinessDeviceCard / QualityProductCard 等包裹使用。
 * 依赖接口：纯展示组件（无状态、无请求）；内容通过默认插槽（信息区）、image 与 actions 具名插槽注入。
 * 约束：tone 决定状态徽标配色（green/gray/amber），status 文案由调用方按各自业务语义给出；
 *   标题与长文本统一省略号截断（title 属性保留完整值供悬停查看）。
 */
defineProps<{ title: string; status: string; tone?: 'green' | 'gray' | 'amber'; badge?: string }>()
</script>
<template>
  <article class="catalog-card">
    <header class="catalog-card-heading">
      <h2 :title="title"><i class="fa-solid fa-table-cells-large" aria-hidden="true" /><span>{{ title }}</span></h2>
      <span v-if="badge" class="catalog-card-badge">{{ badge }}</span>
      <span class="catalog-card-status" :class="tone || 'gray'">{{ status }}</span>
    </header>
    <div class="catalog-card-body"><div class="catalog-card-info"><slot /></div><slot name="image" /></div>
    <footer class="catalog-card-actions"><slot name="actions" /></footer>
  </article>
</template>
<style scoped>
.catalog-card { display: flex; flex-direction: column; min-width: 0; min-height: 246px; padding: 20px; border: 1px solid #ebedef; border-radius: 16px; background: #fff; box-shadow: 0 3px 12px #1018280c, 0 1px 3px #10182805; }
.catalog-card-heading { display: flex; align-items: center; gap: 7px; min-width: 0; }
.catalog-card-heading h2 { display: flex; align-items: center; gap: 7px; min-width: 0; margin: 0; color: #303437; font-size: 16px; line-height: 24px; font-weight: 650; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.catalog-card-heading i { font-size: 13px; flex-shrink: 0; }
.catalog-card-heading h2 span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.catalog-card-badge { flex-shrink: 0; padding: 1px 5px; border: 1px solid #e9ebed; border-radius: 3px; color: #8d9298; font-size: 11px; background: #f6f7f8; }
.catalog-card-status { flex-shrink: 0; margin-left: auto; padding: 3px 7px; border-radius: 3px; background: #59636f; color: #fff; font-size: 12px; line-height: 19px; }
.catalog-card-status.green { background: #087443; }.catalog-card-status.amber { background: #8a5a00; }
.catalog-card-body { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex: 1; min-width: 0; padding: 18px 0 20px; }
.catalog-card-info { flex: 1; min-width: 0; }
.catalog-card-info :deep(dl) { display: grid; gap: 10px; margin: 0; font-size: 12px; }
.catalog-card-info :deep(dl > div) { display: flex; gap: 7px; min-width: 0; line-height: 18px; }
.catalog-card-info :deep(dt) { flex-shrink: 0; color: #59636f; }.catalog-card-info :deep(dd) { margin: 0; color: #505861; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.catalog-card-info :deep(.catalog-link) { color: #155e9c; }
.catalog-card-actions { display: flex; justify-content: center; margin-top: auto; min-height: 31px; }
.catalog-card-actions :deep(button) { display: inline-flex; align-items: center; justify-content: center; gap: 5px; padding: 7px 11px; min-height: 31px; border: 0; border-radius: 0; background: #1269b5; color: #fff; font-size: 12px; line-height: 18px; white-space: nowrap; }
.catalog-card-actions :deep(button:first-child) { border-radius: 3px 0 0 3px; }.catalog-card-actions :deep(button:last-child) { border-radius: 0 3px 3px 0; }
.catalog-card-actions :deep(button.catalog-danger) { background: #b42318; }.catalog-card-actions :deep(button.catalog-workflow) { background: #ffbd00; color: #493800; }
.catalog-card-actions :deep(button.catalog-secondary) { background: #8ce0b5; color: #195538; }
.catalog-card-actions :deep(button:hover:not(:disabled)) { filter: brightness(.94); }.catalog-card-actions :deep(button:disabled) { opacity: .55; cursor: not-allowed; }
.catalog-card-actions :deep(button:focus-visible) { position: relative; outline: 3px solid #76bafd; outline-offset: 3px; z-index: 1; }
@media(max-width:600px) { .catalog-card { padding: 19px; } }
</style>
