<script setup lang="ts">
/**
 * WorkflowValuesPreview —— 业务判定结果的字段预览表（WorkflowRunResult 内使用）。
 *
 * 输入：`output` 为判定节点的运行输出（rowBranches 时按 trueRows/falseRows 分栏，否则读 data）。
 * 关键约束：
 * - 默认只展示业务主列（priority 白名单），其余核对字段需用户点击"查看全部核对字段"才展开，
 *   避免把 recordKey/deviceKey 等内部字段直接摊给业务用户；主列全缺失时自动回退为全量列；
 * - hidden 集合是内部字段黑名单，新增内部键需同步加入，否则会泄漏到界面；
 * - 空值统一显示"未记录"（对象类型转 JSON 字符串），不能用空白单元格表达缺失；
 * - 分页步长 20，切换输出或分支时必须回到第 1 页。
 */
import { computed, shallowRef, watch } from 'vue'
const props = defineProps<{ output: Record<string, any> }>()
const branch = shallowRef<'true' | 'false'>('true'), page = shallowRef(0), showEvidence = shallowRef(false)
const rows = computed<Record<string, any>[]>(() => props.output.rowBranches ? props.output[branch.value + 'Rows']?.data || [] : props.output.data || [])
const hidden = new Set(['recordKey', 'deviceKey', 'planId', 'itemId', 'category', 'status', 'reason', 'alertCount', 'pendingCount'])
const priority = ['设备', '润滑点', '检查项目', '维修点', '质检单号', '单据', '检验项目', '项目', '标准原文', '标准', '测量值', '实测值', '结果', '说明', '检查要求', '记录范围', '记录时间', '周期天数', '上次完成时间', '上次完成', '应完成时间']
/** 列顺序：业务主列按 priority 顺序靠前，其余按数据中首次出现顺序补齐。 */
const columns = computed(() => {
  const available = [...new Set(rows.value.flatMap(row => Object.keys(row)))].filter(k => !hidden.has(k))
  const main = priority.filter(k => available.includes(k))
  return showEvidence.value || !main.length ? [...main, ...available.filter(k => !priority.includes(k))] : main
})
const visible = computed(() => rows.value.slice(page.value * 20, (page.value + 1) * 20))
const columnLabels = computed<Record<string, string>>(() => Object.fromEntries((Array.isArray(props.output.columns) ? props.output.columns : []).filter((c: any) => c?.columnName && c.comment).map((c: any) => [c.columnName, c.comment])))
watch([() => props.output, branch], () => { page.value = 0 })
/** 单元格呈现：null/undefined/空串统一为"未记录"，对象序列化后展示，避免出现 [object Object]。 */
function display(value: unknown) { return value === null || value === undefined || value === '' ? '未记录' : typeof value === 'object' ? JSON.stringify(value) : String(value) }
</script>
<template>
  <div class="value-preview">
    <button type="button" :aria-pressed="showEvidence" @click="showEvidence = !showEvidence">{{ showEvidence ? '仅显示业务结果' : '查看全部核对字段' }}</button>
    <div v-if="output.rowBranches" class="value-branches" aria-label="判断分支结果"><button type="button" :aria-pressed="branch === 'true'" @click="branch = 'true'">满足 IF · {{ output.trueRows?.data?.length || 0 }} 条</button><button type="button" :aria-pressed="branch === 'false'" @click="branch = 'false'">不满足 ELSE · {{ output.falseRows?.data?.length || 0 }} 条</button></div>
    <p v-if="!rows.length">此路径没有记录。</p>
    <template v-else><p>共 {{ rows.length }} 条记录；空值显示为“未记录”。</p><div class="value-table-scroll" tabindex="0" aria-label="节点结果表格，可横向滚动"><table><thead><tr><th v-for="column in columns" :key="column">{{ columnLabels[column] || column }}<small v-if="columnLabels[column]" class="value-column-name">{{ column }}</small></th></tr></thead><tbody><tr v-for="(row, index) in visible" :key="index"><td v-for="column in columns" :key="column" :class="{ missing: row[column] === null || row[column] === undefined }">{{ display(row[column]) }}</td></tr></tbody></table></div><nav v-if="rows.length > 20" aria-label="结果分页"><button type="button" :disabled="page === 0" @click="page--">上一页</button><span>{{ page + 1 }} / {{ Math.ceil(rows.length / 20) }}</span><button type="button" :disabled="(page + 1) * 20 >= rows.length" @click="page++">下一页</button></nav></template>
  </div>
</template>
<style scoped>
.value-preview { min-width: 0; font-size: 13px; }
.value-column-name { display: block; margin-top: 3px; font-size: 11px; font-weight: 400; color: #667085; }
.value-branches, nav { display: flex; gap: 8px; align-items: center; }
.value-branches button[aria-pressed=true] { color: #155eef; background: #eef4ff; }
.value-table-scroll { max-width: 100%; overflow: auto; border: 1px solid #eaecf0; border-radius: 8px; }
table { border-collapse: collapse; min-width: 100%; } th, td { padding: 9px 12px; text-align: left; border-bottom: 1px solid #eaecf0; min-width: 110px; max-width: 280px; overflow-wrap: anywhere; }
th { white-space: nowrap; background: #f9fafb; } td.missing { color: #667085; } nav { justify-content: flex-end; margin-top: 10px; }
</style>
