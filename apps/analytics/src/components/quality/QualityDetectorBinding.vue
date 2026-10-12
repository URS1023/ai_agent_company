<script setup lang="ts">
/**
 * 检测器绑定卡片：为某个质检指标选择可用的 HTTP 检测服务，并显示服务契约是否就绪。
 * 路由：无独立路由，由质检流程向导（QualityWorkflowWizard → QualityWorkflowGroup）按分组内的指标渲染。
 * 依赖接口：api/qualityWorkflows.ts 的 detectorSourceState 做前端预检（服务端仍是最终裁判）；服务列表由父组件传入。
 * 关键交互：choose 时同时写入 metricId/metricVersion 与 datasourceId/datasourceVersion（版本必须成对固定，
 *   否则运行期会取到被改动过的服务配置）；不可用的服务在选项中禁用并附带中文原因。
 * 约束：numeric（人工数值）指标不允许绑定自动检测服务 —— 必须走人工实测录入，卡片内直接给出说明。
 */
import { computed } from 'vue'
import type { HttpSource } from '../../api/collection'
import type { QualityRecord } from '../../api/quality'
import { detectorSourceState, type DetectorBindingDraft } from '../../api/qualityWorkflows'

const props = defineProps<{
  metric: QualityRecord
  modelValue?: DetectorBindingDraft
  sources: HttpSource[]
  disabled: boolean
}>()
const emit = defineEmits<{ 'update:modelValue': [value: DetectorBindingDraft] }>()
const categoryLabel = computed(() => props.metric.category === 'appearance' ? '外观缺陷' : props.metric.category === 'component' ? '成分检测' : '人工数值')
/** 已选服务的完整记录（用于展示契约就绪情况）；未选或已被删除时为 undefined。 */
const selected = computed(() => props.sources.find(source => source.id === props.modelValue?.datasourceId))
/** 前端预检结果（就绪/不可用 + 中文原因），直接用于选项禁用与说明文案。 */
function state(source: HttpSource) { return detectorSourceState(source, props.metric.category) }
/** 选中服务：仅当预检通过时写回（含 datasourceVersion），并同步指标的 id/version 以固定绑定版本。 */
function choose(id: string) {
  const source = props.sources.find(item => item.id === id)
  if (!source || !state(source).ready) return
  emit('update:modelValue', {
    metricId: props.metric.id!, metricVersion: props.metric.version!,
    datasourceId: source.id, datasourceVersion: source.version,
  })
}
</script>

<template>
  <article class="qc-detector-binding" :class="{ 'is-unavailable': metric.category === 'numeric' }">
    <header><div><span class="qc-sequence">{{ categoryLabel }}</span><h4>{{ metric.name }}</h4></div><strong>指标 v{{ metric.version }}</strong></header>
    <p v-if="metric.category === 'component'" class="qc-standard">{{ metric.componentName }} · 标准 [{{ metric.standardMin ?? '不限' }}, {{ metric.standardMax ?? '不限' }}] {{ metric.unit }}</p>
    <p v-else-if="metric.category === 'appearance'" class="qc-standard">最低置信度 {{ metric.minimumConfidence }} · 阻断级别 {{ (metric.blockingSeverities || []).join('、') || '未设置' }}</p>
    <p v-else class="wf-error">此指标需要人工实测，不能伪装成自动设备结果。请在“质检记录”录入，或选择只含外观/成分指标的模板。</p>
    <template v-if="metric.category !== 'numeric'">
      <label>指标检测服务<select :value="modelValue?.datasourceId || ''" :disabled="disabled" required @change="choose(($event.target as HTMLSelectElement).value)">
        <option value="">请选择设备已授权服务</option>
        <option v-for="source in sources" :key="source.id" :value="source.id" :disabled="!state(source).ready">{{ source.name }} · v{{ source.version }}{{ state(source).ready ? '' : ' · ' + state(source).reason }}</option>
      </select></label>
      <div v-if="selected" class="qc-service-contract" :class="{ ready: state(selected).ready }"><span>{{ state(selected).ready ? '配置就绪' : '配置需修正' }}</span><p>{{ state(selected).reason }} · POST / JSONPath：{{ selected.recordPath }}</p><small>{{ metric.category === 'appearance' ? '请求 sampleId + imageBase64；响应 sampleId + defects' : '请求 sampleId；响应 sampleId + components' }}</small></div>
      <p v-else class="wf-note">仅显示此设备台账已关联的 HTTP 服务。服务需允许自动调用，并保存一条带样本占位符的 POST 请求模板。</p>
    </template>
  </article>
</template>
