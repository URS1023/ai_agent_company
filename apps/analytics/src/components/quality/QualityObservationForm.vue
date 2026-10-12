<script setup lang="ts">
/**
 * 单个指标的人工观察录入卡（外观 / 成分 / 数值三类共用）。
 * 路由：无独立路由，由 QualityInspectionForm 按模板修订快照里的指标列表渲染。
 * 依赖接口：无请求（图片上传交给 QualityImageInput，读取交给 QualityImageViewer）。
 * 关键交互：recorded 未勾选表示"未完成检测"（提交时会被过滤掉，不会误判为合格）；
 *   外观类必须先明确选择"已检查未发现缺陷 / 记录具体缺陷 / 尚未完成"，用 null / [] / [defect] 三态表达；
 *   上传中通过 uploading 事件冒泡到表单，阻止在图片核验未完成时提交。
 * 约束：不上传图片时必须填写图像资料标识（仅文字记录，禁止自动访问外部地址）；缺陷最多 300 条。
 */
import type { DefectBox, QualityDefect, QualityRecord } from '../../api/quality'
import QualityImageInput from './QualityImageInput.vue'
import QualityImageViewer from './QualityImageViewer.vue'
import QualityDefectEditor from './QualityDefectEditor.vue'
const model = defineModel<QualityRecord>({ required: true })
defineProps<{ metric: QualityRecord; disabled: boolean }>()
defineEmits<{ uploading: [value: boolean] }>()
function patch(key: string, value: unknown) { model.value = { ...model.value, [key]: value } }
/** 更新第 index 个缺陷：整体替换 defects 数组以触发 v-model 变更。 */
function setDefect(index: number, value: QualityDefect) { patch('defects', model.value.defects.map((row: QualityDefect, i: number) => i === index ? value : row)) }
/** 原图框选回调：把归一化坐标写到对应缺陷的 bbox（defects[index] 必须存在，故先做一次展开合并）。 */
function setBox(index: number, value: DefectBox) { setDefect(index, { ...model.value.defects[index], bbox: value }) }
/** 新建缺陷的默认值：置信度 1、级别"待复核"，避免默认判为轻微。 */
function newDefect(): QualityDefect { return { defectType: '', confidence: '1', severity: 'review', suggest: '' } }
/** 外观检查结果三态切换：pending=undefined（未完成，提交时排除）、none=空数组、defects=一条新缺陷。 */
function changeResult(event: Event) { const value = (event.target as HTMLSelectElement).value; patch('defects', value === 'pending' ? null : value === 'none' ? [] : [newDefect()]) }
</script>
<template>
  <fieldset :disabled="disabled" class="qc-observation">
    <legend>{{ metric.name }} · v{{ metric.version }}</legend>
    <p class="wf-note">{{ metric.category === 'appearance' ? '外观缺陷：请保留图像来源，并明确记录是否已检查。' : '标准 [' + (metric.standardMin ?? '不限') + ', ' + (metric.standardMax ?? '不限') + '] ' + metric.unit }}</p>
    <label class="qc-check"><input type="checkbox" :checked="model.recorded" @change="patch('recorded', ($event.target as HTMLInputElement).checked)" />已人工测量/检查此指标</label>
    <div v-if="model.recorded" class="qc-form-grid">
      <template v-if="metric.category !== 'appearance'">
        <label>实测值<input :value="model.value" inputmode="decimal" @input="patch('value', ($event.target as HTMLInputElement).value || null)" /></label>
        <label>实际单位<input :value="model.unit" maxlength="40" @input="patch('unit', ($event.target as HTMLInputElement).value)" /></label>
      </template>
      <template v-else>
        <div class="qc-span"><QualityImageInput :model-value="model.imageFileId || null" :disabled="disabled" @update:model-value="patch('imageFileId', $event)" @uploading="$emit('uploading', $event)" /></div>
        <label class="qc-span">图像资料标识<input :value="model.imageRef" :required="!model.imageFileId" maxlength="1000" placeholder="未上传时填写来源；仅记录文字，不自动访问外部地址" @input="patch('imageRef', ($event.target as HTMLInputElement).value)" /></label>
        <label class="qc-span">外观检查结果<select :value="model.defects === null ? 'pending' : model.defects.length ? 'defects' : 'none'" @change="changeResult"><option value="pending">尚未完成外观检查</option><option value="none">已检查，未发现缺陷</option><option value="defects">已检查，记录具体缺陷</option></select></label>
        <div v-if="model.imageFileId" class="qc-span"><QualityImageViewer :file-id="model.imageFileId" :defects="model.defects || []" editable :disabled="disabled" @box="setBox" /></div>
        <div v-for="(item, index) in model.defects || []" :key="index" class="qc-span"><QualityDefectEditor :model-value="item" :index="Number(index)" :disabled="disabled" @update:model-value="setDefect(Number(index), $event)" @remove="patch('defects', model.defects.filter((_: unknown, i: number) => i !== Number(index)))" /></div>
        <button v-if="model.defects?.length" type="button" class="qc-span" :disabled="model.defects.length >= 300" @click="patch('defects', [...model.defects, newDefect()])">添加缺陷</button>
      </template>
      <label class="qc-span">采样/人工观察备注<textarea :value="model.note" maxlength="2000" rows="2" @input="patch('note', ($event.target as HTMLTextAreaElement).value)" /></label>
    </div>
  </fieldset>
</template>
