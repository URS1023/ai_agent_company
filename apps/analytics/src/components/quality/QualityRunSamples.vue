<script setup lang="ts">
/**
 * 运行样本编辑器（按"设备标准组"批量录入样本编号与原图）。
 * 路由：无独立路由，由 QualityRunPanel 按 plan.groups 渲染，v-model 绑定该组的样本草稿数组。
 * 依赖接口：无请求；图片上传由 QualityImageInput 完成（uploading 事件带样本 key 冒泡到运行面板，用于阻止提交）。
 * 关键交互：每组至少保留一个样本（remove 在只剩一条时禁用）；canAdd 由父组件按 100 条上限控制；
 *   外观组必须先上传原图才能填写？不 —— 允许先填编号，但提交前由父组件统一校验 imageFileId；
 *   预览原图用 preview 记录当前展开的样本 key（同时只展开一个）。
 * 约束：样本编号在提交时会被 trim 并检查组内唯一（由 useQualityRun.submit 负责）。
 */
import { shallowRef } from 'vue'
import type { QualityRunGroupPlan } from '../../api/qualityRuns'
import { newSample, type SampleDraft } from '../../composables/useQualityRun'
import QualityImageInput from './QualityImageInput.vue'
import QualityImageViewer from './QualityImageViewer.vue'
const props = defineProps<{ group: QualityRunGroupPlan; disabled: boolean; canAdd: boolean }>()
const model = defineModel<SampleDraft[]>({ required: true })
const emit = defineEmits<{ uploading: [key: string, value: boolean] }>()
const preview = shallowRef('')
/** 按 key（而非下标）定位并更新样本，避免删除/新增后下标错位写错行。 */
function patch(key: string, field: 'sampleId' | 'imageFileId', value: string | null) { model.value = model.value.map(row => row.key === key ? { ...row, [field]: value } : row) }
/** 移除样本并显式通知父组件该样本不再上传中（否则 uploading 标记会永久阻塞提交）。 */
function remove(key: string) { model.value = model.value.filter(row => row.key !== key); emit('uploading', key, false) }
</script>
<template>
  <fieldset class="qc-run-group" :data-group-id="group.nodeId"><legend>{{ group.device.name }} · {{ group.title }}</legend>
    <p class="qc-standard">{{ group.template.name }} · 固定修订 v{{ group.templateRevision }}<span>{{ group.imageRequired ? '包含外观检测，需逐样本原图' : '成分检测，无需上传图片' }}</span></p>
    <details><summary>查看本组完整检验标准</summary><ul><li v-for="metric in group.template.metrics" :key="metric.id">{{ metric.name }} · {{ metric.category === 'appearance' ? '外观缺陷' : metric.componentName }} {{ metric.standardMin ?? '' }}{{ metric.standardMax != null ? ' 至 ' + metric.standardMax : '' }} {{ metric.unit }}</li></ul></details>
    <div v-for="(row, index) in model" :key="row.key" class="qc-run-sample" :data-sample-index="index"><div class="qc-sample-heading"><label>样本编号 {{ index + 1 }}<input :value="row.sampleId" required maxlength="160" :disabled="disabled" @input="patch(row.key, 'sampleId', ($event.target as HTMLInputElement).value)" /></label><button type="button" :disabled="disabled || model.length === 1" :aria-label="'移除样本 ' + (index + 1)" @click="remove(row.key)">移除样本</button></div>
      <QualityImageInput v-if="group.imageRequired" :model-value="row.imageFileId" :disabled="disabled" @update:model-value="patch(row.key, 'imageFileId', $event)" @uploading="emit('uploading', row.key, $event)" />
      <template v-if="row.imageFileId"><button type="button" @click="preview = preview === row.key ? '' : row.key">{{ preview === row.key ? '收起原图' : '预览原图' }}</button><QualityImageViewer v-if="preview === row.key" :file-id="row.imageFileId" /></template>
    </div><button type="button" :disabled="disabled || !canAdd" @click="model = [...model, newSample()]">添加样本</button>
  </fieldset>
</template>
<style scoped>
.qc-run-sample .qc-image-viewer{max-width:640px;margin-top:12px}
.qc-run-group{min-width:0;border:1px solid var(--qc-line);padding:18px;margin:18px 0;background:var(--qc-paper);border-radius:6px}.qc-run-group legend{font-weight:650;padding:0 8px}.qc-standard{font-weight:600}.qc-standard span{display:block;font-weight:400;color:var(--qc-muted);margin-top:8px}.qc-run-sample{border-top:1px solid var(--qc-line);padding:16px 0}.qc-sample-heading{display:flex;gap:14px;align-items:end}.qc-sample-heading label{flex:1;min-width:0}.qc-sample-heading button{margin-bottom:12px}.qc-run-group li{line-height:1.9}@media(max-width:650px){.qc-run-group{padding:12px}.qc-sample-heading{align-items:start;flex-direction:column;gap:0}.qc-sample-heading label{width:100%}}
</style>
