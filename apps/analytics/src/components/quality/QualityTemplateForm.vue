<script setup lang="ts">
/**
 * 检验模板编辑表单（v-model 绑定 QualityRecord），由质检工作台的模板配置弹窗使用。
 * 依赖接口：api/quality.ts 的 quality.get('metrics') 逐个取指标名称用于回显（ids → names）。
 * 关键交互：通过 QualityResourceSelect 选择指标后 add() 追加到 metricIds（去重），列表项可移除；
 *   指标名称用 Promise.all 并发拉取，任一失败即报错但不影响已选 id 的保存语义。
 * 约束：至少添加一个指标（不会自动补默认指标）；保存生成新修订并固定所选指标的当时标准 ——
 *   已有质检记录与已发布工作流不会自动切换（提示文案即业务规则）。
 */
import { shallowRef, watch } from 'vue'
import { quality, qualityBase, type QualityRecord } from '../../api/quality'
import QualityResourceSelect from './QualityResourceSelect.vue'
const model = defineModel<QualityRecord>({ required: true })
defineProps<{ disabled: boolean }>()
const choice = shallowRef(''), names = shallowRef<Record<string, string>>({}), error = shallowRef('')
function patch(key: string, value: unknown) { model.value = { ...model.value, [key]: value } }
/** 追加指标：去重后写入 metricIds，并清空选择器（choice）避免重复添加同一项。 */
function add(row: QualityRecord | null) { if (!row?.id) return; if (!model.value.metricIds.includes(row.id)) patch('metricIds', [...model.value.metricIds, row.id]); choice.value = '' }
watch(() => model.value.metricIds as string[], async (ids, _, cleanup) => {
  const controller = new AbortController(); cleanup(() => controller.abort()); error.value = ''
  try { const loaded = await Promise.all(ids.map(id => quality.get('metrics', id, controller.signal))); if (!controller.signal.aborted) names.value = Object.fromEntries(loaded.map(item => [item.data.id!, item.data.name])) }
  catch (cause) { if (!controller.signal.aborted) error.value = (cause as Error).message }
}, { immediate: true })
</script>
<template>
  <fieldset :disabled="disabled" class="qc-form-grid"><label>模板名称<input :value="model.name" required maxlength="160" @input="patch('name', ($event.target as HTMLInputElement).value)" /></label><label>适用设备类别<input :value="model.deviceType" maxlength="100" placeholder="留空表示不限；填写须与设备类别一致" @input="patch('deviceType', ($event.target as HTMLInputElement).value)" /></label><div class="qc-span"><QualityResourceSelect v-model="choice" :endpoint="qualityBase + '/metrics'" label="添加检验指标" :disabled="disabled" @select="add" /><ul class="qc-selected"><li v-for="(id, index) in model.metricIds" :key="id"><span>{{ index + 1 }}. {{ names[id] || id }}</span><button type="button" @click="patch('metricIds', model.metricIds.filter((item: string) => item !== id))">移除指标</button></li></ul><p v-if="!model.metricIds.length" class="wf-note">请明确添加至少一个指标，不会自动选择默认指标。</p><p v-if="error" class="wf-error" role="alert">{{ error }}</p></div><label>状态<select :value="model.status" @change="patch('status', ($event.target as HTMLSelectElement).value)"><option value="active">启用</option><option value="inactive">停用</option></select></label><label class="qc-span">模板说明<textarea :value="model.description" rows="3" maxlength="3000" @input="patch('description', ($event.target as HTMLTextAreaElement).value)" /></label><p class="wf-note qc-span">保存会生成新修订，固定所选指标当前标准；已有质检与已发布工作流不会自动切换。</p></fieldset>
</template>
