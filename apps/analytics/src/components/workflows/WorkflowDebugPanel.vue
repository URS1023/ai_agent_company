<script setup lang="ts">
/**
 * WorkflowDebugPanel —— 编排页的「调试和预览」侧栏（WorkflowWorkbench 内，运行/结果两种状态共用）。
 *
 * 位置：`/workflows/:id`（含设备工作流入口），由 runOpen / resultOpen 控制显示，沉浸式布局下覆盖右侧。
 * 依赖接口：`api/request.can`（是否允许选草稿版本）、`api/workflows`（Workflow / FlowRun / StartInputVariable）、
 * WorkflowStartInputForm（开始节点输入与校验）、WorkflowRunResult（结果回放）。
 * 关键约束：
 * - 运行版本二选一：draft 需 workflows.edit 权限，published 需流程已发布；未发布时该选项禁用，
 *   不允许静默改用草稿；
 * - 写操作需显式同意：只要运行已发布版本或图中含 output-table 节点，就必须勾选确认后才允许执行
 *   （真实数据库写入不可撤销）；
 * - 必须等 WorkflowStartInputForm 报 validity 为 true 才能提交，避免用不完整输入发起真实运行；
 * - 已有结果时"重置"按钮语义变为重新运行（emit restart），由父级决定关闭结果面板而非清空输入。
 */
import { computed, shallowRef } from 'vue'
import type { StartInputVariable, Workflow } from '../../api/workflows'
import type { FlowRun } from '../../api/workflows'
import { can } from '../../api/request'
import WorkflowRunResult from './WorkflowRunResult.vue'
import WorkflowStartInputForm from './WorkflowStartInputForm.vue'
import { usePublishedWorkflow } from '../../composables/usePublishedWorkflow'

const props = defineProps<{
  flow: Workflow
  busy: boolean
  error: string
  domain?: 'alert' | 'quality'
  device?: Record<string, any>
  result?: FlowRun | null
  showResult?: boolean
  refreshing?: boolean
}>()
const inputs = defineModel<string>('inputs', { required: true })
const mode = defineModel<string>('mode', { required: true })
const confirmWrites = defineModel<boolean>('confirmWrites', { required: true })
const emit = defineEmits<{ close: []; run: []; restart: []; refresh: []; resume: [input: unknown]; cancel: [] }>()
const publishedFlow = computed(() => mode.value === 'published' ? props.flow : { ...props.flow, status: 'draft' })
const { contract, loading: contractLoading, error: contractError, ready: contractReady } = usePublishedWorkflow(publishedFlow)
const selectedGraph = computed(() => mode.value === 'published' ? contract.value?.graph : props.flow.graph)
const versionReady = computed(() => mode.value === 'draft' ? can('workflows.edit') : contractReady.value)
/** 调试面板选择正式模式时，与正式运行页共用不可变发布契约。 */
const startVariables = computed<StartInputVariable[]>(() => (selectedGraph.value?.nodes.find(node => node.kind === 'start')?.config.variables || []))

/** 需要写库确认的条件：运行正式版本，或图中存在目标表写入节点。 */
const needsWriteConsent = computed(() => mode.value === 'published' || props.flow.graph.nodes.some(node => node.kind === 'output-table'))
const inputsValid = shallowRef(false)
const inputResetKey = shallowRef(0)

/** 重置输入：清空为 `{}`、递增 reset-key 让子表单丢弃内部草稿，并撤销写库同意勾选。 */
function reset() {
  if (props.showResult) { emit('restart'); return }
  inputs.value = '{}'
  inputResetKey.value++
  confirmWrites.value = false
}

function example() {
  inputs.value = JSON.stringify({ data: [{ temperature: 95 }] }, null, 2)
}
</script>

<template>
  <aside class="wf-debug-panel" :class="{ 'is-result': showResult }" aria-label="调试和预览">
    <header>
      <h2>调试和预览</h2>
      <div>
        <button type="button" :aria-label="showResult ? '重新运行' : '重置运行输入'" :title="showResult ? '重新运行' : '重置'" :disabled="busy" @click="reset"><i class="fa-solid fa-rotate-right" /></button>
        <span />
        <button type="button" aria-label="关闭调试和预览" :disabled="busy" @click="$emit('close')"><i class="fa-solid fa-xmark" /></button>
      </div>
    </header>
    <div class="wf-debug-scroll">
      <WorkflowRunResult v-if="showResult && result" compact :run="result" :busy="busy" :refreshing="refreshing" @refresh="$emit('refresh')" @resume="$emit('resume', $event)" @cancel="$emit('cancel')" />
      <template v-else>
        <section v-if="device" class="wf-debug-device">
          <small>当前设备</small><strong>{{ device.name }}</strong><span>{{ device.code }}<template v-if="device.location"> · {{ device.location }}</template></span>
        </section>
        <section class="wf-debug-input-card">
          <header><strong>用户输入</strong><small>开始节点</small></header>
          <label>执行版本
            <select v-model="mode" :disabled="busy">
              <option v-if="can('workflows.edit')" value="draft">当前已保存草稿</option>
              <option value="published" :disabled="flow.status !== 'published'">已发布正式版本</option>
            </select>
          </label>
          <p v-if="contractLoading" role="status">正在加载发布版本的输入字段…</p>
          <p v-if="contractError" class="wf-error" role="alert">{{ contractError }}</p>
          <WorkflowStartInputForm v-model="inputs" :variables="startVariables" :disabled="busy || !versionReady" :reset-key="inputResetKey" @validity="inputsValid = $event" />
          <div v-if="domain === 'alert'" class="wf-debug-examples">
            <button type="button" :disabled="busy" @click="example">填入预警数据示例</button>
            <small>示例只填入编辑框，确认执行前可继续修改。</small>
          </div>
        </section>
        <label v-if="needsWriteConsent" class="wf-write-consent"><input v-model="confirmWrites" type="checkbox" :disabled="busy" />我已核对所选版本的目标表与字段，确认允许实际数据库写入</label>
        <p v-if="needsWriteConsent" class="wf-note">所选版本包含目标表写入时，需要确认后执行。</p>
        <p v-if="error" role="alert" class="wf-error">{{ error }}</p>
      </template>
    </div>
    <footer v-if="!showResult"><button type="button" class="wf-primary" :disabled="busy || !versionReady || !inputsValid" @click="versionReady && inputsValid && $emit('run')"><i class="fa-solid fa-play" />{{ busy ? '正在执行…' : '运行' }}</button></footer>
  </aside>
</template>
