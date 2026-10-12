<script setup lang="ts">
/**
 * WorkflowStartInputForm —— 开始节点输入表单（调试面板与 Web 应用页共用）。
 *
 * 位置：WorkflowDebugPanel（草稿/正式调试）与 WorkflowPublishedRunner（正式运行页）。
 * 依赖接口：`api/workflows.StartInputVariable`、WorkflowFileInput（file / file-list 类型）。
 * 数据契约：v-model 始终是 JSON 字符串（即最终提交给后端的 inputs），本组件负责文本 ↔ 结构互转。
 * 关键约束：
 * - 必须用 lastEmitted 标记自己刚写出的 JSON，否则父级回灌的等值 modelValue 会把用户正在输入的
 *   草稿覆盖掉（尤其 json/json_object 的中间态文本）；
 * - JSON 字段的非法草稿要保留在界面上并阻止提交（fieldErrors 非空时 validity 为 false），
 *   不能静默丢弃用户输入；
 * - resetKey 变化表示父级要求重置，需清空内部草稿；
 * - 必须向父级持续上报 validity，父级据此禁用运行按钮（提交前校验不能只靠前端禁用态）。
 */
import { computed, reactive, watch } from 'vue'
import WorkflowFileInput from './WorkflowFileInput.vue'
import type { StartInputVariable } from '../../api/workflows'
import { parseWorkflowJson } from '../../utils/workflowJson.js'
import '../../styles/workflow-node-contracts.css'

const props = defineProps<{ modelValue: string; variables: StartInputVariable[]; disabled: boolean; resetKey?: number }>()
const emit = defineEmits<{ 'update:modelValue': [value: string]; validity: [valid: boolean] }>()
const drafts = reactive<Record<string, string>>({})
let lastEmitted: string | undefined
/** 整体 JSON 校验：必须是对象（数组与标量拒绝），供父级禁用运行按钮使用。 */
const parseError = computed(() => {
  try {
    const value = parseWorkflowJson(props.modelValue || '{}')
    return value && typeof value === 'object' && !Array.isArray(value) ? '' : '开始节点输入必须是 JSON 对象'
  } catch (cause) { return cause instanceof RangeError ? cause.message : 'JSON 输入格式不正确' }
})
const parsed = computed<Record<string, any>>(() => {
  try {
    const value = parseWorkflowJson(props.modelValue || '{}')
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {}
  } catch { return {} }
})
const hasFields = computed(() => props.variables.length > 0)

function fieldValue(field: StartInputVariable) {
  return field.variable in parsed.value ? parsed.value[field.variable] : field.default
}
/**
 * 写入单个字段值并同步整个 JSON：值 undefined 表示删除该键（清空输入），
 * 同时用 lastEmitted 记录本次输出，避免父级回灌时被自己的 watch 当成外部变更覆盖草稿。
 */
function setValue(name: string, value: unknown) {
  if (parseError.value) return
  const next = { ...parsed.value }
  if (value === undefined) delete next[name]
  else next[name] = value
  lastEmitted = JSON.stringify(next, null, 2)
  emit('update:modelValue', lastEmitted)
}
function setNumber(field: StartInputVariable, event: Event) {
  const value = (event.target as HTMLInputElement).value
  setValue(field.variable, value === '' ? undefined : Number(value))
}
/**
 * JSON 类字段的草稿输入：解析失败时故意不写回 modelValue，草稿仍留在界面上，
 * 由 fieldErrors 阻止提交（提示用户修正括号/引号/逗号）。
 */
function editJson(field: StartInputVariable, event: Event) {
  drafts[field.variable] = (event.target as HTMLTextAreaElement).value
  try { setValue(field.variable, drafts[field.variable]?.trim() ? parseWorkflowJson(drafts[field.variable]!) : undefined) } catch { /* 非法或精度不安全的草稿保留在输入框，由字段错误阻止提交。 */ }
}
function complexDraft(field: StartInputVariable) {
  if (drafts[field.variable] !== undefined) return drafts[field.variable]
  const value = fieldValue(field)
  return value === undefined || value === null ? '' : JSON.stringify(value, null, 2)
}
/** 逐字段错误：JSON 类字段先校验草稿可解析与对象形态，再做必填校验（file-list 空数组视为未填）。 */
const fieldErrors = computed(() => Object.fromEntries(props.variables.map(field => {
  let value = fieldValue(field)
  if (['json', 'json_object'].includes(field.type)) {
    const draft = complexDraft(field)?.trim()
    if (draft) {
      try { value = parseWorkflowJson(draft) } catch (cause) { return [field.variable, cause instanceof RangeError ? cause.message : 'JSON 格式不正确，请检查括号、引号和逗号'] }
      if (field.type === 'json_object' && (!value || typeof value !== 'object' || Array.isArray(value))) return [field.variable, '请输入 JSON 对象，例如 {"name":"示例"}']
    } else value = undefined
  }
  return [field.variable, field.required && (value === undefined || value === null || value === '' || (field.type === 'file-list' && Array.isArray(value) && !value.length)) ? `请填写${field.label}` : '']
})))
watch([() => props.modelValue, () => props.variables, () => props.resetKey], ([value], previous) => {
  if (value === lastEmitted && previous && props.resetKey === previous[2] && props.variables === previous[1]) { lastEmitted = undefined; return }
  lastEmitted = undefined
  for (const key of Object.keys(drafts)) delete drafts[key]
  for (const field of props.variables) {
    if (['json', 'json_object'].includes(field.type)) drafts[field.variable] = complexDraft(field) || ''
  }
}, { immediate: true })
watch(() => !parseError.value && !Object.values(fieldErrors.value).some(Boolean), valid => emit('validity', valid), { immediate: true, flush: 'sync' })
</script>

<template>
  <div class="wf-start-runtime-form">
    <template v-if="hasFields">
      <div v-for="field in variables.filter(item => item.variable !== 'windowAfterKey')" :key="field.variable" class="wf-runtime-field">
        <span>{{ field.label }}<b v-if="field.required">*</b><small>{{ field.variable }}<template v-if="field.unit"> · {{ field.unit }}</template></small></span>
        <input v-if="['windowStart', 'windowEnd'].includes(field.variable)" type="datetime-local" :value="fieldValue(field) ?? ''" :aria-label="field.label" :disabled="disabled" @input="setValue(field.variable, ($event.target as HTMLInputElement).value || undefined)" />
        <textarea v-else-if="field.type === 'paragraph'" :value="fieldValue(field) ?? ''" :maxlength="field.maxLength || undefined" :placeholder="field.placeholder" rows="4" :aria-label="field.label" :disabled="disabled" @input="setValue(field.variable, ($event.target as HTMLTextAreaElement).value)" />
        <select v-else-if="field.type === 'select'" :value="fieldValue(field) ?? ''" :aria-label="field.label" :disabled="disabled" @change="setValue(field.variable, ($event.target as HTMLSelectElement).value || undefined)"><option value="">请选择</option><option v-for="option in field.options || []" :key="option" :value="option">{{ option }}</option></select>
        <label v-else-if="field.type === 'checkbox'" class="wf-runtime-checkbox"><input type="checkbox" :checked="!!fieldValue(field)" :aria-label="field.label" :disabled="disabled" @change="setValue(field.variable, ($event.target as HTMLInputElement).checked)" /><span>{{ field.placeholder || '是' }}</span></label>
        <input v-else-if="field.type === 'number'" type="number" :value="fieldValue(field) ?? ''" :placeholder="field.placeholder" :aria-label="field.label" :disabled="disabled" @input="setNumber(field, $event)" />
        <textarea v-else-if="field.type === 'json' || field.type === 'json_object'" :value="complexDraft(field)" rows="5" spellcheck="false" :placeholder="field.placeholder || '输入 JSON'" :aria-label="field.label" :aria-invalid="!!fieldErrors[field.variable]" :disabled="disabled" @input="editJson(field, $event)" />
        <WorkflowFileInput v-else-if="field.type === 'file' || field.type === 'file-list'" :model-value="fieldValue(field)" :multiple="field.type === 'file-list'" :disabled="disabled" @update:model-value="setValue(field.variable, $event)" />
        <input v-else :type="field.type === 'url' ? 'url' : 'text'" :value="fieldValue(field) ?? ''" :maxlength="field.maxLength || undefined" :placeholder="field.placeholder" :aria-label="field.label" :disabled="disabled" @input="setValue(field.variable, ($event.target as HTMLInputElement).value)" />
        <em v-if="field.hint">{{ field.hint }}</em>
        <p v-if="fieldErrors[field.variable]" class="wf-error" role="alert">{{ fieldErrors[field.variable] }}</p>
      </div>
      <details class="wf-runtime-json"><summary>JSON 输入</summary><textarea :value="modelValue" rows="8" spellcheck="false" :disabled="disabled" @input="$emit('update:modelValue', ($event.target as HTMLTextAreaElement).value)" /><p v-if="parseError" class="wf-error">{{ parseError }}</p></details>
    </template>
    <label v-else class="wf-runtime-field"><span>开始节点输入<small>JSON 对象</small></span><textarea :value="modelValue" rows="12" spellcheck="false" :disabled="disabled" @input="$emit('update:modelValue', ($event.target as HTMLTextAreaElement).value)" /><p v-if="parseError" class="wf-error">{{ parseError }}</p></label>
  </div>
</template>
