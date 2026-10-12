<script setup lang="ts">
/**
 * 质检记录录入表单（人工实测入口）。
 * 路由：无独立路由，由质检工作台的“新增质检记录”弹窗/区域使用，提交事件由父组件调用 quality.createInspection。
 * 依赖接口：api/quality.ts 的 quality.revisions（按模板取修订快照）、QualityResourceSelect 拉取模板与设备列表。
 * 关键交互：必须显式选择"模板修订 + 受检设备 + 样本标识"才允许提交（修订号固定，避免标准漂移）；
 *   仅提交 recorded=true 的指标（未勾选保留为"未完成检测"）；有图片上传在进行中时禁止保存。
 * 约束：requestKey 由 createClientId 在表单创建时生成，用于提交幂等；表单状态全程标记为"人工录入"来源。
 * 错误呈现：error 就近展示；上传中/缺字段/无修订的选择都会给出中文原因。
 */
import { computed, ref, shallowRef, watch } from 'vue'
import { quality, qualityBase, type QualityRecord } from '../../api/quality'
import QualityResourceSelect from './QualityResourceSelect.vue'
import QualityObservationForm from './QualityObservationForm.vue'
import { createClientId } from '../../utils/randomId'
const props = defineProps<{ disabled: boolean }>()
const emit = defineEmits<{ submit: [payload: object] }>()
const templateId = shallowRef(''), deviceId = shallowRef(''), sampleId = shallowRef(''), revision = shallowRef<number | ''>('')
const versions = shallowRef<QualityRecord[]>([]), revisionOffset = shallowRef(0), loading = shallowRef(false), error = shallowRef('')
const uploading = shallowRef<Record<string, boolean>>({})
function uploadState(id: string, value: boolean) { uploading.value = { ...uploading.value, [id]: value } }
// requestKey 在组件创建时固定：同一次填写重复提交会被后端幂等识别，不会产生多条质检记录。
const observations = ref<Record<string, QualityRecord>>({}), requestKey = createClientId()
/** 所选修订的完整快照（含 metrics），用于渲染指标录入卡与标准值。 */
const chosen = computed(() => versions.value.find(item => item.revision === revision.value))
const metrics = computed<QualityRecord[]>(() => chosen.value?.snapshot.metrics || [])
watch([templateId, revisionOffset], async ([id], _, cleanup) => {
  const controller = new AbortController(); cleanup(() => controller.abort()); loading.value = true; error.value = ''
  try { const response = id ? await quality.revisions(id, revisionOffset.value, controller.signal) : { data: [] }; if (!controller.signal.aborted) versions.value = response.data }
  catch (cause) { if (!controller.signal.aborted) error.value = (cause as Error).message }
  finally { if (!controller.signal.aborted) loading.value = false }
}, { immediate: true })
watch(templateId, () => { revision.value = ''; revisionOffset.value = 0 })
// 指标集合变化（切换修订）时重建观察草稿：recorded 默认 false，即"未完成检测"，避免默认判为合格。
watch(metrics, rows => { observations.value = Object.fromEntries(rows.map(row => [row.id!, { metricId: row.id, recorded: false, value: null, unit: row.unit, imageRef: '', imageFileId: null, defects: null, note: '' }])) })
/** 提交前校验与组装：拦截上传未完成/缺修订/缺设备/缺样本标识；只提交已勾选（recorded）的指标并剔除 recorded 标记。 */
function submit() {
  if (Object.values(uploading.value).some(Boolean)) { error.value = '请等待图片上传和校验完成后保存'; return }
  if (props.disabled || !chosen.value || !deviceId.value || !sampleId.value.trim()) { error.value = '请选择模板修订、设备并填写样本标识'; return }
  const values = Object.values(observations.value).filter(row => row.recorded).map(({ recorded, ...row }) => row)
  emit('submit', { templateId: templateId.value, templateRevision: revision.value, deviceId: deviceId.value, sampleId: sampleId.value, requestKey, observations: values })
}
</script>
<template>
  <form id="qc-inspection-form" @submit.prevent="submit"><fieldset :disabled="disabled" class="qc-form-grid"><div class="qc-span"><QualityResourceSelect v-model="templateId" :endpoint="qualityBase + '/templates'" label="质检模板" :disabled="disabled" /></div><label>固定模板修订<select v-model="revision" required :disabled="loading"><option value="">请明确选择标准修订</option><option v-for="item in versions" :key="item.revision" :value="item.revision">v{{ item.revision }} · {{ item.createTime }}</option></select></label><div class="wf-actions"><button type="button" :disabled="loading || !revisionOffset" @click="revisionOffset -= 30; revision = ''">较新修订</button><button type="button" :disabled="loading || versions.length < 30" @click="revisionOffset += 30; revision = ''">更早修订</button></div><div class="qc-span"><QualityResourceSelect v-model="deviceId" endpoint="/api/v1/alerts/devices" label="受检设备" :disabled="disabled" /></div><label class="qc-span">样本 / 批次标识<input v-model="sampleId" required maxlength="160" placeholder="例如：2026-08-31-批次01" /></label></fieldset><p class="wf-note">此表单记录人工实测，来源明确标为“人工录入”。自动检测请使用共享工作流中的外观、成分和质检判定节点。未勾选的指标会保留为“未完成检测”。</p><QualityObservationForm v-for="metric in metrics" :key="metric.id" v-model="observations[metric.id!]" :metric="metric" :disabled="disabled" @uploading="uploadState(metric.id!, $event)" /><p v-if="error" class="wf-error" role="alert">{{ error }}</p></form>
</template>
