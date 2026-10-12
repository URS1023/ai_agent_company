<script setup lang="ts">
/**
 * WorkflowNodeForm —— 节点属性面板（画布右侧），按节点 kind 分派到各专用子表单。
 *
 * 位置：WorkflowEditor 的右栏，选中节点时渲染（`/workflows/:id` 与设备工作流入口）。
 * 依赖接口：`api/workflows`（FlowNode / FlowEdge / NodeKind / StartInputVariable / copy）、
 * `utils/workflowJson.parseWorkflowJson`（宽松 JSON 解析，用于"原始配置"编辑）、
 * 子表单：WorkflowDataSourceBinding、WorkflowStartFieldsEditor、WorkflowReadValuesForm、WorkflowCalculationForm、
 * WorkflowOutputValuesForm、WorkflowAggregateValuesForm、WorkflowListOperationForm、WorkflowInputBindings、
 * WorkflowHttpRequestForm、WorkflowAdvancedNodeForm，以及 alerts/writeback/quality 的业务节点表单。
 * 关键约束：
 * - 节点配置以 `contract.configSchema`（后端下发）为准：required/描述/取值规则直接取自契约，
 *   前端不得自行改写或回退默认模板；"未选模板时禁止回退默认模板"的语义由子表单与后端共同保证；
 * - 所有修改通过 `config()` 以不可变方式上抛 update，父级负责自动保存与冲突处理；
 * - `disabled`（只读模式）下所有输入不可编辑，且原始 JSON 也不允许应用；
 * - 重试与错误分支对部分节点无意义（retryUnsupported / errorUnsupported 集合），
 *   这些集合必须与后端能力清单保持一致，否则会生成后端不支持的配置；
 * - formFields 依赖上游字段推导（带环检测与缓存），仅用于字段下拉提示，不作为校验依据。
 */
import { computed, shallowRef, watch } from 'vue'
import { copy, type FlowEdge, type FlowNode, type NodeKind, type StartInputVariable } from '../../api/workflows'
import { parseWorkflowJson } from '../../utils/workflowJson.js'
import { createClientId } from '../../utils/randomId'
import ExpressionEditor from './ExpressionEditor.vue'
import AlertPolicySelect from '../alerts/AlertPolicySelect.vue'
import OutputTableForm from '../writeback/OutputTableForm.vue'
import QualityNodeForm from '../quality/QualityNodeForm.vue'
import HttpFieldSelection from '../collection/HttpFieldSelection.vue'
import WorkflowAdvancedNodeForm from './WorkflowAdvancedNodeForm.vue'
import WorkflowDataSourceBinding from './WorkflowDataSourceBinding.vue'
import WorkflowStartFieldsEditor from './WorkflowStartFieldsEditor.vue'
import WorkflowListOperationForm from './WorkflowListOperationForm.vue'
import WorkflowInputBindings from './WorkflowInputBindings.vue'
import WorkflowReadValuesForm from './WorkflowReadValuesForm.vue'
import WorkflowCalculationForm from './WorkflowCalculationForm.vue'
import WorkflowOutputValuesForm from './WorkflowOutputValuesForm.vue'
import WorkflowAggregateValuesForm from './WorkflowAggregateValuesForm.vue'
import WorkflowValuesPreview from './WorkflowValuesPreview.vue'
import WorkflowHttpRequestForm from './WorkflowHttpRequestForm.vue'
import '../../styles/http-collection.css'
import '../../styles/workflow-node-contracts.css'
const props = withDefaults(defineProps<{ node: FlowNode; nodes: FlowNode[]; edges: FlowEdge[]; sources: any[]; resources: { knowledgeBases: any[]; bots: any[]; tools: any[]; workflows: any[]; models: any[] }; disabled: boolean; fields: string[]; catalog?: NodeKind[]; contract?: NodeKind; errors: string[]; validationKnown: boolean; initialTab?: 'settings' | 'lastRun'; lastRun?: any; device?: Record<string, any> }>(), { initialTab: 'settings', lastRun: undefined, device: undefined, catalog: () => [] })
const emit = defineEmits<{
  update: [node: FlowNode]
  errorStrategy: [node: FlowNode]
  addNext: [value: { nodeId: string; direction: 'after'; side: 'right'; port: FlowEdge['port']; clientX: number; clientY: number }]
  remove: [id: string]
  close: []
  sourceSaved: [source: any]
  run: []
}>()
const json = shallowRef(''), defaultJson = shallowRef('{}'), error = shallowRef('')
const tab = shallowRef<'settings' | 'lastRun'>(props.initialTab)
watch(() => props.node, node => {
  json.value = JSON.stringify(node.config, null, 2)
  defaultJson.value = JSON.stringify(node.defaultValue ?? {}, null, 2)
  error.value = ''
}, { immediate: true, deep: true })
watch(() => props.initialTab, value => { tab.value = value })
const kind = computed(() => props.node.kind)
const valueGraph = computed(() => props.nodes.some(n => n.kind === 'read-values'))
/**
 * 自上游推导本节点可用的字段名列表（仅 read-values 参与推导时启用）。
 * 用 visiting 做环检测、cache 做记忆化，避免环形图导致无限递归；
 * 各节点类型对输出的定义必须与后端保持一致，否则字段下拉会与运行结果对不上。
 */
const formFields = computed(() => {
  if (!valueGraph.value) return props.fields
  const cache = new Map<string, string[]>(), visiting = new Set<string>([props.node.id])
  function outputFields(id: string): string[] {
    if (visiting.has(id)) return []
    if (cache.has(id)) return cache.get(id)!
    const node = props.nodes.find(n => n.id === id)
    if (!node) return []
    visiting.add(id)
    const incoming = props.edges.filter(e => e.target === id).flatMap(e => outputFields(e.source))
    let fields = incoming
    if (node.kind === 'start') fields = (node.config.variables || []).map((v: StartInputVariable) => v.variable)
    if (node.kind === 'read-values') fields = [...(node.config.matchMode && node.config.matchMode !== 'off' ? [...(node.config.keepInputFields?.length ? node.config.keepInputFields : incoming), node.config.matchCountField || '匹配记录数'] : []), ...(node.config.fields || []).map((f: string) => node.config.fieldAliases?.[f] || f)]
    if (node.kind === 'calculate-values') fields = [...incoming, ...(node.config.calculations || []).map((c: any) => c.name)]
    if (node.kind === 'aggregator') fields = [...(node.config.groupBy || []), ...(node.config.metrics || []).map((m: any) => m.name)]
    if (node.kind === 'output-values') fields = [...(node.config.fields?.length ? node.config.fields : incoming), '结果', '说明', ...(node.config.mode === 'quality' ? ['不合格标记', '待确认标记'] : [])]
    visiting.delete(id)
    const result = [...new Set<string>(fields)]
    cache.set(id, result)
    return result
  }
  return [...new Set(props.edges.filter(e => e.target === props.node.id).flatMap(e => outputFields(e.source)))]
})
const nodeIcons: Record<string, string> = { 'document-extractor': 'fa-file-lines', 'list-operator': 'fa-list-ol', start: 'fa-play', end: 'fa-flag-checkered', condition: 'fa-code-branch', 'http-request': 'fa-cloud-arrow-down', 'database-query': 'fa-database', 'alert-evaluate': 'fa-triangle-exclamation', 'alert-notify': 'fa-bell', 'qc-evaluate': 'fa-clipboard-check', 'qc-appearance': 'fa-eye', 'qc-component': 'fa-flask', llm: 'fa-wand-magic-sparkles', knowledge: 'fa-book-open', template: 'fa-file-code', 'output-table': 'fa-table' }
Object.assign(nodeIcons, { 'read-values': 'fa-database', 'calculate-values': 'fa-calculator', 'output-values': 'fa-file-export' })
const nodeIcon = computed(() => nodeIcons[kind.value] || 'fa-cube')
const lastRunStatus = computed(() => props.lastRun?.status || '')
const lastRunStatusLabel = computed(() => ({ pending: '等待执行', running: '执行中', waiting: '等待输入', completed: '执行成功', failed: '执行失败', skipped: '未执行', cancelled: '已取消' } as Record<string, string>)[lastRunStatus.value] || '')
function pretty(value: unknown) {
  if (value === undefined) return ''
  try { return JSON.stringify(value, null, 2) } catch { return String(value) }
}
const constraints = computed(() => {
  const schema = props.contract?.configSchema || {}
  const required = new Set<string>(schema.required || [])
  return Object.entries<Record<string, any>>(schema.properties || {}).map(([name, property]) => ({
    name,
    required: required.has(name),
    description: property.description || '',
    rule: constraintRule(property),
  }))
})
const requiredConstraintCount = computed(() => constraints.value.filter(item => item.required).length)
const advancedKinds = new Set(['llm', 'extractor', 'knowledge', 'agent', 'question', 'delay', 'classifier', 'setvar', 'text', 'template', 'aggregator', 'loop', 'iterator', 'while', 'parallel', 'subflow', 'tool', 'code'])
const retryUnsupported = new Set(['start', 'end', 'reply', 'question', 'delay', 'merge', 'condition', 'classifier', 'parallel'])
const errorUnsupported = new Set(['start', 'end', 'reply', 'question', 'delay', 'merge', 'condition', 'classifier', 'parallel'])
const canRetry = computed(() => !retryUnsupported.has(kind.value))
const canHandleError = computed(() => !errorUnsupported.has(kind.value))
type OutputVariable = { name: string; type: string; description: string }
const fixedOutputs: Record<string, OutputVariable[]> = {
  'document-extractor': [{ name: 'text', type: 'String', description: '文件提取出的文本' }],
  'list-operator': [{ name: 'result', type: 'Array', description: '处理后的列表' }, { name: 'first_record', type: 'Any', description: '首项；空列表返回 null' }, { name: 'last_record', type: 'Any', description: '末项；空列表返回 null' }, { name: 'count', type: 'Number', description: '输出项数' }],
  'http-request': [{ name: 'success', type: 'Boolean', description: '采集是否完整成功' }, { name: 'data', type: 'Array[Object]', description: '真实 HTTP 响应记录' }, { name: 'queryId', type: 'String', description: '采集运行标识' }],
  'database-query': [{ name: 'data', type: 'Array[Object]', description: '查询结果记录' }, { name: 'totalCount', type: 'Number', description: '实际返回行数' }, { name: 'queryId', type: 'String', description: '查询运行标识' }],
  'report-analysis': [{ name: 'data', type: 'Array[Object]', description: '分析查询结果' }, { name: 'totalCount', type: 'Number', description: '结果行数' }],
  'jsonpath-extract': [{ name: 'data', type: 'Array[Object]', description: '提取并校验后的记录' }, { name: 'columns', type: 'Array[Object]', description: '输出字段定义' }],
  condition: [{ name: 'matched', type: 'Boolean', description: '条件是否满足' }, { name: 'data', type: 'Object', description: '传递给分支的原输入' }],
  classifier: [{ name: 'category', type: 'String', description: '命中的分类标识' }, { name: 'label', type: 'String', description: '分类名称' }, { name: 'data', type: 'Object', description: '原输入' }],
  llm: [{ name: 'content', type: 'String', description: '模型生成内容' }, { name: 'model', type: 'String', description: '实际调用模型' }, { name: 'usage', type: 'Object', description: 'Token 用量' }],
  knowledge: [{ name: 'results', type: 'Array[Object]', description: '知识检索结果' }, { name: 'totalCount', type: 'Number', description: '命中数量' }],
  question: [{ name: 'input', type: 'Any', description: '进入节点的输入' }, { name: 'answer', type: 'Any', description: '用户提交的回答' }],
  iterator: [{ name: 'data', type: 'Array[Object]', description: '逐项处理结果' }, { name: 'iteratedCount', type: 'Number', description: '迭代项数' }],
  loop: [{ name: 'data', type: 'Object', description: '循环完成后的值' }, { name: 'iterations', type: 'Number', description: '实际循环次数' }],
  while: [{ name: 'data', type: 'Object', description: '循环完成后的值' }, { name: 'iterations', type: 'Number', description: '实际循环次数' }, { name: 'terminated', type: 'Boolean', description: '是否正常结束' }],
  subflow: [{ name: 'outputs', type: 'Object', description: '子流程正式版本输出' }, { name: 'childRunId', type: 'String', description: '子流程运行标识' }],
  code: [{ name: 'data', type: 'Object', description: '隔离代码执行结果' }],
  tool: [{ name: 'data', type: 'Any', description: '工具实际返回值' }, { name: 'status', type: 'String', description: '工具执行状态' }],
  'output-table': [{ name: 'writeOperationId', type: 'String', description: '可追溯写入操作标识' }, { name: 'affectedRows', type: 'Number', description: '实际影响行数' }],
  'alert-evaluate': [{ name: 'alertReportId', type: 'String', description: '预警报告标识' }, { name: 'issues', type: 'Array[Object]', description: '实际命中的问题' }, { name: 'passed', type: 'Boolean', description: '本次是否正常' }],
  'qc-evaluate': [{ name: 'qualityInspectionId', type: 'String', description: '质检记录标识' }, { name: 'issues', type: 'Array[Object]', description: '质检问题' }],
}
/** 节点输出变量说明：用于告知用户"下游能引用哪些字段"，按 kind 静态或按配置动态生成。 */
const outputVariables = computed<OutputVariable[]>(() => {
  if (kind.value === 'read-values') return (props.node.config.fields || []).map((field: string) => ({ name: props.node.config.fieldAliases?.[field] || field, type: 'Any', description: '读取的原始值；缺少记录时为空' }))
  if (kind.value === 'calculate-values') return (props.node.config.calculations || []).map((item: any) => ({ name: item.name || '未命名', type: 'Any', description: '计算结果；同时保留上游字段' }))
  if (kind.value === 'output-values') return [{ name: 'data', type: 'Array[Object]', description: '所选字段、结果与说明' }]
  if (kind.value === 'condition' && props.node.config.mode === 'rows') return [{ name: 'trueRows', type: 'Array[Object]', description: '满足条件的记录，自动沿 IF 传递' }, { name: 'falseRows', type: 'Array[Object]', description: '不满足条件的记录，自动沿 ELSE 传递' }]
  if (kind.value === 'start') return ((props.node.config.variables || []) as StartInputVariable[]).map(item => ({ name: item.variable, type: item.type, description: item.label }))
  if (kind.value === 'extractor') return (props.node.config.fields || []).map((item: any) => ({ name: item.name || '未命名', type: item.type || 'Any', description: item.description || '抽取字段' }))
  if (kind.value === 'arithmetic') return (props.node.config.fields || []).map((item: any) => ({ name: item.name || '未命名', type: 'Decimal', description: '计算字段' }))
  if (kind.value === 'setvar') return (props.node.config.variables || []).map((item: any) => ({ name: item.name || '未命名', type: 'Any', description: '设置的变量' }))
  if (kind.value === 'aggregator') return (props.node.config.metrics || []).map((item: any) => ({ name: item.name || '未命名', type: 'Number', description: '聚合指标' }))
  return fixedOutputs[kind.value] || [{ name: 'output', type: 'Any', description: '节点的实际运行结果' }]
})
/** 本节点各输出端口已连接的下游节点；error 端口仅在开启失败分支时列出，与画布端口的显示保持一致。 */
const nextGroups = computed(() => {
  const ports: FlowEdge['port'][] = kind.value === 'condition' ? ['true', 'false'] : ['out', ...(props.node.errorStrategy === 'fail-branch' ? ['error' as const] : [])]
  return ports.map(port => ({
    port,
    label: port === 'true' ? 'IF' : port === 'false' ? 'ELSE' : port === 'error' ? '失败分支' : '',
    nodes: props.edges.filter(edge => edge.source === props.node.id && edge.port === port).map(edge => props.nodes.find(node => node.id === edge.target)).filter(Boolean) as FlowNode[],
  }))
})
/** 从后端 configSchema 片段提炼人类可读的取值规则（枚举/类型/区间/长度/条数），只做展示。 */
function constraintRule(property: Record<string, any>) {
  const variants = [property, ...(property.anyOf || []), ...(property.oneOf || [])]
  const values = [...new Set(variants.flatMap(item => item.enum || []))]
  const types = [...new Set(variants.map(item => item.type).filter((value: string) => value && value !== 'null'))]
  const typeNames: Record<string, string> = { string: '文本', integer: '整数', number: '数值', object: '对象', array: '列表', boolean: '布尔值' }
  const rules = []
  if (values.length) rules.push('可选：' + values.join(' / '))
  else if (types.length) rules.push(types.map(type => typeNames[String(type)] || String(type)).join(' / '))
  if (property.minimum !== undefined || property.maximum !== undefined) rules.push(`${property.minimum ?? '不限'}～${property.maximum ?? '不限'}`)
  if (property.minLength !== undefined || property.maxLength !== undefined) rules.push(`${property.minLength ?? 0}～${property.maxLength ?? '不限'} 字符`)
  if (property.minItems !== undefined || property.maxItems !== undefined) rules.push(`${property.minItems ?? 0}～${property.maxItems ?? '不限'} 项`)
  return rules.join(' · ')
}
function config(key: string, value: unknown) { emit('update', { ...props.node, config: { ...props.node.config, [key]: value } }) }
function patchItem(key: string, index: number, patch: object) { config(key, props.node.config[key].map((item: any, i: number) => i === index ? { ...item, ...patch } : item)) }
function removeItem(key: string, index: number) { config(key, props.node.config[key].filter((_: unknown, i: number) => i !== index)) }
function addRule() { config('rules', [...(props.node.config.rules || []), { id: 'rule_' + createClientId().slice(0, 8), name: '', severity: 'warning', message: '', expression: { op: 'gte', args: [{ field: '' }, { value: 0 }] } }]) }
function notificationOutcome(value: string, selected: boolean) { config('outcomes', selected ? [...new Set([...(props.node.config.outcomes || []), value])] : (props.node.config.outcomes || []).filter((item: string) => item !== value)) }
/**
 * 应用"原始配置"编辑框内容：必须是 JSON 对象（数组/标量拒绝），解析失败只提示错误、不改动节点配置。
 * 副作用：派发 update 替换整个 config，用户需自行承担绕过表单校验的风险。
 */
function applyJson() { try { const value = parseWorkflowJson(json.value); if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('配置需要 JSON 对象'); emit('update', { ...props.node, config: value }); error.value = '' } catch (cause) { error.value = (cause as Error).message } }
function setStartVariables(value: StartInputVariable[]) { config('variables', value) }
function patchRetry(patch: Partial<FlowNode['retryConfig']>) { emit('update', { ...props.node, retryConfig: { ...(props.node.retryConfig || { retryEnabled: false, maxRetries: 3, retryInterval: 1000 }), ...patch } }) }
/**
 * 切换错误处理策略。改为 default-value 时补空默认值对象，改为其他策略时清空 defaultValue，
 * 避免残留默认值在后端被误用；error 分支的连线清理由父级 updateErrorStrategy 负责。
 */
function setErrorStrategy(value: string) {
  const strategy = value as FlowNode['errorStrategy']
  emit('errorStrategy', { ...props.node, errorStrategy: strategy, defaultValue: strategy === 'default-value' ? props.node.defaultValue ?? {} : null })
}
function applyDefaultJson() {
  try { emit('update', { ...props.node, defaultValue: parseWorkflowJson(defaultJson.value) }); error.value = '' }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '默认值 JSON 无效' }
}
function addNext(port: FlowEdge['port'], event: MouseEvent) {
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect()
  emit('addNext', { nodeId: props.node.id, direction: 'after', side: 'right', port, clientX: rect.right, clientY: rect.top + rect.height / 2 })
}
function convertToUpstreamAnalysis() {
  const goal = String(props.node.config.query || '').trim()
  emit('update', {
    ...props.node,
    kind: 'llm',
    config: {
      modelProfile: 'reasoning',
      systemPrompt: '只分析上游传入的真实数据；明确区分事实、推断与建议，不编造缺失数据。',
      promptTemplate: `分析目标：${goal || '分析上游数据并给出结论'}\n\n上游数据：\n{{ input }}`,
      temperature: 0.1,
    },
  })
}
</script>
<template>
  <aside class="wf-properties" aria-label="节点配置">
    <header class="wf-property-heading">
      <span class="wf-panel-node-icon" aria-hidden="true"><i class="fa-solid" :class="nodeIcon" /></span>
      <label class="wf-panel-title"><span class="wf-visually-hidden">节点名称</span><input :value="node.title" :disabled="disabled" aria-label="节点名称" @input="$emit('update', { ...node, title: ($event.target as HTMLInputElement).value })" /><small>{{ contract?.title || node.kind }}</small></label>
      <button type="button" class="wf-panel-run" :disabled="disabled" aria-label="运行工作流" title="运行工作流" @click="$emit('run')"><i class="fa-solid fa-play" aria-hidden="true" /></button>
      <details class="wf-panel-more"><summary aria-label="节点操作" title="节点操作"><i class="fa-solid fa-ellipsis" aria-hidden="true" /></summary><div><button v-if="kind !== 'start'" type="button" class="danger" @click="$emit('remove', node.id)"><i class="fa-regular fa-trash-can" aria-hidden="true" />删除节点</button></div></details>
      <span class="wf-panel-separator" aria-hidden="true" />
      <button type="button" class="wf-properties-close" aria-label="关闭节点配置" title="关闭" @click="$emit('close')"><i class="fa-solid fa-xmark" aria-hidden="true" /></button>
    </header>
    <nav class="wf-panel-tabs" aria-label="节点面板">
      <button type="button" :class="{ active: tab === 'settings' }" :aria-current="tab === 'settings' ? 'page' : undefined" @click="tab = 'settings'">设置</button>
      <button type="button" :class="{ active: tab === 'lastRun' }" :aria-current="tab === 'lastRun' ? 'page' : undefined" @click="tab = 'lastRun'">上次运行</button>
    </nav>
    <section v-if="tab === 'settings'" class="wf-panel-content">
      <div class="wf-panel-check-state"><span class="wf-contract-state" :class="{ invalid: errors.length, valid: validationKnown && !errors.length }">{{ validationKnown ? errors.length ? `待补 ${errors.length} 项` : '配置已通过' : '尚未检查' }}</span></div>
      <section v-if="errors.length" class="wf-node-issues" role="alert"><strong>当前节点尚不可运行</strong><ul><li v-for="message in errors" :key="message">{{ message }}</li></ul></section>
      <fieldset :disabled="disabled">
    <label class="wf-node-description"><span class="wf-visually-hidden">节点描述</span><textarea :value="node.description" rows="2" maxlength="1000" placeholder="添加描述..." @input="$emit('update', { ...node, description: ($event.target as HTMLTextAreaElement).value })" /></label>
    <template v-if="kind === 'start'"><label class="wf-config-label"><input type="checkbox" :checked="node.config.incrementalWindow" :disabled="disabled" @change="config('incrementalWindow', ($event.target as HTMLInputElement).checked)" />按上次成功采集至本次运行的时间取新增和更新数据</label><label v-if="node.config.incrementalWindow">首次回看分钟数<input type="number" min="1" max="5256000" :value="node.config.lookbackMinutes || 30" :disabled="disabled" @input="config('lookbackMinutes', Number(($event.target as HTMLInputElement).value))" /></label><WorkflowStartFieldsEditor :model-value="node.config.variables || []" :disabled="disabled" @update:model-value="setStartVariables" /></template>
    <WorkflowReadValuesForm v-else-if="kind === 'read-values'" :config="node.config" :sources="sources" :fields="formFields" :device="device" :disabled="disabled" @update="$emit('update', { ...node, config: $event })" />
    <WorkflowCalculationForm v-else-if="kind === 'calculate-values'" :config="node.config" :fields="formFields" :disabled="disabled" @update="$emit('update', { ...node, config: $event })" />
    <WorkflowOutputValuesForm v-else-if="kind === 'output-values'" :config="node.config" :fields="formFields" :disabled="disabled" @update="$emit('update', { ...node, config: $event })" />
    <WorkflowAggregateValuesForm v-else-if="kind === 'aggregator'" :config="node.config" :fields="formFields" @update="$emit('update', { ...node, config: $event })" />
    <WorkflowListOperationForm v-else-if="kind === 'list-operator'" :config="node.config" :fields="formFields" @update="$emit('update', { ...node, config: $event })" />
    <template v-else-if="kind === 'document-extractor'"><p class="wf-note">在输入变量中选择开始节点的文件或文件列表，提取结果输出为 text。</p><label>文件间分隔符<textarea :value="node.config.separator" rows="2" @input="config('separator', ($event.target as HTMLTextAreaElement).value)" /></label></template>
    <template v-else-if="['http-request', 'database-query', 'report-analysis'].includes(kind)">
      <WorkflowDataSourceBinding :kind="kind" :config="node.config" :sources="sources" :disabled="disabled" @update="$emit('update', { ...node, config: $event })" @source-saved="$emit('sourceSaved', $event)" />
      <template v-if="kind === 'http-request'">
        <WorkflowHttpRequestForm :config="node.config" :disabled="disabled" @update="$emit('update', { ...node, config: $event })" />
      </template>
      <template v-if="kind !== 'http-request'"><label>{{ kind === 'database-query' ? '只读 SQL' : '分析问题' }}<textarea :value="node.config.query" rows="5" @input="config('query', ($event.target as HTMLTextAreaElement).value)" /></label><label>最多返回行数<input type="number" min="1" max="10000" :value="node.config.maxRows" @change="config('maxRows', Number(($event.target as HTMLInputElement).value))" /></label><small>{{ kind === 'database-query' ? '动态 SQL 参数通过命名输入 parameters 绑定，不进行字符串拼接。' : '此节点会独立查询所选数据源；分析上游 HTTP 结果请使用“大模型”或“聚合”节点。' }}</small><button v-if="kind === 'report-analysis'" type="button" @click="convertToUpstreamAnalysis">改为分析上游数据</button></template>
    </template>
    <template v-else-if="kind === 'arithmetic'"><div v-for="(field, index) in node.config.fields" :key="index" class="wf-rule-card"><label>输出字段<input :value="field.name" @input="patchItem('fields', index, { name: ($event.target as HTMLInputElement).value })" /></label><label v-if="field.formula !== undefined">计算公式<input :value="field.formula" placeholder="例如：温度 * 1.8 + 32" @input="patchItem('fields', index, { formula: ($event.target as HTMLInputElement).value })" /></label><ExpressionEditor v-else :model-value="field.expression" :fields="formFields" @update:model-value="patchItem('fields', index, { expression: $event })" /><button :disabled="node.config.fields.length <= 1" @click="removeItem('fields', index)">移除此计算</button></div><button @click="config('fields', [...node.config.fields, { name: '', formula: '' }])">添加计算字段</button><p class="wf-note">按顺序计算，支持中文字段、加减乘除与括号；精确小数不按浮点数舍入。</p></template>
    <QualityNodeForm v-else-if="kind.startsWith('qc-')" :kind="kind" :config="node.config" :disabled="disabled" :device="device" @update="$emit('update', { ...node, config: $event })" />
    <template v-else-if="['condition', 'filter'].includes(kind)"><label v-if="kind === 'condition'">判断对象<select :value="node.config.mode || 'record'" @change="config('mode', ($event.target as HTMLSelectElement).value)"><option value="record">一个对象</option><option value="rows">每条记录分别判断</option></select></label><ExpressionEditor :model-value="node.config.expression" :fields="formFields" @update:model-value="config('expression', $event)" /><p v-if="kind === 'condition' && node.config.mode === 'rows'" class="wf-note">满足条件的记录沿 IF 连线传递，其余记录沿 ELSE 连线传递。</p></template>
    <template v-else-if="kind === 'rule-evaluate'"><label>判定用途<select :value="node.config.mode" @change="config('mode', ($event.target as HTMLSelectElement).value)"><option value="alert">预警：满足条件生成问题</option><option value="quality">质检：不满足合格条件生成问题</option></select></label><div v-for="(rule, index) in node.config.rules" :key="rule.id" class="wf-rule-card"><label>规则名称<input :value="rule.name" @input="patchItem('rules', index, { name: ($event.target as HTMLInputElement).value })" /></label><ExpressionEditor :model-value="rule.expression" :fields="formFields" @update:model-value="patchItem('rules', index, { expression: $event })" /><label>问题等级<select :value="rule.severity" @change="patchItem('rules', index, { severity: ($event.target as HTMLSelectElement).value })"><option value="info">提示</option><option value="warning">警告</option><option value="critical">严重</option></select></label><label>问题说明<input :value="rule.message" @input="patchItem('rules', index, { message: ($event.target as HTMLInputElement).value })" /></label><button :disabled="node.config.rules.length <= 1" @click="removeItem('rules', index)">移除此规则</button></div><button @click="addRule">添加规则项</button></template>
    <template v-else-if="kind === 'set-operation'"><label>集合运算<select :value="node.config.operation" @change="config('operation', ($event.target as HTMLSelectElement).value)"><option value="union">并集</option><option value="intersection">交集</option><option value="difference">左差集</option></select></label><label>匹配字段（逗号分隔）<input :value="node.config.keys?.join(', ')" @change="config('keys', ($event.target as HTMLInputElement).value.split(',').map(item => item.trim()).filter(Boolean))" /></label><p class="wf-note">在输入映射中配置 left 和 right 两组数据。留空匹配字段时比较完整记录；匹配成功保留左侧记录。</p></template>
    <template v-else-if="kind === 'jsonpath-extract'"><label>记录 JSONPath<input :value="node.config.recordPath" @input="config('recordPath', ($event.target as HTMLInputElement).value)" /></label><HttpFieldSelection :key="node.id" :model-value="node.config.fields || []" :record-path="node.config.recordPath" :disabled="disabled" @update:model-value="config('fields', $event)" /></template>
    <OutputTableForm v-else-if="kind === 'output-table'" :config="node.config" :disabled="disabled" :fields="formFields" @update="$emit('update', { ...node, config: $event })" />
    <AlertPolicySelect v-else-if="kind === 'alert-evaluate'" :config="node.config" :disabled="disabled" :device="device" @update="$emit('update', { ...node, config: $event })" />
    <template v-else-if="kind === 'alert-notify'"><label>收件范围<select :value="node.config.audience" @change="config('audience', ($event.target as HTMLSelectElement).value)"><option value="executor">本次执行人</option><option value="all">工作区全部启用成员（需运维管理权限）</option></select></label><label>最低问题等级<select :value="node.config.minimumSeverity" @change="config('minimumSeverity', ($event.target as HTMLSelectElement).value)"><option value="info">提示</option><option value="warning">警告</option><option value="critical">严重</option></select></label><fieldset><legend>发送消息的报告结果</legend><label v-for="item in [['issues','发现问题'],['no_data','无数据'],['normal','正常']]" :key="item[0]" class="wf-write-consent"><input type="checkbox" :checked="node.config.outcomes?.includes(item[0])" @change="notificationOutcome(item[0], ($event.target as HTMLInputElement).checked)" />{{ item[1] }}</label></fieldset><p class="wf-note">只发送站内消息并保留上游报告对象；消息与节点结果在同一事务保存。</p></template>
    <template v-else-if="kind === 'business-report'"><label>报告标题<input :value="node.config.title" @input="config('title', ($event.target as HTMLInputElement).value)" /></label><label>业务类型<select :value="node.config.kind" @change="config('kind', ($event.target as HTMLSelectElement).value)"><option value="alert">设备检查</option><option value="quality">产品质检</option></select></label><label><input type="checkbox" :checked="node.config.aiAnalysis !== false" @change="config('aiAnalysis', ($event.target as HTMLInputElement).checked)" />AI 解读已判定结果</label><label>分析模型<select :value="node.config.modelProfile || 'reasoning'" @change="config('modelProfile', ($event.target as HTMLSelectElement).value)"><option value="reasoning">默认分析模型</option><option v-for="model in resources.models" :key="model.id" :value="model.profile_name">{{ model.name || model.profile_name }}</option></select></label><p class="wf-note">生成可读报告及逐项依据。AI 只能解读上游判定，不能改变标准或周期；相同业务结果复用分析。</p></template>
    <template v-else-if="kind === 'business-notify'"><label>收件范围<select :value="node.config.audience || 'executor'" @change="config('audience', ($event.target as HTMLSelectElement).value)"><option value="executor">本次执行人</option><option value="all">工作区启用成员（需管理权限）</option></select></label><p class="wf-note">仅通知新问题、问题变化和已确认恢复。同一问题不重复通知，本批未读取的对象不视为恢复。</p></template>
    <WorkflowAdvancedNodeForm v-else-if="advancedKinds.has(kind)" :node="node" :disabled="disabled" :fields="formFields" :catalog="catalog" :sources="sources" :resources="resources" @update="$emit('update', { ...node, config: $event })" />
    <p v-else-if="kind === 'end'" class="wf-note">在下方添加工作流要返回的输出变量，设置名称并选择上游结果。未指定时返回唯一有效上游的完整输出。</p>
    <p v-else class="wf-note">{{ kind === 'merge' ? '汇合实际执行的分支；未选中分支不会作为空结果参与。' : '保留上游输出，供业务页面或 AI 工作台读取。' }}</p>
    <template v-if="kind !== 'start'"><details v-if="valueGraph"><summary>高级变量映射</summary><p class="wf-note">默认按连线传递数据，无需配置变量路径。</p><WorkflowInputBindings :node="node" :nodes="nodes" :edges="edges" :disabled="disabled" @update="$emit('update', { ...node, inputs: $event })" /></details><WorkflowInputBindings v-else :node="node" :nodes="nodes" :edges="edges" :disabled="disabled" @update="$emit('update', { ...node, inputs: $event })" /></template>
    <details v-if="kind !== 'end'" class="wf-panel-collapse wf-output-vars"><summary><span>输出变量</span><b>{{ outputVariables.length }}</b></summary><div class="wf-panel-collapse-body"><dl><div v-for="item in outputVariables" :key="item.name"><dt><code>{{ item.name }}</code><span>{{ item.type }}</span></dt><dd>{{ item.description }}</dd></div></dl></div></details>
    <section v-if="canRetry" class="wf-node-policy-panel">
      <header><label for="wf-retry-enabled">失败时重试</label><input id="wf-retry-enabled" type="checkbox" :checked="node.retryConfig?.retryEnabled" @change="patchRetry({ retryEnabled: ($event.target as HTMLInputElement).checked })" /></header>
      <div v-if="node.retryConfig?.retryEnabled" class="wf-retry-settings">
        <label><span>最大重试次数</span><input type="range" min="1" max="10" :value="node.retryConfig.maxRetries" @input="patchRetry({ maxRetries: Number(($event.target as HTMLInputElement).value) })" /><span class="wf-number-unit"><input type="number" min="1" max="10" :value="node.retryConfig.maxRetries" @change="patchRetry({ maxRetries: Math.min(10, Math.max(1, Number(($event.target as HTMLInputElement).value))) })" />次</span></label>
        <label><span>重试间隔</span><input type="range" min="100" max="5000" step="100" :value="node.retryConfig.retryInterval" @input="patchRetry({ retryInterval: Number(($event.target as HTMLInputElement).value) })" /><span class="wf-number-unit"><input type="number" min="100" max="5000" step="100" :value="node.retryConfig.retryInterval" @change="patchRetry({ retryInterval: Math.min(5000, Math.max(100, Number(($event.target as HTMLInputElement).value))) })" />毫秒</span></label>
      </div>
    </section>
    <section v-if="canHandleError" class="wf-node-policy-panel wf-error-strategy">
      <header><span>异常处理 <i class="fa-regular fa-circle-question" title="节点最终失败时停止流程、使用默认输出或进入失败分支" /></span><select aria-label="异常处理方式" :value="node.errorStrategy || 'none'" @change="setErrorStrategy(($event.target as HTMLSelectElement).value)"><option value="none">无</option><option value="default-value">默认值</option><option value="fail-branch">失败分支</option></select></header>
      <div v-if="node.errorStrategy === 'default-value'" class="wf-default-value"><p>节点失败后以这段 JSON 作为输出，普通连线继续执行。</p><textarea v-model="defaultJson" rows="5" spellcheck="false" /><button type="button" @click="applyDefaultJson">应用默认值</button></div>
      <div v-else-if="node.errorStrategy === 'fail-branch'" class="wf-fail-branch-card"><i class="fa-solid fa-turn-down" aria-hidden="true" /><div><strong>ERROR 失败分支</strong><p>失败时输出 errorType、errorMessage 和安全引用；请在“下一步”连接处理节点。</p></div></div>
    </section>
    <section v-if="!['end', 'reply'].includes(kind)" class="wf-next-step">
      <header><strong>下一步</strong><small>添加此工作流程中的下一个节点</small></header>
      <div v-for="group in nextGroups" :key="group.port" class="wf-next-group" :class="{ 'is-error': group.port === 'error' }">
        <span v-if="group.label" class="wf-next-branch">{{ group.label }}</span>
        <div class="wf-next-line" aria-hidden="true"><i /></div>
        <div class="wf-next-items"><article v-for="item in group.nodes" :key="item.id"><i class="fa-solid" :class="nodeIcons[item.kind] || 'fa-cube'" /><span>{{ item.title }}</span></article><button type="button" :aria-label="group.port === 'error' ? '添加失败处理节点' : '添加并行节点'" @click="addNext(group.port, $event)"><i class="fa-solid fa-plus" />{{ group.nodes.length ? '添加并行节点' : group.port === 'error' ? '添加失败处理节点' : '添加下一节点' }}</button></div>
      </div>
    </section>
    <details v-if="constraints.length" class="wf-contract-details"><summary>属性约束 · {{ requiredConstraintCount }} 项必填</summary><dl><div v-for="item in constraints" :key="item.name"><dt><code>{{ item.name }}</code><b v-if="item.required">必填</b></dt><dd>{{ item.description || '节点运行配置' }}<small v-if="item.rule">{{ item.rule }}</small></dd></div></dl></details>
    <details><summary>高级配置 JSON</summary><label>节点完整配置<textarea v-model="json" rows="7" spellcheck="false" /></label><button @click="applyJson">应用配置 JSON</button><p class="wf-note">必须符合节点契约；发布前会再次校验，不执行任意 Python 代码。</p></details>
        <p v-if="error" role="alert" class="wf-error">{{ error }}</p><button v-if="kind !== 'start'" class="wf-danger" @click="$emit('remove', node.id)">移除节点及相关连线</button>
      </fieldset>
    </section>
    <section v-else class="wf-last-run" aria-live="polite">
      <template v-if="lastRun">
        <div class="wf-last-run-state" :class="`is-${lastRunStatus}`"><i class="fa-solid" :class="lastRunStatus === 'completed' ? 'fa-circle-check' : lastRunStatus === 'failed' ? 'fa-circle-exclamation' : lastRunStatus === 'running' ? 'fa-spinner' : 'fa-circle-info'" aria-hidden="true" /><div><strong>{{ lastRunStatusLabel }}</strong><small v-if="lastRun.durationMs !== undefined">{{ lastRun.durationMs }} ms</small></div></div>
        <section v-if="lastRun.message" class="wf-last-run-message"><h3>信息</h3><p>{{ lastRun.message }}</p></section>
        <section v-if="lastRun.summary"><h3>结果摘要</h3><p>{{ lastRun.summary }}</p></section>
        <section v-if="lastRun.output !== undefined"><h3>输出</h3><template v-if="valueGraph && lastRun.output && (Array.isArray(lastRun.output.data) || lastRun.output.rowBranches)"><WorkflowValuesPreview :output="lastRun.output" /><details><summary>查看原始数据</summary><pre>{{ pretty(lastRun.output) }}</pre></details></template><pre v-else>{{ pretty(lastRun.output) }}</pre></section>
      </template>
      <div v-else class="wf-last-run-empty"><i class="fa-regular fa-clock" aria-hidden="true" /><strong>尚无运行结果</strong><p>运行工作流后，可在这里查看此节点的状态、耗时和输出。</p><button type="button" class="wf-primary" :disabled="disabled" @click="$emit('run')"><i class="fa-solid fa-play" aria-hidden="true" />运行工作流</button></div>
    </section>
  </aside>
</template>
