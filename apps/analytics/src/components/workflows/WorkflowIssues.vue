<script setup lang="ts">
/**
 * WorkflowIssues —— 运行结果中的「逐项问题」列表（WorkflowRunResult 内渲染）。
 *
 * 依赖数据：`issues` 为后端逐项判定输出（含 rowIndex / ruleName / severity / message），
 * `rows` 为同一批业务数据行，用 rowIndex 回查上下文。
 * 关键约束：
 * - rowIndex 是相对原始结果数组的下标，不能按分页后的可见行重算，否则上下文会错行；
 * - 后端对"缺少数据或判断依据"这类问题只给通用 ruleName，此时优先展示业务行里的「说明」/reason，
 *   保证用户看到的是可执行原因而不是内部规则名；
 * - 上下文列固定白名单（contextFields），避免把 recordKey 等内部字段暴露给业务用户。
 */
import { computed, shallowRef, watch } from 'vue'
const props = defineProps<{ issues: any[]; rows: Record<string, unknown>[] }>()
const page = shallowRef(0)
const contextFields = ['检查要求', '实测值', '记录范围', '记录时间', '周期天数', '上次完成时间', '换油周期', '原巡检结果', '实测说明', '参考时间']
/** 空字符串也按"未记录"呈现：后端对缺失字段可能返回 ''，直接 String() 会得到空白单元格。 */
function display(value: unknown) { return value === null || value === undefined || value === '' ? '未记录' : String(value) }
/** 把一条后端 issue 与对应业务行合并成卡片模型；rowIndex 越界时用空行兜底，不抛错。 */
const items = computed(() => props.issues.map((issue, index) => {
  const row = props.rows[issue.rowIndex] || {}
  const names = ['设备', '润滑点', '检查项目', '维修点', '质检单号', '检测项目'].map(key => row[key]).filter(value => value !== undefined && value !== null && value !== '')
  const generic = ['缺少数据或判断依据，需补全后确认', '存在需关注的异常或逾期'].includes(issue.ruleName)
  const explanation = generic && (row['说明'] || row.reason) ? String(row['说明'] || row.reason) : issue.message || '不符合当前规则'
  return { index, issue, title: names.join(' · ') || `第 ${Number(issue.rowIndex) + 1} 行`, explanation,
    conclusion: generic && row['结果'] ? String(row['结果']) : issue.ruleName,
    context: contextFields.filter(key => Object.hasOwn(row, key)).map(key => ({ key, value: display(row[key]) })) }
}))
const visible = computed(() => items.value.slice(page.value * 20, (page.value + 1) * 20))
watch(() => props.issues, () => { page.value = 0 })
</script>
<template>
  <ol class="wf-issue-list" aria-label="逐项问题原因">
    <li v-for="item in visible" :key="item.index" class="wf-issue-card">
      <header><strong>{{ item.title }}</strong><span>{{ item.conclusion }}</span></header>
      <p>{{ item.explanation }}</p>
      <dl v-if="item.context.length"><div v-for="field in item.context" :key="field.key"><dt>{{ field.key }}</dt><dd>{{ field.value }}</dd></div></dl>
      <details><summary>判定规则与数据行</summary><p>第 {{ Number(item.issue.rowIndex) + 1 }} 行 · {{ item.issue.ruleName }} · {{ ({ info: '提示', warning: '警告', critical: '严重' } as Record<string, string>)[item.issue.severity] || item.issue.severity }}</p><p>{{ item.issue.message }}</p></details>
    </li>
  </ol>
  <nav v-if="items.length > 20" class="wf-actions" aria-label="问题分页"><button :disabled="page === 0" @click="page--">上一页问题</button><span>{{ page + 1 }} / {{ Math.ceil(items.length / 20) }}</span><button :disabled="(page + 1) * 20 >= items.length" @click="page++">下一页问题</button></nav>
</template>
<style scoped>
.wf-issue-list { display: grid; gap: 10px; padding: 0; list-style: none; min-width: 0; }
.wf-issue-card { padding: 12px; border: 1px solid #e4e7ec; border-radius: 10px; background: #fff; overflow-wrap: anywhere; }
header { display: flex; justify-content: space-between; align-items: flex-start; gap: 8px; }
header strong { min-width: 0; font-size: 13px; line-height: 1.5; } header span { flex-shrink: 0; max-width: 45%; padding: 2px 6px; border-radius: 4px; background: #f2f4f7; font-size: 12px; }
p { font-size: 13px; line-height: 1.65; margin: 8px 0; } dl { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; margin: 12px 0; }
dt { font-size: 11px; color: #667085; } dd { font-size: 12px; margin: 3px 0 0; } details { font-size: 12px; color: #667085; } summary { cursor: pointer; }
</style>
