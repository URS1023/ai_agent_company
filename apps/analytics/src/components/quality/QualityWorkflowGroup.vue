<script setup lang="ts">
/**
 * 质检流程向导中的单个"设备标准组"编辑卡（设备 + 固定模板修订 + 每个指标的检测器绑定）。
 * 路由：无独立路由，由 QualityWorkflowWizard 按分组列表渲染（v-model 交回整组草稿）。
 * 依赖接口：api/quality.ts 的 quality.revisions（模板修订快照）、api/qualityWorkflows.ts 的 linkedQualitySourceIds；
 *   检测服务候选由父组件一次性加载后传入（HttpSource[]）。
 * 关键交互：切换设备时会把"不属于新设备授权范围"的检测器绑定清空（避免跨设备绑定在运行期被拒）；
 *   切换模板修订时按 metricId+metricVersion 复用已有绑定，其余重置（指标版本变化必须重新选服务）；
 *   当模板的设备类别与所选设备类别不一致时给出 incompatibleDevice 提示（服务端仍会拦截）。
 * 加载/取消：修订列表请求持有单个 AbortController，翻页或换模板时 abort 上一次；卸载时中止。
 * 错误呈现：error 就近展示；该组件不弹全局消息。
 */
import { computed, onBeforeUnmount, shallowRef } from 'vue'
import type { HttpSource } from '../../api/collection'
import { quality, qualityBase, type QualityRecord } from '../../api/quality'
import { linkedQualitySourceIds, type DetectorBindingDraft, type QualityWorkflowGroupDraft } from '../../api/qualityWorkflows'
import QualityDetectorBinding from './QualityDetectorBinding.vue'
import QualityResourceSelect from './QualityResourceSelect.vue'

const props = defineProps<{ modelValue: QualityWorkflowGroupDraft; index: number; sources: HttpSource[]; disabled: boolean; removable: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: QualityWorkflowGroupDraft]; remove: [] }>()
const selectedDevice = shallowRef<QualityRecord | null>(null)
const revisions = shallowRef<QualityRecord[]>([]), selectedRevision = shallowRef<QualityRecord | null>(null)
const revisionOffset = shallowRef(0), loading = shallowRef(false), error = shallowRef('')
let request: AbortController | null = null
const sourceIds = computed(() => linkedQualitySourceIds(selectedDevice.value))
/** 可选检测服务：只保留与当前设备关联（主数据源 + 质检专用源）的 HTTP 源，禁止跨设备选择。 */
const availableSources = computed(() => props.sources.filter(source => sourceIds.value.has(source.id)))
const metrics = computed<QualityRecord[]>(() => selectedRevision.value?.snapshot?.metrics || [])
/** 模板与设备的类别不一致提示：模板限定了设备类别且与所选设备不同时为 true（前端先提示，服务端仍会校验）。 */
const incompatibleDevice = computed(() => !!selectedRevision.value?.snapshot?.deviceType && !!selectedDevice.value
  && selectedRevision.value.snapshot.deviceType !== selectedDevice.value.category)
/** 整组更新：以新对象回传 v-model（组件内不直接修改 props.modelValue）。 */
function patch(values: Partial<QualityWorkflowGroupDraft>) { emit('update:modelValue', { ...props.modelValue, ...values }) }
/** 切换设备：清空不属于新设备授权范围的检测器绑定（保留 datasourceId 仍合法的项，避免运行期被拒）。 */
function chooseDevice(row: QualityRecord | null) {
  selectedDevice.value = row
  const allowed = linkedQualitySourceIds(row)
  patch({ deviceId: row?.id || '', detectors: props.modelValue.detectors.map(item => allowed.has(item.datasourceId) ? item : { ...item, datasourceId: '', datasourceVersion: 1 }) })
}
/** 应用模板修订：按"指标 id + 版本"复用已有绑定（服务仍属于本设备时），其余指标重置为未选择服务。 */
function applyRevision(revision: QualityRecord) {
  selectedRevision.value = revision
  const previous = new Map(props.modelValue.detectors.map(item => [item.metricId + ':' + item.metricVersion, item]))
  const detectors = (revision.snapshot?.metrics || []).map((metric: QualityRecord) => {
    const existing = previous.get(metric.id + ':' + metric.version)
    return existing && sourceIds.value.has(existing.datasourceId) ? existing : {
      metricId: metric.id!, metricVersion: metric.version!, datasourceId: '', datasourceVersion: 1,
    }
  })
  patch({ templateRevision: revision.revision, detectors })
}
/** 加载模板修订列表：preferred 用于切换模板后尽量停留在原修订号，找不到则退回最新修订。 */
async function loadRevisions(id: string, preferred?: number) {
  request?.abort(); const active = new AbortController(); request = active
  loading.value = true; error.value = ''
  try {
    const response = await quality.revisions(id, revisionOffset.value, active.signal)
    if (active.signal.aborted) return
    revisions.value = response.data
    const revision = response.data.find(item => item.revision === preferred) || response.data[0]
    if (revision) applyRevision(revision)
    else { selectedRevision.value = null; patch({ templateRevision: 0, detectors: [] }) }
  } catch (cause) { if (!active.signal.aborted) error.value = (cause as Error).message }
  finally { if (active === request) loading.value = false }
}
function chooseTemplate(row: QualityRecord | null) {
  revisions.value = []; selectedRevision.value = null; revisionOffset.value = 0
  patch({ templateId: row?.id || '', templateRevision: 0, detectors: [] })
  if (row?.id) void loadRevisions(row.id, row.version)
}
function chooseRevision(event: Event) {
  const value = Number((event.target as HTMLSelectElement).value)
  const revision = revisions.value.find(item => item.revision === value)
  if (revision) applyRevision(revision)
}
function pageRevisions(offset: number) { revisionOffset.value = offset; void loadRevisions(props.modelValue.templateId) }
function detector(metric: QualityRecord): DetectorBindingDraft | undefined { return props.modelValue.detectors.find(item => item.metricId === metric.id && item.metricVersion === metric.version) }
function updateDetector(metric: QualityRecord, binding: DetectorBindingDraft) {
  patch({ detectors: props.modelValue.detectors.map(item => item.metricId === metric.id && item.metricVersion === metric.version ? binding : item) })
}
onBeforeUnmount(() => request?.abort())
</script>

<template>
  <section class="qc-workflow-group">
    <header class="qc-group-heading"><div><span class="qc-sequence">设备标准组 {{ String(index + 1).padStart(2, '0') }}</span><h3>{{ modelValue.title || selectedDevice?.name || '待配置检测组' }}</h3><p>一组样本使用同一设备与固定检验标准；每个指标可绑定不同服务。</p></div><button v-if="removable" type="button" :disabled="disabled" @click="$emit('remove')">移除此组</button></header>
    <div class="qc-wiring-row" aria-label="质检证据链"><span>设备</span><i>→</i><span>固定标准</span><i>→</i><span>指标检测服务</span><i>→</i><span>独立报告</span></div>
    <div class="qc-group-grid"><label>组名称（可选）<input :value="modelValue.title" maxlength="160" :disabled="disabled" placeholder="例如：一号线成品终检" @input="patch({ title: ($event.target as HTMLInputElement).value })" /></label><div></div>
      <QualityResourceSelect :model-value="modelValue.deviceId" endpoint="/api/v1/alerts/devices" label="受检设备" :statuses="['active']" :disabled="disabled" @select="chooseDevice" />
      <QualityResourceSelect :model-value="modelValue.templateId" :endpoint="qualityBase + '/templates'" label="检验模板" :statuses="['active']" :disabled="disabled" @select="chooseTemplate" />
      <div class="qc-revision-field"><label>固定模板修订<select :value="modelValue.templateRevision || ''" :disabled="disabled || loading || !revisions.length" required @change="chooseRevision"><option value="">请选择不可变标准</option><option v-for="item in revisions" :key="item.revision" :value="item.revision">v{{ item.revision }} · {{ item.createTime }}</option></select></label><div class="wf-actions"><button type="button" :disabled="disabled || loading || !revisionOffset" @click="pageRevisions(revisionOffset - 30)">较新修订</button><button type="button" :disabled="disabled || loading || revisions.length < 30" @click="pageRevisions(revisionOffset + 30)">更早修订</button></div></div>
      <aside class="qc-device-services"><strong>设备已授权检测服务</strong><span>{{ availableSources.length }} 个 HTTP 服务可选</span><p v-if="selectedDevice && !availableSources.length">请先在“共享设备台账”关联外观或成分服务。</p><p v-else-if="selectedDevice">主采集源与质检服务均可使用；流程会固定实际选中的版本。</p><p v-else>选择设备后显示其已授权服务。</p></aside>
    </div>
    <p v-if="incompatibleDevice" class="wf-error" role="alert">此模板限定设备类型“{{ selectedRevision?.snapshot?.deviceType }}”，与当前设备类型“{{ selectedDevice?.category || '未填写' }}”不一致。</p>
    <p v-if="error" class="wf-error" role="alert">{{ error }}</p><p v-if="loading" role="status">正在读取不可变模板修订…</p>
    <div v-if="metrics.length" class="qc-detector-grid"><QualityDetectorBinding v-for="metric in metrics" :key="metric.id + ':' + metric.version" :metric="metric" :model-value="detector(metric)" :sources="availableSources" :disabled="disabled || loading || incompatibleDevice" @update:model-value="updateDetector(metric, $event)" /></div>
    <p v-else-if="modelValue.templateId && !loading" class="wf-note">当前页面未读取到可用模板修订，请检查模板及指标权限。</p>
  </section>
</template>
