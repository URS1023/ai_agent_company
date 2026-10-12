<script setup lang="ts">
/**
 * WorkflowAggregateValuesForm —— 变量聚合器（aggregator）的分组与指标配置表单。
 *
 * 位置：WorkflowNodeForm 中 kind === 'aggregator' 时渲染。
 * 输入：`config`（节点配置）、`fields`（上游可用字段）；输出：以 update 事件回传完整 config。
 * 关键约束：
 * - 分组字段与指标字段的候选项 = 上游字段 ∪ 配置中已选值，保证历史配置（字段已不在上游）仍可显示与取消；
 * - operations 的键必须与后端聚合算子一一对应，新增算子需同步后端，否则运行时会报未知算子；
 * - 指标名称是下游引用键，重名会互相覆盖，因此默认名带序号。
 */
import { computed } from 'vue'
const props = defineProps<{ config: Record<string, any>; fields: string[] }>()
const emit = defineEmits<{ update: [config: Record<string, any>] }>()
const operations: Record<string, string> = { count: '计数', sum: '求和', avg: '平均值', min: '最小值', max: '最大值', count_distinct: '去重计数' }
const groupChoices = computed<string[]>(() => [...new Set([...props.fields, ...(props.config.groupBy || [])])])
const metricChoices = computed<string[][]>(() => (props.config.metrics || []).map((metric: any) => [...new Set([...props.fields, ...(metric.field ? [metric.field] : [])])]))
function grouping(name: string, selected: boolean) { emit('update', { ...props.config, groupBy: selected ? [...new Set([...(props.config.groupBy || []), name])] : (props.config.groupBy || []).filter((n: string) => n !== name) }) }
function patch(index: number, value: Record<string, any>) { emit('update', { ...props.config, metrics: props.config.metrics.map((m: any, i: number) => i === index ? { ...m, ...value } : m) }) }
function remove(index: number) { emit('update', { ...props.config, metrics: props.config.metrics.filter((_: any, i: number) => i !== index) }) }
function add() { emit('update', { ...props.config, metrics: [...props.config.metrics, { name: '汇总结果' + (props.config.metrics.length + 1), operation: 'count', field: '' }] }) }
</script>
<template>
  <div><details open><summary>按哪些字段分组</summary><p class="wf-note">相同单据的记录合并为一组；不选择字段时汇总全部记录。</p><label v-for="name in groupChoices" :key="name" class="wf-check"><input type="checkbox" :checked="config.groupBy?.includes(name)" @change="grouping(name, ($event.target as HTMLInputElement).checked)" />{{ name }}</label></details>
    <section v-for="(metric, index) in config.metrics" :key="index" class="wf-rule-card"><label>结果名称<input :value="metric.name" @input="patch(index, { name: ($event.target as HTMLInputElement).value })" /></label><label>怎样汇总<select :value="metric.operation" @change="patch(index, { operation: ($event.target as HTMLSelectElement).value })"><option v-for="(label, key) in operations" :key="key" :value="key">{{ label }}</option></select></label><label>计算哪个字段<select :value="metric.field" @change="patch(index, { field: ($event.target as HTMLSelectElement).value })"><option value="">{{ metric.operation === 'count' ? '全部记录' : '选择字段' }}</option><option v-for="name in metricChoices[index]" :key="name" :value="name">{{ name }}</option></select></label><button type="button" :disabled="config.metrics.length <= 1" @click="remove(index)">移除此计算</button></section><button type="button" @click="add">添加汇总计算</button>
  </div>
</template>
