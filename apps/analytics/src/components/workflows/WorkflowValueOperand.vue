<script setup lang="ts">
/**
 * WorkflowValueOperand —— 表达式中的"值来源"选择器（字段 / 固定数值 / 固定文本 / 布尔）。
 *
 * 位置：ExpressionEditor 与 WorkflowCalculationForm 的每个操作数位置。
 * 数据契约：v-model 为 { source: 'field', field } 或 { source: 'constant', value }，
 * 必须与后端规则表达式里的操作数结构一致。
 * 关键约束：
 * - 类型由 modelValue 的值类型推断（number/boolean/text），切换类型时按新类型给默认值，
 *   不保留旧值（避免文本 "0" 与数值 0 混淆）；
 * - fields 为空时仍允许选择 field 来源（field 为空串），由上层校验兜底。
 */
import { computed, ref, watch } from 'vue'
import { parseWorkflowJson } from '../../utils/workflowJson.js'
const props = defineProps<{ modelValue: Record<string, any>; fields: string[]; label: string; disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: Record<string, any>] }>()
const type = computed(() => props.modelValue.source === 'field' ? 'field' : typeof props.modelValue.value === 'number' ? 'number' : typeof props.modelValue.value === 'boolean' ? 'boolean' : 'text')
const draft = ref(''), inputError = ref('')
// 保存回执会重建同值对象；只有配置语义改变才能覆盖用户尚未应用的输入。
watch([() => props.modelValue.source, () => props.modelValue.value], ([, value]) => {
  draft.value = String(value ?? '')
  inputError.value = ''
}, { immediate: true })
/** 未通过精度校验的数字只留在输入草稿，不能覆盖上次有效配置或被自动保存。 */
function editValue(event: Event) {
  draft.value = (event.target as HTMLInputElement).value
  try {
    const value = type.value === 'number' ? parseWorkflowJson(draft.value) : draft.value
    if (type.value === 'number' && typeof value !== 'number') throw new Error('请输入有效数值')
    inputError.value = ''
    emit('update:modelValue', { source: 'constant', value })
  } catch (cause) {
    inputError.value = cause instanceof RangeError
      ? '数值精度超出浏览器安全范围，未应用该输入。精确小数或编号请改选固定文本。'
      : '请输入有效数值，未应用该输入。'
  }
}
/** 切换值来源/类型：field 取首个可用字段，常量按类型给默认值（不沿用旧值，避免类型混淆）。 */
function changeType(value: string) {
  emit('update:modelValue', value === 'field' ? { source: 'field', field: props.fields[0] || '' } : { source: 'constant', value: value === 'number' ? 0 : value === 'boolean' ? true : '' })
}
</script>
<template>
  <fieldset class="value-operand" :disabled="disabled">
    <legend>{{ label }}</legend>
    <select :aria-label="label + '来源'" :value="type" @change="changeType(($event.target as HTMLSelectElement).value)">
      <option value="field">读取或计算的字段</option><option value="number">固定数值</option><option value="text">固定文本或日期</option><option value="boolean">是 / 否</option>
    </select>
    <select v-if="type === 'field'" :aria-label="label" :value="modelValue.field || ''" @change="emit('update:modelValue', { source: 'field', field: ($event.target as HTMLSelectElement).value })">
      <option value="">请选择字段</option><option v-for="field in fields" :key="field" :value="field">{{ field }}</option>
      <option v-if="modelValue.field && !fields.includes(modelValue.field)" :value="modelValue.field">{{ modelValue.field }}（请核对上游）</option>
    </select>
    <select v-else-if="type === 'boolean'" :aria-label="label" :value="String(modelValue.value)" @change="emit('update:modelValue', { source: 'constant', value: ($event.target as HTMLSelectElement).value === 'true' })"><option value="true">是</option><option value="false">否</option></select>
    <input v-else :aria-label="label" type="text" :inputmode="type === 'number' ? 'decimal' : undefined" :aria-invalid="Boolean(inputError)" :value="draft" @input="editValue" />
    <p v-if="inputError" class="operand-error" role="alert">{{ inputError }}</p>
  </fieldset>
</template>
<style scoped>
.value-operand { display: grid; gap: 8px; border: 0; padding: 0; margin: 10px 0; min-width: 0; }
.value-operand legend { font-size: 13px; color: #475467; padding-bottom: 6px; }
.operand-error { margin: 0; color: #b42318; font-size: 12px; }
</style>
