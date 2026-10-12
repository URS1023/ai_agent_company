<script setup lang="ts">
/**
 * 质检流程向导（在 QualityWorkbench 的"配置并发布检测流程"弹窗内使用）。
 * 职责：填写流程名称与业务说明，维护多个"设备标准组"（每组：设备 + 固定模板修订 + 各指标的检测服务绑定），
 *   可选配置回写目标，并在底部统一展示 incompleteReason（未满足的阻断原因）。
 * 依赖接口：状态来自 useQualityWorkflowWizard（api/qualityWorkflows.ts、api/collection/sources）；
 *   回写配置复用工作流的 OutputTableForm，字段清单必须与后端质检结果表结构一致。
 * 关键交互：提交即"创建并发布"（此步骤不调用设备，仅校验权限、固定修订与服务契约）；
 *   分组上限 30、预估节点上限 100（由 composable 校验）；取消需父组件二次确认。
 * 错误呈现：error / loading 状态内联展示，另有向导底部的 canSubmit 闸门。
 */
import { onMounted } from 'vue'
import type { QualityWorkflowCreated } from '../../api/qualityWorkflows'
import { useQualityWorkflowWizard } from '../../composables/useQualityWorkflowWizard'
import OutputTableForm from '../writeback/OutputTableForm.vue'
import QualityWorkflowGroup from './QualityWorkflowGroup.vue'

const emit = defineEmits<{ created: [value: QualityWorkflowCreated]; cancel: [] }>()
const { form, sources, loading, busy, error, detectorCount, nodeCount, incompleteReason, canSubmit,
  loadSources, updateGroup, addGroup, removeGroup, submit } = useQualityWorkflowWizard()
// 可写回的业务字段白名单：与后端质检结果表列名一一对应，改一处必须同步另一处。
const resultFields = ['qualityInspectionId', 'sampleId', 'deviceId', 'metricId', 'name', 'category', 'outcome', 'reason', 'observation', 'provenance']
/** 提交：仅成功时向上抛 created（父组件负责关闭弹窗并打开运行面板）；失败由向导内 error 展示。 */
async function create() { const value = await submit(); if (value) emit('created', value) }
function updateWriteback(config: Record<string, any>) { form.value = { ...form.value, writeback: config } }
onMounted(loadSources)
</script>

<template>
  <form id="qc-workflow-wizard" class="qc-workflow-wizard" @submit.prevent="create">
    <section class="qc-wizard-overview"><div><span class="qc-sequence">检测流程</span><h2>建立自动检测流程</h2></div><dl><div><dt>设备标准组</dt><dd>{{ form.groups.length }}</dd></div><div><dt>指标服务</dt><dd>{{ detectorCount }}</dd></div><div><dt>生成节点</dt><dd>{{ nodeCount }} / 100</dd></div></dl></section>
    <section class="qc-workflow-meta"><label>流程名称<input v-model="form.name" required maxlength="200" :disabled="busy" placeholder="例如：成品外观与成分自动终检" /></label><label>业务说明<textarea v-model="form.description" rows="2" maxlength="2000" :disabled="busy" placeholder="可选：产线、产品、班组与适用范围" /></label></section>
    <div class="qc-wizard-guidance"><strong>配置依赖</strong><p>先维护设备、检验模板和 HTTP 检测服务。</p><div class="wf-actions"><RouterLink to="/alerts/devices">设备</RouterLink><RouterLink to="/quality/templates">模板</RouterLink><RouterLink to="/datasources">HTTP 服务</RouterLink></div></div>
    <p v-if="error" class="wf-error" role="alert">{{ error }}</p><p v-if="loading" role="status">正在读取已授权的 HTTP 检测服务…</p>
    <div class="qc-workflow-groups"><QualityWorkflowGroup v-for="(group, index) in form.groups" :key="group.key" :model-value="group" :index="index" :sources="sources" :disabled="busy || loading" :removable="form.groups.length > 1" @update:model-value="updateGroup(index, $event)" @remove="removeGroup(index)" /></div>
    <button type="button" class="qc-add-group" :disabled="busy || loading || form.groups.length >= 30" @click="addGroup">＋ 添加设备标准组</button>
    <section class="qc-writeback-option"><label class="qc-check"><input v-model="form.writebackEnabled" type="checkbox" :disabled="busy" />将报告结果写入已授权业务表</label><p>启用后选择目标表和字段；每批运行仍需再次确认。</p><OutputTableForm v-if="form.writebackEnabled" :config="form.writeback" :disabled="busy" :fields="resultFields" @update="updateWriteback" /></section>
    <footer class="qc-wizard-footer"><div><strong>{{ incompleteReason || '配置已具备发布条件' }}</strong><p>发布前校验权限、固定修订与服务契约；此步骤不调用设备。</p></div><div class="wf-actions"><button type="button" :disabled="busy" @click="$emit('cancel')">取消</button><button type="submit" class="wf-primary" :disabled="!canSubmit">{{ busy ? '正在校验并发布…' : '创建并发布检测流程' }}</button></div></footer>
  </form>
</template>
