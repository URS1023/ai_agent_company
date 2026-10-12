<script setup lang="ts">
/**
 * 目录分页条（设备目录、质检产品目录共用）。
 * 路由：无独立路由，由 BusinessDeviceWorkspace / QualityProductWorkspace 渲染。
 * 依赖接口：无请求；分页为受控组件（total/page/pageSize 由父组件维护），翻页与改每页数以上抛事件表达。
 * 约束：page-sizes 固定 [12, 24, 48]（与 useBusinessDevices 记忆状态的白名单一致，改这里需同步）；
 *   total 为 0 时整条不渲染，避免出现空分页器。
 */
defineProps<{ total: number; page: number; pageSize: number; disabled?: boolean }>()
defineEmits<{ page: [value: number]; size: [value: number] }>()
</script>
<template>
  <nav v-if="total" class="catalog-pagination" aria-label="列表分页">
    <!-- 当前分页组件的内置条数控件没有可访问名称，使用带显式标签的原生选择器保留相同分页合同。 -->
    <label class="catalog-page-size">每页条数<select :value="pageSize" :disabled="disabled" @change="$emit('size', Number(($event.target as HTMLSelectElement).value))"><option v-for="size in [12, 24, 48]" :key="size" :value="size">{{ size }} 条</option></select></label>
    <el-pagination background :total="total" :current-page="page" :page-size="pageSize" :pager-count="5" :disabled="disabled" layout="total, prev, pager, next, jumper" @update:current-page="$emit('page', $event)" />
  </nav>
</template>
<style scoped>
.catalog-pagination { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; justify-content: flex-end; margin: 28px 0 12px; padding-top: 8px; }.catalog-pagination :deep(.el-pagination) { flex-wrap: wrap; row-gap: 12px; --el-color-primary: #1269b5; font-weight: 400; }
.catalog-page-size { display: inline-flex; align-items: center; gap: 8px; color: #475467; font-size: 13px; }.catalog-page-size select { min-height: 32px; border: 1px solid #cbd5e1; border-radius: 4px; padding: 4px 8px; background: #fff; color: #334155; }.catalog-page-size select:focus-visible { outline: 2px solid #1269b5; outline-offset: 2px; }
@media(max-width:720px) { .catalog-pagination { justify-content: center; }.catalog-pagination :deep(.el-pagination__jump) { margin: 0; } }
</style>
