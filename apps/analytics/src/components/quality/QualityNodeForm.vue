<script setup lang="ts">
/**
 * 质检节点配置表单（工作流 qc-appearance / qc-component / qc-evaluate 三种节点的公共表单）。
 * 路由：无独立路由，由工作流节点表单（WorkflowNodeForm）按 node.kind 渲染。
 * 依赖接口：通过 change 事件把 config 交回父组件（受控写法，组件内不改 config）；下拉数据由 QualityResourceSelect 读取
 *   /api/v1/alerts/devices、/api/v1/quality/templates、/api/v1/quality/metrics、/api/v1/collection/sources。
 * 关键交互：检测类节点必须固定"指标版本 + 检测服务版本"（成对写回，版本错配会导致运行期取到被改过的服务）；
 *   请求参数来源 saved/input 决定使用已保存请求体还是按上游样本逐条绑定；
 *   判定节点必须固定模板修订与样本来源（fixed 单样本 / input 逐样本分别生成报告）。
 * 约束：检测服务必须与当前设备关联且允许自动调用；未配置或执行失败不会生成占位合格结果（提示文案即业务规则）。
 */
import { qualityBase, type QualityRecord } from '../../api/quality'
import InlineQualityTemplateSelect from './InlineQualityTemplateSelect.vue'
import QualityResourceSelect from './QualityResourceSelect.vue'

const props = defineProps<{
  kind: string
  config: Record<string, any>
  disabled: boolean
  device?: Record<string, any>
}>()
const emit = defineEmits<{ update: [config: Record<string, any>] }>()
/** 单字段更新：以新 config 对象整体回传（父组件持有 config，本组件不就地修改）。 */
function patch(key: string, value: unknown) { emit('update', { ...props.config, [key]: value }) }
/** 选择资源：把资源 id 与其 version 成对写回（revisionKey 用于模板修订/指标版本/服务版本）。 */
function choose(row: QualityRecord | null, idKey: string, revisionKey: string) {
  emit('update', { ...props.config, [idKey]: row?.id || '', [revisionKey]: row?.version || 1 })
}
</script>

<template>
  <div class="quality-ui">
    <template v-if="kind === 'qc-evaluate'">
      <InlineQualityTemplateSelect v-if="device" :config="config" :device="device" :disabled="disabled" @update="$emit('update', $event)" />
      <template v-else>
        <QualityResourceSelect :model-value="config.deviceId || ''" endpoint="/api/v1/alerts/devices" label="质检设备" :disabled="disabled" @update:model-value="patch('deviceId', $event)" />
        <QualityResourceSelect :model-value="config.templateId || ''" :endpoint="qualityBase + '/templates'" label="检验模板" :disabled="disabled" @select="choose($event, 'templateId', 'templateRevision')" />
      </template>
      <label>固定模板修订<input type="number" min="1" :value="config.templateRevision" :disabled="disabled" @input="patch('templateRevision', Number(($event.target as HTMLInputElement).value))" /></label>
      <label>报告样本来源
        <select :value="config.sampleMode || 'fixed'" :disabled="disabled" @change="$emit('update', { ...config, sampleMode: ($event.target as HTMLSelectElement).value, sampleId: '' })">
          <option value="fixed">固定单个样本</option>
          <option value="input">按上游样本分别生成报告</option>
        </select>
      </label>
      <label v-if="config.sampleMode !== 'input'">样本 / 批次标识<input :value="config.sampleId" :disabled="disabled" maxlength="160" placeholder="例如：2026-09-07-批次01" @input="patch('sampleId', ($event.target as HTMLInputElement).value)" /></label>
      <p class="wf-note">固定模式接受此样本的检测引用或人工录入；逐样本模式按实际检测引用分别生成报告。模板缺失项会记录为未完成，不会误判为合格。</p>
    </template>
    <template v-else>
      <div v-if="device" class="wf-bound-resource"><small>当前设备</small><strong>{{ device.name }}</strong><span>{{ device.code }}{{ device.category ? ` · ${device.category}` : '' }}</span></div>
      <QualityResourceSelect v-else :model-value="config.deviceId || ''" endpoint="/api/v1/alerts/devices" label="质检设备" :disabled="disabled" @update:model-value="patch('deviceId', $event)" />
      <QualityResourceSelect :model-value="config.metricId || ''" :endpoint="qualityBase + '/metrics'" :label="kind === 'qc-appearance' ? '外观指标' : '成分指标'" :disabled="disabled" @select="choose($event, 'metricId', 'metricVersion')" />
      <label>固定指标版本<input :value="config.metricVersion" type="number" min="1" :disabled="disabled" @input="patch('metricVersion', Number(($event.target as HTMLInputElement).value))" /></label>
      <QualityResourceSelect :model-value="config.datasourceId || ''" endpoint="/api/v1/collection/sources" label="设备检测服务" :disabled="disabled" @select="choose($event, 'datasourceId', 'datasourceVersion')" />
      <label>确认连接 / 请求配置版本<input :value="config.datasourceVersion" type="number" min="1" :disabled="disabled" @input="patch('datasourceVersion', Number(($event.target as HTMLInputElement).value))" /></label>
      <label>请求参数来源
        <select :value="config.requestMode || 'saved'" :disabled="disabled" @change="patch('requestMode', ($event.target as HTMLSelectElement).value)">
          <option value="saved">已保存的固定请求体</option>
          <option value="input">上游样本逐条绑定</option>
        </select>
      </label>
      <template v-if="config.requestMode === 'input'">
        <label>样本编号字段<input :value="config.sampleField || 'sampleId'" :disabled="disabled" maxlength="120" @input="patch('sampleField', ($event.target as HTMLInputElement).value)" /></label>
        <label v-if="kind === 'qc-appearance'">原图文件标识字段<input :value="config.imageField || 'imageFileId'" :disabled="disabled" maxlength="120" @input="patch('imageField', ($event.target as HTMLInputElement).value)" /></label>
        <p class="wf-note">输入 1–100 条样本。HTTP 服务请求模板可引用 ${input.sampleId}；外观检测还可引用 ${input.imageBase64}。</p>
      </template>
      <p class="wf-note">检测服务必须与当前设备关联，并允许自动采集。未配置或执行失败不会生成占位合格结果。</p>
    </template>
  </div>
</template>
