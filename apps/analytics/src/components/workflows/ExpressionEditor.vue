<script setup lang="ts">
/**
 * ExpressionEditor —— 规则表达式的递归编辑器（逐项规则判定/条件分支/计算节点共用）。
 *
 * 依赖接口：`api/workflows.copy`、`utils/workflowJson` 的 parseWorkflowJson（宽松解析）与
 * parseRuleConstant / formatRuleConstant（常量文本与类型的互转）、WorkflowValueOperand（值来源选择）。
 * 关键约束：
 * - 组件自引用递归（模板中渲染自身），嵌套深度上限 8 层；超过时只允许用"表达式 JSON"编辑，
 *   避免用户构造出前后端都难以处理的超深表达式；
 * - op 与 args 的元数必须匹配后端契约：present/missing 无比较值，between/outside 需要上下限，
 *   in/not_in 的集合必须写成 JSON 数组；切换判断方式时会按新元数重排既有参数；
 * - 常量解析失败时回退显示上一个合法值并提示错误，绝不把非法文本写入表达式。
 */
import { computed, shallowRef, watch } from 'vue'
import { copy } from '../../api/workflows'
import { formatRuleConstant, parseRuleConstant, parseWorkflowJson } from '../../utils/workflowJson.js'
import WorkflowValueOperand from './WorkflowValueOperand.vue'
const props = defineProps<{ modelValue: Record<string, any>; fields?: string[]; depth?: number }>()
const emit = defineEmits<{ 'update:modelValue': [value: Record<string, any>] }>()
const op = computed(() => props.modelValue.op || 'custom')
const group = computed(() => op.value === 'and' || op.value === 'or')
const supported = ['gte', 'gt', 'lte', 'lt', 'eq', 'ne', 'between', 'outside', 'in', 'not_in', 'present', 'missing']
const advanced = shallowRef(false), json = shallowRef(''), error = shallowRef('')
watch(() => props.modelValue, value => { json.value = JSON.stringify(value, null, 2) }, { immediate: true, deep: true })
const names: Record<string, string> = { gte: '大于等于', gt: '大于', lte: '小于等于', lt: '小于', eq: '等于', ne: '不等于', between: '范围内（含边界）', outside: '范围外', in: '属于集合', not_in: '不属于集合', present: '有值', missing: '缺失或空值' }
/**
 * 切换判断方式并按新元数重排参数：从单条比较切到 AND/OR 时把原表达式整体降为子条件，
 * 反向切换时取第一个子条件，缺失的位置按新操作符补默认比较值。
 */
function changeOp(next: string) {
  if (next === 'and' || next === 'or') emit('update:modelValue', { op: next, args: group.value ? props.modelValue.args : [copy(props.modelValue)] })
  else {
    const old = props.modelValue.args || []
    const comparison = (index: number, fallback: number) => old[index]?.field ? copy(old[index]) : { value: old[index]?.value ?? fallback }
    emit('update:modelValue', { op: next, args: [{ field: old[0]?.field || '' }, ...(['present', 'missing'].includes(next) ? [] : ['in', 'not_in'].includes(next) ? [{ value: Array.isArray(old[1]?.value) ? old[1].value : [] }] : [comparison(1, 0)]), ...(['between', 'outside'].includes(next) ? [comparison(2, 100)] : [])] })
  }
}
function arg(index: number, value: object) { const next = copy(props.modelValue); next.args[index] = value; emit('update:modelValue', next) }
/** 常量输入解析：失败时把输入框回退到原值并提示错误，不写入无法解析的文本。 */
function constant(index: number, event: Event) {
  const input = event.target as HTMLInputElement
  try { const value = parseRuleConstant(input.value); arg(index, { value }); error.value = ''; input.value = formatRuleConstant(value) }
  catch (cause) { input.value = formatRuleConstant(props.modelValue.args?.[index]?.value); error.value = (cause as Error).message }
}
function add() { emit('update:modelValue', { ...props.modelValue, args: [...props.modelValue.args, { op: 'gte', args: [{ field: '' }, { value: 0 }] }] }) }
/** 移除子条件：至少保留一条，空表达式会被后端判为无效规则。 */
function remove(index: number) { const args = props.modelValue.args.filter((_: unknown, i: number) => i !== index); if (args.length) emit('update:modelValue', { ...props.modelValue, args }) }
function applyJson() { try { const value = parseWorkflowJson(json.value); if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error('表达式需要 JSON 对象'); emit('update:modelValue', value); error.value = '' } catch (cause) { error.value = (cause as Error).message } }
</script>
<template>
  <div class="wf-expression">
    <label>判断方式<select :value="op" @change="changeOp(($event.target as HTMLSelectElement).value)"><option v-for="(label, key) in names" :key="key" :value="key">{{ label }}</option><option value="and">全部满足 AND</option><option value="or">任一满足 OR</option><option v-if="op === 'custom' || ![...supported, 'and', 'or'].includes(op)" :value="op">自定义表达式</option></select></label>
    <template v-if="group"><div v-for="(item, index) in modelValue.args" :key="index" class="wf-expression-child"><ExpressionEditor v-if="(depth || 0) < 8" :model-value="item" :fields="fields" :depth="(depth || 0) + 1" @update:model-value="arg(index, $event)" /><span v-else>嵌套层级较多，请使用表达式 JSON 编辑。</span><button type="button" :disabled="modelValue.args.length <= 1" @click="remove(index)">移除此条件</button></div><button type="button" @click="add">添加条件</button></template>
    <template v-else-if="supported.includes(op)"><label>判断字段<select v-if="fields?.length" :value="modelValue.args?.[0]?.field || ''" @change="arg(0, { field: ($event.target as HTMLSelectElement).value })"><option value="">选择字段</option><option v-if="modelValue.args?.[0]?.field && !fields.includes(modelValue.args[0].field)" :value="modelValue.args[0].field">{{ modelValue.args[0].field }}（请核对上游）</option><option v-for="name in fields" :key="name" :value="name">{{ name }}</option></select><input v-else :value="modelValue.args?.[0]?.field || ''" placeholder="先配置读取字段" @input="arg(0, { field: ($event.target as HTMLInputElement).value })" /></label>
      <WorkflowValueOperand v-for="index in (['present', 'missing', 'in', 'not_in'].includes(op) ? [] : ['between', 'outside'].includes(op) ? [1, 2] : [1])" :key="'value-' + index" :model-value="modelValue.args?.[index]?.field ? { source: 'field', field: modelValue.args[index].field } : { source: 'constant', value: modelValue.args?.[index]?.value ?? 0 }" :fields="fields || []" :label="index === 2 ? '上限' : ['between', 'outside'].includes(op) ? '下限' : '与什么比较'" @update:model-value="arg(index, $event.source === 'field' ? { field: $event.field } : { value: $event.value })" />
      <label v-for="index in (['in', 'not_in'].includes(op) ? [1] : [])" :key="index">{{ index === 2 ? '上限' : ['between', 'outside'].includes(op) ? '下限' : ['in', 'not_in'].includes(op) ? '集合（JSON 数组）' : '比较值' }}<input :value="formatRuleConstant(modelValue.args?.[index]?.value)" @change="constant(index, $event)" /></label><small v-if="['in', 'not_in'].includes(op)">数值可直接输入；集合使用 JSON 数组，例如 ["华北","华东"]；精确长小数或编号用双引号包裹；布尔值用 true/false。范围和大小比较按数值处理，等于/不等于会区分文本与数值类型。</small></template>
    <p v-if="error" class="wf-error" role="alert">{{ error }}</p><button type="button" class="wf-link" @click="advanced = !advanced">{{ advanced ? '收起' : '编辑' }}表达式 JSON</button><div v-if="advanced"><label>完整表达式<textarea v-model="json" rows="6" /></label><button type="button" @click="applyJson">应用表达式</button></div>
  </div>
</template>
