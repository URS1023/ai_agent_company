<script setup lang="ts">
/**
 * WorkflowTableMatching —— 直连表读取的字段别名 / 输入匹配 / 排序配置（WorkflowTableReadForm 内使用）。
 *
 * 依赖接口：`composables/useWorkflowTableMetadata.ReadColumn`。
 * 关键约束：
 * - fieldAliases 是"物理列名 → 业务字段名"的映射，下游引用的是业务名，因此别名必须唯一；
 *   这里只在值为空时删除键，不做去重校验（由后端模板锁校验兜底）；
 * - matches 定义上游输入与列的对应关系，orderBy 定义排序；三者都按索引不可变替换。
 */
import type { ReadColumn } from '../../composables/useWorkflowTableMetadata'
const props = defineProps<{ config: Record<string, any>; columns: ReadColumn[]; upstreamFields: string[] }>()
const emit = defineEmits<{ update: [value: Record<string, any>] }>()
function patch(value: Record<string, any>) { emit('update', { ...props.config, ...value }) }
function match(index: number, value: Record<string, string>) { patch({ matches: props.config.matches.map((m: any, i: number) => i === index ? { ...m, ...value } : m) }) }
function order(index: number, value: Record<string, string>) { patch({ orderBy: props.config.orderBy.map((m: any, i: number) => i === index ? { ...m, ...value } : m) }) }
/** 设置/清除字段别名：空值即删除该键，避免留下空别名影响下游引用。 */
function alias(field: string, value: string) { const aliases = { ...props.config.fieldAliases }; if (value.trim()) aliases[field] = value.trim(); else delete aliases[field]; patch({ fieldAliases: aliases }) }
</script>
<template>
  <section class="table-matching">
    <label>与上游数据的关系<select :value="config.matchMode || 'off'" @change="patch({ matchMode: ($event.target as HTMLSelectElement).value })"><option value="off">只输出本次读取的数据</option><option value="all">按字段关联，保留全部匹配记录</option><option value="latest">按字段关联，取排序第一条记录</option><option value="unique">按字段关联，仅采用唯一匹配记录</option></select></label>
    <template v-if="config.matchMode && config.matchMode !== 'off'">
      <p class="wf-note">保留上游每一行并补充本表字段。未匹配时补充字段为空；不唯一时可通过匹配数量判断，避免借用其他设备或单据的数据。</p>
      <div v-for="(item, index) in config.matches || []" :key="index" class="match-row">
        <label>本表关联字段<select :value="item.field" @change="match(index, { field: ($event.target as HTMLSelectElement).value })"><option value="">选择字段</option><option v-for="c in columns" :key="c.columnName" :value="c.columnName">{{ c.comment || '无注释' }} · {{ c.columnName }}</option></select></label>
        <label>对应上游字段<select :value="item.inputField" @change="match(index, { inputField: ($event.target as HTMLSelectElement).value })"><option value="">选择上游字段</option><option v-for="field in upstreamFields" :key="field">{{ field }}</option></select></label>
        <button type="button" @click="patch({ matches: config.matches.filter((_: unknown, i: number) => i !== index) })">删除对应关系</button>
      </div>
      <button type="button" :disabled="(config.matches?.length || 0) >= 8" @click="patch({ matches: [...(config.matches || []), { field: '', inputField: '' }] })">＋ 添加字段对应关系</button>
      <label>匹配数量输出名称<input :value="config.matchCountField || '匹配记录数'" @change="patch({ matchCountField: ($event.target as HTMLInputElement).value })" /></label>
      <details :open="!!config.keepInputFields?.length"><summary>保留的上游字段</summary><p class="wf-note">未勾选时保留全部上游字段。勾选后仅保留所选字段及本次读取值。</p><label v-for="field in upstreamFields" :key="field" class="wf-check"><input type="checkbox" :checked="config.keepInputFields?.includes(field)" @change="patch({ keepInputFields: ($event.target as HTMLInputElement).checked ? [...(config.keepInputFields || []), field] : (config.keepInputFields || []).filter((f: string) => f !== field) })" />{{ field }}</label></details>
    </template>
    <template v-else><label>读取范围<select :value="config.take ? 'limited' : 'all'" @change="patch({ take: ($event.target as HTMLSelectElement).value === 'all' ? null : 100 })"><option value="all">全部符合条件的记录</option><option value="limited">排序后只取前 N 条</option></select></label><label v-if="config.take">本次最多读取<input type="number" min="1" max="10000" :value="config.take" @change="patch({ take: Number(($event.target as HTMLInputElement).value) })" /></label></template>
    <details :open="config.matchMode === 'latest' || !!config.orderBy?.length"><summary>排序与最近记录</summary>
      <p class="wf-note">按顺序比较排序字段。取最近记录时，先选完成时间降序，再选唯一标识，确保相同时间的记录排序稳定。</p>
      <div v-for="(item, index) in config.orderBy || []" :key="index" class="match-row">
        <label>排序字段<select :value="item.field" @change="order(index, { field: ($event.target as HTMLSelectElement).value })"><option value="">选择字段</option><option v-for="c in columns" :key="c.columnName" :value="c.columnName">{{ c.comment || '无注释' }} · {{ c.columnName }}</option></select></label>
        <label>排序方向<select :value="item.direction" @change="order(index, { direction: ($event.target as HTMLSelectElement).value })"><option value="desc">从大到小 / 从新到旧</option><option value="asc">从小到大 / 从旧到新</option></select></label>
        <button type="button" @click="patch({ orderBy: config.orderBy.filter((_: unknown, i: number) => i !== index) })">删除排序</button>
      </div>
      <button type="button" :disabled="(config.orderBy?.length || 0) >= 5" @click="patch({ orderBy: [...(config.orderBy || []), { field: '', direction: 'desc' }] })">＋ 添加排序字段</button>
    </details>
    <details :open="!!Object.keys(config.fieldAliases || {}).length"><summary>字段输出名称</summary>
      <p class="wf-note">设置后，后续计算和判断使用此名称。关联读取时使用不同名称，保留原要求和实际值。</p>
      <label v-for="field in config.fields || []" :key="field">{{ columns.find(c => c.columnName === field)?.comment || field }}<small>{{ field }}</small><input :aria-label="field + '输出名称'" :placeholder="field" :value="config.fieldAliases?.[field] || ''" @change="alias(field, ($event.target as HTMLInputElement).value)" /></label>
    </details>
  </section>
</template>
<style scoped>
.table-matching { display: grid; gap: 12px; margin: 16px 0; }
.match-row { display: grid; gap: 8px; padding: 10px; margin: 8px 0; border: 1px solid #e4e7ec; border-radius: 8px; }
.table-matching small { display: block; color: #667085; overflow-wrap: anywhere; }
</style>
