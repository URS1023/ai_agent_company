<script setup lang="ts">
/**
 * WorkflowRunResult —— 运行结果详情（调试面板、日志页"查看结果"、版本与运行弹窗共用）。
 *
 * 依赖接口：`api/workflows`（FlowRun / flowStatus）、`api/collection.jsonCell`（单元格安全呈现）、
 * `api/request.can`（按权限提示预警/质检报告入口）、writeback/WorkflowWriteReceipts（写库回执）、
 * WorkflowRunControls（等待输入/取消）、WorkflowIssues、WorkflowValuesPreview、WorkflowBusinessSummary、
 * WorkflowReportMarkdown。
 * 关键约束：
 * - 默认选中节点优先级：带 schemaVersion === 'workflow-business-report-v1' 的业务报告输出 >
 *   第一个输出 > 失败节点 > 末节点；用户手动切换后（selectedByUser）不得被状态轮询覆盖，
 *   否则运行中的自动刷新会把用户看到的节点切走；
 * - 节点被移除时回退到失败节点/末节点，避免选中态指向不存在的数据；
 * - 空数据必须显式呈现："没有可判定的数据，不生成合格结论"，不允许用空表格冒充通过；
 * - 运行中/等待输入的运行状态由父级每 3 秒刷新（文案已说明），本组件只负责渲染。
 */
import { computed, shallowRef, watch } from 'vue'
import { flowStatus, type FlowRun } from '../../api/workflows'
import { jsonCell } from '../../api/collection'
import { can } from '../../api/request'
import WorkflowWriteReceipts from '../writeback/WorkflowWriteReceipts.vue'
import WorkflowRunControls from './WorkflowRunControls.vue'
import WorkflowIssues from './WorkflowIssues.vue'
import WorkflowValuesPreview from './WorkflowValuesPreview.vue'
import WorkflowReportMarkdown from './WorkflowReportMarkdown.vue'
import WorkflowBusinessSummary from './WorkflowBusinessSummary.vue'
const props = withDefaults(defineProps<{ run: FlowRun; busy?: boolean; refreshing?: boolean; compact?: boolean }>(), { busy: false, refreshing: false, compact: false })
defineEmits<{ resume: [input: unknown]; cancel: []; refresh: [] }>()
const selected = shallowRef(''), page = shallowRef(0)
const selectedByUser = shallowRef(false)
const nodes = computed(() => Object.values(props.run.result?.nodes || {}))
/** 默认展示节点：优先业务报告输出，其次任一输出，再退到失败节点/末节点；无结果时返回空串。 */
function preferredNode() { return Object.entries(props.run.result?.outputs || {}).find(([, value]: [string, any]) => value?.schemaVersion === 'workflow-business-report-v1')?.[0] || Object.keys(props.run.result?.outputs || {})[0] || nodes.value.find(node => node.status === 'failed')?.nodeId || nodes.value.at(-1)?.nodeId || '' }
watch(() => props.run.id, () => { selectedByUser.value = false; selected.value = preferredNode(); page.value = 0 }, { immediate: true })
watch(() => props.run.status, () => { if (!selectedByUser.value) selected.value = preferredNode() })
watch(nodes, value => {
  if (!value.some(node => node.nodeId === selected.value)) selected.value = value.find(node => node.status === 'failed')?.nodeId || value.at(-1)?.nodeId || ''
})
const state = computed(() => props.run.result?.nodes?.[selected.value])
const output = computed(() => state.value?.output)
/** 兼容 python_execute 包装层：真实业务结果在 data.result 里，直接展示包装对象会误导用户。 */
const displayOutput = computed(() => output.value?.toolName === 'python_execute' && output.value?.data && Object.hasOwn(output.value.data, 'result')
  ? output.value.data.result : output.value)
const textOutput = computed(() => displayOutput.value !== undefined && (displayOutput.value === null || typeof displayOutput.value !== 'object') ? jsonCell(displayOutput.value) : null)
const reportContent = computed(() => typeof displayOutput.value?.content === 'string' ? displayOutput.value.content : '')
const outputFields = computed(() => displayOutput.value && typeof displayOutput.value === 'object' && !Array.isArray(displayOutput.value) && !Array.isArray(displayOutput.value.data)
  ? Object.entries(displayOutput.value) : [])
const listOutput = computed<any[] | null>(() => Array.isArray(displayOutput.value) ? displayOutput.value
  : Array.isArray(displayOutput.value?.data) ? displayOutput.value.data
    : Array.isArray(displayOutput.value?.result) ? displayOutput.value.result : null)
const rows = computed<Record<string, unknown>[]>(() => (listOutput.value || []).map(value =>
  value !== null && typeof value === 'object' && !Array.isArray(value) ? value : { value }))
const columns = computed<string[]>(() => displayOutput.value?.columns?.map((column: any) => column.columnName) || [...new Set(rows.value.flatMap(row => Object.keys(row)))])
const columnLabels = computed<Record<string, string>>(() => Object.fromEntries((Array.isArray(displayOutput.value?.columns) ? displayOutput.value.columns : []).filter((c: any) => c?.columnName && c.comment).map((c: any) => [c.columnName, c.comment])))
const visibleRows = computed(() => rows.value.slice(page.value * 50, (page.value + 1) * 50))
const issues = computed<any[]>(() => output.value?.issues || [])
/** 业务判定结果判定依据：同时存在「结果」与「说明」两列才走业务视图（业务摘要 + 字段预览）。 */
const businessOutput = computed(() => rows.value.some(row => Object.hasOwn(row, '结果') && Object.hasOwn(row, '说明')))
watch(selected, () => { page.value = 0 })
</script>
<template>
  <section class="wf-result" :class="{ 'wf-result--compact': compact }" aria-label="工作流运行结果"><div class="wf-heading"><div><h2>{{ flowStatus[run.status] }}</h2><p>修订 v{{ run.revision }} · {{ run.durationMs }} ms · 运行 {{ run.id }}</p></div><button :disabled="busy || refreshing" @click="$emit('refresh')">{{ refreshing ? '刷新中…' : '刷新结果' }}</button></div><p v-if="['running', 'waiting', 'cancel_requested'].includes(run.status)" class="wf-note" role="status">运行状态每 3 秒自动更新，可关闭此窗口后从“版本与运行”继续查看。</p><p v-if="run.errorMessage" role="alert" class="wf-error">{{ run.errorMessage }}</p>
    <p v-if="run.result?.collectionWindow" class="wf-note">采集时间：{{ run.result.collectionWindow.start }} 至 {{ run.result.collectionWindow.end }}（{{ run.result.collectionWindow.timeZone }}）<span v-if="run.result.collectionWindow.hasMore"> · 本时间段还有后续批次</span></p>
    <WorkflowRunControls :run="run" :busy="busy" @resume="$emit('resume', $event)" @cancel="$emit('cancel')" />
    <WorkflowWriteReceipts :execution-id="run.id" />
    <label>查看节点<select v-model="selected" @change="selectedByUser = true"><option v-for="node in nodes" :key="node.nodeId" :value="node.nodeId">{{ node.title }} · {{ flowStatus[node.status] }}</option></select></label>
    <p v-if="state?.message" class="wf-note">{{ state.message }}</p><p v-if="state?.references?.query_id">采集/查询历史：{{ state.references.query_id }}</p>
    <WorkflowReportMarkdown v-if="reportContent" :content="reportContent" />
    <section v-else-if="textOutput !== null || outputFields.length" class="wf-value-output" aria-label="节点输出"><h3>节点输出</h3><p v-if="textOutput !== null">{{ textOutput }}</p><dl v-else><template v-for="[name, value] in outputFields" :key="name"><dt>{{ name }}</dt><dd>{{ jsonCell(value) }}</dd></template></dl></section>
    <p v-if="output?.summary?.status === 'no_data'" role="status">没有可判定的数据，不生成合格结论。</p>
    <p v-if="output?.alertReportId && can('alerts.view')" class="wf-note">预警报告与问题已保存在本次运行中，可从“版本与运行”再次查看。</p>
    <p v-if="output?.qualityInspectionId && can('quality.view')" class="wf-note">质检报告与逐项结果已保存在本次运行中，可从“版本与运行”再次查看。</p>
    <WorkflowBusinessSummary v-if="businessOutput && !reportContent" :rows="rows" />
    <template v-if="output?.issues"><h3>问题列表 · {{ issues.length }} 项</h3><p v-if="!issues.length">{{ output.summary?.status === 'no_data' ? '没有执行任何逐项判定。' : '在本次数据和规则范围内未发现问题。' }}</p><WorkflowIssues v-else :issues="issues" :rows="rows" /></template>
    <h3 v-if="listOutput !== null">数据结果 · {{ rows.length }} 行</h3><WorkflowValuesPreview v-if="businessOutput" :output="displayOutput" /><div v-else-if="rows.length" class="wf-table-wrap" tabindex="0" role="region" aria-label="节点数据结果，可横向滚动"><table class="wf-table"><thead><tr><th v-for="column in columns" :key="column">{{ columnLabels[column] || column }}<small v-if="columnLabels[column]" class="wf-column-name">{{ column }}</small></th></tr></thead><tbody><tr v-for="(row, index) in visibleRows" :key="index"><td v-for="column in columns" :key="column">{{ jsonCell(row[column]) }}</td></tr></tbody></table></div><div v-if="!businessOutput && rows.length > 50" class="wf-actions"><button :disabled="page === 0" @click="page--">上一页数据</button><span>第 {{ page + 1 }} 页</span><button :disabled="(page + 1) * 50 >= rows.length" @click="page++">下一页数据</button></div>
    <details><summary>运行元信息与节点原始输出</summary><dl><dt>统一运行标识</dt><dd>{{ run.runId }}</dd><dt>节点状态</dt><dd>{{ flowStatus[state?.status] }}</dd></dl><pre tabindex="0" role="region" aria-label="节点原始输出，可滚动查看">{{ JSON.stringify(output ?? state?.references ?? {}, null, 2) }}</pre></details>
  </section>
</template>
<style scoped>
.wf-column-name { display: block; margin-top: 3px; font-size: 11px; font-weight: 400; color: #667085; }
</style>
