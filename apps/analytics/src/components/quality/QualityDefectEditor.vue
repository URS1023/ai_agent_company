<script setup lang="ts">
/**
 * 单条缺陷编辑器（外观检测结果的录入/修改行）。
 * 路由：无独立路由，由质检记录的“外观观察”表单（QualityObservationForm）按缺陷列表渲染。
 * 依赖接口：无请求；通过 v-model 维护 QualityDefect（缺陷类型/置信度/级别/处置建议/位置框）。
 * 关键交互：位置框交给 QualityDefectPosition（坐标输入或原图框选）；remove 事件由父组件执行移除。
 * 约束：disabled 时整个 fieldset 禁用（只读查看场景）；置信度用 inputmode="decimal" 以便移动端输入小数。
 */
import type { QualityDefect } from '../../api/quality'
import QualityDefectPosition from './QualityDefectPosition.vue'
const model = defineModel<QualityDefect>({ required: true })
defineProps<{ index: number; disabled: boolean }>()
defineEmits<{ remove: [] }>()
/** 单字段更新：整体替换 model（defineModel 必须重新赋值才会触发父组件更新）。 */
function patch(key: keyof QualityDefect, value: unknown) { model.value = { ...model.value, [key]: value } }
</script>
<template>
  <fieldset class="qc-observation" :disabled="disabled"><legend>缺陷 {{ index + 1 }}</legend><div class="qc-form-grid"><label>缺陷类型<input :value="model.defectType" required maxlength="160" @input="patch('defectType', ($event.target as HTMLInputElement).value)" /></label><label>置信度<input :value="model.confidence" required inputmode="decimal" @input="patch('confidence', ($event.target as HTMLInputElement).value)" /></label><label>缺陷级别<select :value="model.severity" @change="patch('severity', ($event.target as HTMLSelectElement).value)"><option value="minor">轻微</option><option value="moderate">中度</option><option value="severe">重度</option><option value="review">待复核</option></select></label><label>处置建议<input :value="model.suggest" maxlength="2000" @input="patch('suggest', ($event.target as HTMLInputElement).value)" /></label><QualityDefectPosition :model-value="model.bbox" :disabled="disabled" @update:model-value="patch('bbox', $event)" /></div><button type="button" @click="$emit('remove')">移除此缺陷</button></fieldset>
</template>
