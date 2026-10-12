<script setup lang="ts">
/**
 * WorkflowTableFilters —— 直连表读取的过滤条件编辑器（WorkflowTableReadForm 内使用）。
 *
 * 依赖接口：`composables/useWorkflowTableMetadata.ReadColumn`（列元数据，含注释用于展示）。
 * 数据契约：Filter 的 operator 取值必须与后端查询构造器一致（operators 表为唯一来源）；
 * valueSource 区分常量与上游输入字段，`in` 语义固定为"匹配上游任一值"，切换时强制 valueSource = 'input'。
 * 关键约束：
 * - 每个条件的 skipEmpty 用于"上游为空时跳过该条件"，业务上常用于可选筛选，不要默认开启；
 * - 更新按索引不可变替换，不改动其他条件。
 */
import type { ReadColumn } from '../../composables/useWorkflowTableMetadata'
type Filter = { field: string; operator: string; valueSource: string; value?: string | number | boolean | null; inputField?: string; skipEmpty?: boolean }
const props = defineProps<{ filters: Filter[]; columns: ReadColumn[]; upstreamFields: string[]; device?: Record<string, any> }>()
const emit = defineEmits<{ update: [filters: Filter[]] }>()
const operators = [['eq', '等于'], ['ne', '不等于'], ['gt', '大于'], ['gte', '大于等于'], ['lt', '小于'], ['lte', '小于等于'], ['contains', '包含文字'], ['is_null', '为空'], ['not_null', '不为空'], ['in', '匹配上游任一值']]
function patch(index: number, values: Partial<Filter>) { emit('update', props.filters.map((f, i) => i === index ? { ...f, ...values } : f)) }
/** 切换操作符：`in` 必须取上游输入值，因此同时把 valueSource 固定为 input。 */
function operator(index: number, value: string) { patch(index, { operator: value, ...(value === 'in' ? { valueSource: 'input' } : {}) }) }
</script>
<template>
  <section class="table-filters">
    <div class="filter-heading"><strong>筛选条件</strong><button type="button" :disabled="filters.length >= 20" @click="emit('update', [...filters, { field: '', operator: 'eq', valueSource: 'fixed', value: '' }])">＋ 添加条件</button></div>
    <p class="wf-note">不添加条件时读取所选表的全部记录。多个条件需要同时满足。</p>
    <div v-for="(filter, index) in filters" :key="index" class="filter-card">
      <div class="filter-heading"><strong>条件 {{ index + 1 }}</strong><button type="button" :aria-label="`删除条件 ${index + 1}`" @click="emit('update', filters.filter((_, i) => i !== index))">删除</button></div>
      <label>筛选字段<select :value="filter.field" @change="patch(index, { field: ($event.target as HTMLSelectElement).value })"><option value="">选择字段</option><option v-for="c in columns" :key="c.columnName" :value="c.columnName">{{ c.comment || '无注释' }} · {{ c.columnName }}</option></select></label>
      <label>比较方式<select :value="filter.operator || 'eq'" @change="operator(index, ($event.target as HTMLSelectElement).value)"><option v-for="[value, label] in operators" :key="value" :value="value">{{ label }}</option></select></label>
      <template v-if="!['is_null', 'not_null'].includes(filter.operator)">
        <label>筛选值来源<select :value="filter.valueSource || 'fixed'" :disabled="filter.operator === 'in'" @change="patch(index, { valueSource: ($event.target as HTMLSelectElement).value })"><option value="fixed">填写固定值</option><option value="input">上游节点字段</option><option v-if="device?.id" value="device">当前设备标识</option></select></label>
        <label v-if="!filter.valueSource || filter.valueSource === 'fixed'">筛选值<input :value="String(filter.value ?? '')" placeholder="填写要匹配的值" @input="patch(index, { value: ($event.target as HTMLInputElement).value })" /></label>
        <template v-else-if="filter.valueSource === 'input'">
          <label>上游字段<select :value="filter.inputField || ''" @change="patch(index, { inputField: ($event.target as HTMLSelectElement).value })"><option value="">选择上游字段</option><option v-for="name in upstreamFields" :key="name" :value="name">{{ name }}</option></select></label>
          <p class="wf-note">{{ upstreamFields.length ? '上游有多条记录时，选择“匹配上游任一值”。上游没有记录时，本节点返回空结果。' : '先连接上游读取节点或配置开始节点的输入字段。' }}</p>
          <label class="wf-check"><input type="checkbox" :checked="filter.skipEmpty" @change="patch(index, { skipEmpty: ($event.target as HTMLInputElement).checked })" />运行表单此值留空时不限制</label>
        </template>
        <p v-else class="wf-note">使用 {{ device?.name }} 的源库设备标识匹配所选字段。</p>
      </template>
    </div>
  </section>
</template>
<style scoped>
.filter-heading { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.filter-card { border: 1px solid #e4e7ec; border-radius: 8px; padding: 10px; margin-top: 8px; background: #f9fafb; }
.table-filters { margin: 16px 0; }
</style>
