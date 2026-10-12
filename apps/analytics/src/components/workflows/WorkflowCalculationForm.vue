<script setup lang="ts">
/**
 * WorkflowCalculationForm —— 计算值节点（calculate-values）的计算项配置表单。
 *
 * 位置：WorkflowNodeForm 中 kind === 'calculate-values' 时渲染。
 * 输入：`config`、`fields`、`disabled`；输出：update 事件回传完整 config。
 * 关键约束：
 * - operations 的键与中文标签必须与后端计算算子契约保持一致（含业务专用算子：
 *   读取标准上下限/区间、按对应关系转换、按项目选择实测字段等），前端只做选择与参数编排；
 * - binary 集合标记需要左/右两个操作数的算子，切换算子时必须同步清理或补齐右操作数，
 *   否则会留下后端无法解析的残缺计算项（如 now 需要把左值置为常量）；
 * - 计算项名称是下游字段引用键，重名会互相覆盖。
 */
import { computed } from 'vue'
import WorkflowValueOperand from './WorkflowValueOperand.vue'
const props = defineProps<{ config: Record<string, any>; fields: string[]; disabled: boolean }>()
const emit = defineEmits<{ update: [config: Record<string, any>] }>()
const operations: Record<string, string> = { days_between: '计算日期间隔（天）', hours_between: '计算时间间隔（小时）', add: '相加', subtract: '相减', multiply: '相乘', divide: '相除', copy: '取值', number: '读取单一数值', lower_limit: '读取标准中的数值下限', upper_limit: '读取标准中的数值上限', unit: '读取数值单位', range_lower: '读取区间下限', range_upper: '读取区间上限', range_unit: '读取区间单位', lookup: '按对应关系转换', contains: '文本是否包含' }
Object.assign(operations, { coalesce: '为空时使用另一个值', now: '当前检查时间', add_hours: '日期增加小时数', add_months: '日期增加日历月数', mapped_field: '按项目选择对应实测字段' })
const binary = new Set(['days_between', 'hours_between', 'add', 'subtract', 'multiply', 'divide', 'contains', 'coalesce', 'add_hours', 'add_months'])
const calculations = computed<any[]>(() => props.config.calculations || [])
function patch(index: number, values: Record<string, any>) { emit('update', { ...props.config, calculations: calculations.value.map((c, i) => i === index ? { ...c, ...values } : c) }) }
/** 切换算子：now 无操作数需清空左值，二元算子需保证右操作数存在，其余算子清空右值。 */
function operation(index: number, value: string) { patch(index, { operation: value, ...(value === 'now' ? { left: { source: 'constant', value: null } } : {}), right: binary.has(value) ? calculations.value[index].right || { source: 'constant', value: 0 } : null }) }
function remove(index: number) { emit('update', { ...props.config, calculations: calculations.value.filter((_, i) => i !== index) }) }
function add() { emit('update', { ...props.config, calculations: [...calculations.value, { name: '计算结果' + (calculations.value.length + 1), operation: 'subtract', left: { source: 'field', field: props.fields[0] || '' }, right: { source: 'constant', value: 0 } }] }) }
function available(index: number) { return [...new Set([...props.fields, ...calculations.value.slice(0, index).map(c => c.name)])] }
function mapping(index: number, key: string, value: string) { patch(index, { mapping: { ...calculations.value[index].mapping, [key]: value === '' ? null : Number.isFinite(Number(value)) ? Number(value) : value } }) }
function mappingKey(index: number, key: string, value: string) { if (value.trim() && (value === key || !Object.hasOwn(calculations.value[index].mapping || {}, value))) patch(index, { mapping: Object.fromEntries(Object.entries(calculations.value[index].mapping || {}).map(([k, v]) => [k === key ? value : k, v])) }) }
function removeMapping(index: number, key: string) { patch(index, { mapping: Object.fromEntries(Object.entries(calculations.value[index].mapping || {}).filter(([k]) => k !== key)) }) }
</script>
<template>
  <div class="value-calculations">
    <section v-for="(item, index) in calculations" :key="index" class="wf-rule-card">
      <label>结果名称<input :value="item.name" placeholder="例如：距上次保养天数" @input="patch(index, { name: ($event.target as HTMLInputElement).value })" /></label>
      <label>怎样计算<select :value="item.operation" @change="operation(index, ($event.target as HTMLSelectElement).value)"><option v-for="(name, key) in operations" :key="key" :value="key">{{ name }}</option></select></label>
      <WorkflowValueOperand v-if="item.operation !== 'now'" :model-value="item.left" :fields="available(index)" :label="item.operation.endsWith('between') ? '开始时间' : '第一个值'" :disabled="disabled" @update:model-value="patch(index, { left: $event })" />
      <WorkflowValueOperand v-if="binary.has(item.operation)" :model-value="item.right || { source: 'constant', value: 0 }" :fields="available(index)" :label="item.operation.endsWith('between') ? '结束时间' : '第二个值'" :disabled="disabled" @update:model-value="patch(index, { right: $event })" />
      <div v-if="item.operation === 'mapped_field'" class="value-mapping"><div v-for="(value, key) in item.mapping || {}" :key="key"><label>项目编码<input :value="key" @change="mappingKey(index, String(key), ($event.target as HTMLInputElement).value)" /></label><label>对应实测字段<select :value="value" @change="patch(index, { mapping: { ...item.mapping, [key]: ($event.target as HTMLSelectElement).value } })"><option value="">选择字段</option><option v-for="field in available(index)" :key="field">{{ field }}</option></select></label><button type="button" @click="removeMapping(index, String(key))">删除对应字段</button></div><button type="button" @click="patch(index, { mapping: { ...(item.mapping || {}), ['新项目' + Date.now()]: '' } })">添加项目与字段对应关系</button></div>
      <div v-if="item.operation === 'lookup'" class="value-mapping"><div v-for="(value, key) in item.mapping || {}" :key="key"><label>原值<input :value="key" @change="mappingKey(index, String(key), ($event.target as HTMLInputElement).value)" /></label><label>转换后的值<input :value="value ?? ''" @change="mapping(index, String(key), ($event.target as HTMLInputElement).value)" /></label><button type="button" @click="removeMapping(index, String(key))">删除对应值</button></div><button type="button" @click="patch(index, { mapping: { ...(item.mapping || {}), ['新值' + Date.now()]: null } })">添加对应值</button></div>
      <button type="button" :disabled="calculations.length === 1" @click="remove(index)">移除此计算</button>
    </section>
    <button type="button" @click="add">添加计算</button>
    <p class="wf-note">缺少值、日期无效或除数为零时，计算结果为空；可在下一步判断“缺失或空值”。日期间隔保留小数，避免尚未满一天时被舍入。</p>
  </div>
</template>
