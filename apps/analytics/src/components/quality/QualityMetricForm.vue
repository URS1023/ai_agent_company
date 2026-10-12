<script setup lang="ts">
/**
 * 质检指标编辑表单（v-model 绑定 QualityRecord），由质检工作台的指标配置弹窗使用。
 * 依赖接口：无请求；类别选项取自 api/quality.ts 的 metricCategories（数值测量/成分检测/外观缺陷）。
 * 关键交互：切换类别时清除不适用字段（外观类清空单位与标准上下限，避免残留值被提交）；
 *   数值/成分类要求至少配置一个界限（提示文案）；外观类需设置最低置信度与"不合格缺陷级别"。
 * 约束：标准值以精确文本保存（不做四舍五入、不自动换算单位），前端只限制长度与必填。
 */
import { metricCategories, type QualityRecord } from '../../api/quality'
const model = defineModel<QualityRecord>({ required: true })
defineProps<{ disabled: boolean }>()
function patch(key: string, value: unknown) { model.value = { ...model.value, [key]: value } }
/** 切换检测类别：外观类不适用标准区间与单位，必须清掉（否则会带着旧字段提交）。 */
function category(event: Event) { const value = (event.target as HTMLSelectElement).value; model.value = { ...model.value, category: value, ...(value === 'appearance' ? { standardMin: null, standardMax: null, unit: '' } : {}) } }
/** 勾选/取消"不合格缺陷级别"（阻断级别），键与 api/quality.ts 的严重度枚举保持一致。 */
function severity(value: string, checked: boolean) { patch('blockingSeverities', checked ? [...model.value.blockingSeverities, value] : model.value.blockingSeverities.filter((item: string) => item !== value)) }
</script>
<template>
  <fieldset :disabled="disabled" class="qc-form-grid"><label>指标名称<input :value="model.name" required maxlength="160" @input="patch('name', ($event.target as HTMLInputElement).value)" /></label><label>检测类别<select :value="model.category" @change="category"><option v-for="(label, key) in metricCategories" :key="key" :value="key">{{ label }}</option></select></label><label v-if="model.category === 'component'">成分名称<input :value="model.componentName" required maxlength="120" placeholder="例如 C，必须与设备结果一致" @input="patch('componentName', ($event.target as HTMLInputElement).value)" /></label><template v-if="model.category !== 'appearance'"><label>单位<input :value="model.unit" maxlength="40" @input="patch('unit', ($event.target as HTMLInputElement).value)" /></label><label>标准下限（含端点）<input :value="model.standardMin" inputmode="decimal" placeholder="不限下限" @input="patch('standardMin', ($event.target as HTMLInputElement).value || null)" /></label><label>标准上限（含端点）<input :value="model.standardMax" inputmode="decimal" placeholder="不限上限" @input="patch('standardMax', ($event.target as HTMLInputElement).value || null)" /></label><p class="wf-note qc-span">至少配置一个界限。数值以精确文本保存，不自动四舍五入或换算单位。</p></template><template v-else><label>自动判定最低置信度<input :value="model.minimumConfidence" required inputmode="decimal" @input="patch('minimumConfidence', ($event.target as HTMLInputElement).value)" /></label><fieldset><legend>不合格缺陷级别</legend><label v-for="(label, key) in { minor: '轻微', moderate: '中度', severe: '重度' }" :key="key" class="qc-check"><input type="checkbox" :checked="model.blockingSeverities.includes(key)" @change="severity(key, ($event.target as HTMLInputElement).checked)" />{{ label }}</label></fieldset></template><label>配置状态<select :value="model.status" @change="patch('status', ($event.target as HTMLSelectElement).value)"><option value="active">启用</option><option value="inactive">停用</option></select></label><label class="qc-span">标准说明<textarea :value="model.description" maxlength="3000" rows="3" @input="patch('description', ($event.target as HTMLTextAreaElement).value)" /></label></fieldset>
</template>
