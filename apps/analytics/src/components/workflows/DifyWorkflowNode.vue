<script setup lang="ts">
/**
 * DifyWorkflowNode —— 画布上的节点卡片（Dify 风格），由 WorkflowCanvas 以 `node-workflow` 模板注入。
 *
 * 依赖接口：`@vue-flow/core` 的 Handle/NodeProps；`api/workflows` 的 flowStatus（运行状态中文）、
 * FlowNode / FlowSide / FlowPort 类型；数据由 WorkflowCanvas 在 data 中组装（node/status/issues/disabled/
 * connectedSources/connectedTargets）。
 * 关键约束：
 * - kindNames / iconNames 是前端展示映射，新增节点类型必须同时补两处，否则会退化成 kind 原文与默认立方体图标；
 * - 端口可见性由节点语义决定：start 无输入、end/reply 无输出；condition 只暴露 true/false，
 *   classifier 暴露 out + 各 case（含 fallback 兜底），error 端口仅在 errorStrategy === 'fail-branch' 时出现，
 *   这些约束必须与 WorkflowCanvas.connectionAllowed 的校验保持一致；
 * - 该节点只派发事件（select/nudge/palette/duplicate/remove/inspectRun），不修改图数据；
 * - bodyRows 是"配置摘要"，用于在不打开表单的情况下让用户判断节点是否已配置完整。
 */
import { computed } from 'vue'
import { Handle, Position, useVueFlow, type HandleConnectable, type NodeProps } from '@vue-flow/core'
import { flowStatus, type FlowNode, type FlowSide, type FlowPort } from '../../api/workflows'

export interface WorkflowNodeData {
  node: FlowNode
  status?: string
  issues: string[]
  disabled: boolean
  connectedSources: string[]
  connectedTargets: string[]
}

type PaletteRequest = {
  nodeId: string
  direction: 'before' | 'after'
  side: FlowSide
  port: FlowPort
  clientX: number
  clientY: number
}

const props = defineProps<NodeProps<WorkflowNodeData>>()
const { viewport } = useVueFlow()
// 画布缩小时保持操作栏的屏幕点击尺寸；只补偿操作栏，不改变节点与连线的布局坐标。
const toolStyle = computed(() => ({ transform: `scale(${Math.max(1, 1 / Math.max(viewport.value.zoom, 0.01))})` }))
const emit = defineEmits<{
  select: [id: string]
  nudge: [value: { id: string; x: number; y: number }]
  palette: [value: PaletteRequest]
  duplicate: [id: string]
  remove: [id: string]
  inspectRun: [id: string]
}>()

/** 四个方向句柄的静态定义，顺序即渲染顺序；side 值必须与 WorkflowCanvas 的 side/port 编码一致。 */
const sides: { side: FlowSide; position: Position }[] = [
  { side: 'top', position: Position.Top },
  { side: 'right', position: Position.Right },
  { side: 'bottom', position: Position.Bottom },
  { side: 'left', position: Position.Left },
]
const kindNames: Record<string, string> = {
  'read-values': '读取值', 'calculate-values': '计算值', 'output-values': '输出结果',
  'document-extractor': '文档提取器', 'list-operator': '列表操作',
  start: '开始', end: '结束', reply: '回复输出', merge: '结果汇合',
  'http-request': 'HTTP 请求', 'database-query': '数据库查询', 'report-analysis': 'AI 数据分析',
  'jsonpath-extract': '参数提取器', arithmetic: '计算字段', filter: '记录筛选', condition: '条件分支',
  'set-operation': '交并差', 'rule-evaluate': '逐项规则判定', 'alert-evaluate': '设备预警',
  'alert-notify': '预警通知', 'output-table': '目标表写入', 'qc-appearance': '外观缺陷识别',
  'qc-component': '成分检测', 'qc-evaluate': '质检判定', llm: '大模型', agent: '智能体',
  knowledge: '知识检索', question: '提问', classifier: '问题分类器', extractor: '参数提取器', loop: '循环',
  iterator: '迭代', while: '条件循环', parallel: '并行分支', aggregator: '变量聚合器', delay: '延迟',
  subflow: '子流程', setvar: '变量赋值', text: '文本处理', template: '模板转换', code: '代码执行', tool: '工具调用',
}
const iconNames: Record<string, string> = {
  'read-values': 'fa-database', 'calculate-values': 'fa-calculator', 'output-values': 'fa-file-export',
  'document-extractor': 'fa-file-lines', 'list-operator': 'fa-list-ol',
  start: 'fa-play', end: 'fa-flag-checkered', reply: 'fa-reply', merge: 'fa-code-merge',
  'http-request': 'fa-cloud-arrow-down', 'database-query': 'fa-database', 'report-analysis': 'fa-chart-line',
  'jsonpath-extract': 'fa-filter-circle-dollar', arithmetic: 'fa-calculator', filter: 'fa-filter', condition: 'fa-code-branch',
  'set-operation': 'fa-object-group', 'rule-evaluate': 'fa-list-check', 'alert-evaluate': 'fa-triangle-exclamation',
  'alert-notify': 'fa-bell', 'output-table': 'fa-table', 'qc-appearance': 'fa-eye', 'qc-component': 'fa-flask',
  'qc-evaluate': 'fa-clipboard-check', llm: 'fa-wand-magic-sparkles', agent: 'fa-robot', knowledge: 'fa-book-open',
  question: 'fa-circle-question', classifier: 'fa-tags', extractor: 'fa-file-circle-check', loop: 'fa-rotate',
  iterator: 'fa-list-ol', while: 'fa-repeat', parallel: 'fa-code-fork', aggregator: 'fa-layer-group', delay: 'fa-clock',
  subflow: 'fa-diagram-project', setvar: 'fa-sliders', text: 'fa-font', template: 'fa-file-code', code: 'fa-terminal', tool: 'fa-screwdriver-wrench',
}
const aiKinds = new Set(['llm', 'agent', 'knowledge', 'question', 'classifier', 'extractor'])
const businessKinds = new Set(['alert-evaluate', 'alert-notify', 'output-table', 'qc-appearance', 'qc-component', 'qc-evaluate', 'rule-evaluate'])
const controlKinds = new Set(['condition', 'merge', 'loop', 'iterator', 'while', 'parallel', 'delay', 'subflow'])
const terminalKinds = new Set(['end', 'reply'])

const node = computed(() => props.data.node)
const category = computed(() => node.value.kind === 'start' || terminalKinds.has(node.value.kind)
  ? 'basic' : aiKinds.has(node.value.kind) ? 'ai' : businessKinds.has(node.value.kind) ? 'business' : controlKinds.has(node.value.kind) ? 'control' : 'data')
const icon = computed(() => iconNames[node.value.kind] || 'fa-cube')
const kindName = computed(() => kindNames[node.value.kind] || node.value.kind)
const statusName = computed(() => props.data.status ? flowStatus[props.data.status] || props.data.status : '')
const isCondition = computed(() => node.value.kind === 'condition')
const hasErrorBranch = computed(() => node.value.errorStrategy === 'fail-branch')
const hasTarget = computed(() => node.value.kind !== 'start')
const hasSource = computed(() => !terminalKinds.has(node.value.kind))
const isRunning = computed(() => props.data.status === 'running')
const isSuccess = computed(() => props.data.status === 'completed')
const isFailed = computed(() => props.data.status === 'failed')
const isWaiting = computed(() => ['pending', 'waiting', 'cancel_requested'].includes(props.data.status || ''))
/**
 * 节点卡片正文摘要：按 kind 抽取最能说明"是否已配置"的字段。
 * 未配置时给引导文案（如"选择数据表与字段"），已配置时给实际值；文本类统一压缩空白并截断，
 * 避免长 SQL / 长模板把卡片撑破。新增节点类型若不在此补充，会回退为节点名称。
 */
const bodyRows = computed(() => {
  const config = node.value.config || {}
  const fallback = kindName.value
  if (node.value.kind === 'start') return [`${config.variables?.length || 0} 个输入字段`]
  if (node.value.kind === 'end') return ['输出工作流结果']
  if (node.value.kind === 'reply') return ['返回调用结果']
  if (node.value.kind === 'read-values') return [config.tableName || config.templateId ? `读取 ${config.fields?.length || (config.tableName ? 0 : '全部')} 个字段` : '选择数据表与字段', ...(config.tableName ? [config.tableName] : [])]
  if (node.value.kind === 'calculate-values') return (config.calculations || []).slice(0, 2).map((c: any) => c.name || '选择计算方式')
  if (node.value.kind === 'output-values') return [({ normal: config.mode === 'quality' ? '合格' : '正常', warning: config.mode === 'quality' ? '不合格' : '预警', pending: '待确认' } as Record<string, string>)[config.conclusion] || '选择输出结果', config.message || '填写结果说明']
  if (node.value.kind === 'http-request') return [config.datasourceId ? '已选择 HTTP 数据源' : '选择 HTTP 数据源']
  if (node.value.kind === 'database-query') return [config.query ? String(config.query).replace(/\s+/g, ' ').slice(0, 54) : '配置只读 SQL']
  if (node.value.kind === 'jsonpath-extract') return [config.recordPath || '$.data', `${config.fields?.length || 0} 个提取字段`]
  if (node.value.kind === 'arithmetic') return [`${config.fields?.length || 0} 个计算字段`]
  if (node.value.kind === 'condition') return ['按条件拆分执行路径']
  if (node.value.kind === 'merge') return ['汇合已执行的分支']
  if (node.value.kind === 'alert-evaluate') return [config.ruleId ? `预警策略 · v${config.ruleRevision || 1}` : '选择预警策略']
  if (node.value.kind === 'alert-notify') return [config.audience === 'all' ? '通知工作区成员' : '通知本次执行人']
  if (node.value.kind === 'qc-evaluate') return [config.templateId ? `检验模板 · v${config.templateRevision || 1}` : '选择检验模板']
  if (node.value.kind === 'qc-appearance' || node.value.kind === 'qc-component') return [config.metricId ? `检测指标 · v${config.metricVersion || 1}` : '选择检测指标']
  if (node.value.kind === 'llm' || node.value.kind === 'extractor') return [config.modelProfile ? `模型 · ${config.modelProfile}` : '选择模型']
  if (node.value.kind === 'knowledge') return [`${config.knowledgeBaseIds?.length || 0} 个知识库`]
  if (node.value.kind === 'template') return [String(config.template || '{{ input }}').replace(/\s+/g, ' ').slice(0, 54)]
  if (node.value.kind === 'output-table') return [config.targetId ? '已选择写入目标' : '选择写入目标']
  if (node.value.kind === 'delay') return [`等待 ${config.seconds || 0} 秒`]
  return [fallback]
})

function axisStyle(side: FlowSide, percent: number) {
  return side === 'top' || side === 'bottom' ? { left: `${percent}%` } : { top: `${percent}%` }
}
function targetOffset() {
  if (!hasSource.value) return 50
  return isCondition.value ? 24 : hasErrorBranch.value ? 26 : 34
}
const categoryPorts = computed(() => node.value.kind === 'classifier' ? [
  ...(node.value.config.cases || []).map((item: any) => ({ port: `case:${item.id}` as FlowPort, label: item.label || item.id })),
  { port: `case:${node.value.config.fallback || 'other'}` as FlowPort, label: '未匹配' },
] : [])
function sourceOffsets(): { port: FlowPort; offset: number; label: string }[] {
  if (categoryPorts.value.length) {
    const legacy = props.data.connectedSources.some(handle => handle.startsWith('source-out-'))
      ? [{ port: 'out' as FlowPort, label: '通用输出（旧配置）' }] : []
    const ports = [...categoryPorts.value, ...legacy]
    return ports.map((item, index) => ({ ...item, offset: (65 + index * 26) / (64 + ports.length * 26) * 100 }))
  }
  if (!isCondition.value) return [
    { port: 'out' as const, offset: hasErrorBranch.value ? 52 : hasTarget.value ? 66 : 50, label: '输出' },
    ...(hasErrorBranch.value ? [{ port: 'error' as const, offset: 78, label: '失败分支' }] : []),
  ]
  return [
    { port: 'true' as const, offset: 58, label: '满足' },
    { port: 'false' as const, offset: 80, label: '不满足' },
  ]
}
function sourceId(port: FlowPort, side: FlowSide) {
  return `source-${port}-${side}`
}
function targetId(side: FlowSide) {
  return `target-${side}`
}
function connected(type: 'source' | 'target', handle: string) {
  return (type === 'source' ? props.data.connectedSources : props.data.connectedTargets).includes(handle)
}
function requestPalette(event: MouseEvent, direction: 'before' | 'after', side: FlowSide, port: FlowPort) {
  if (props.data.disabled || (direction === 'before' && connected('target', targetId(side)))) return
  event.stopPropagation()
  emit('palette', { nodeId: props.id, direction, side, port, clientX: event.clientX, clientY: event.clientY })
}
function onKeydown(event: KeyboardEvent) {
  // 节点自身处理移动与选择；子按钮、菜单 summary 保留原生 Enter/空格行为。
  if (event.target !== event.currentTarget) return
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    emit('select', props.id)
    return
  }
  if (!event.key.startsWith('Arrow') || props.data.disabled) return
  event.preventDefault()
  event.stopPropagation()
  const step = event.shiftKey ? 5 : 20
  emit('nudge', {
    id: props.id,
    x: event.key === 'ArrowRight' ? step : event.key === 'ArrowLeft' ? -step : 0,
    y: event.key === 'ArrowDown' ? step : event.key === 'ArrowUp' ? -step : 0,
  })
}
</script>

<template>
  <article
    class="wf-node"
    :class="[`wf-node-${category}`, { selected, invalid: data.issues.length, running: isRunning, succeeded: isSuccess, failed: isFailed, waiting: isWaiting }]"
    :data-node-id="id"
    :aria-label="`${node.title}，${kindName}`"
    :aria-invalid="data.issues.length ? 'true' : undefined"
    :aria-description="`${selected ? '已选择。' : ''}按 Enter 或空格打开节点配置。${data.disabled ? '' : '使用方向键移动节点，按住 Shift 可微调。'}`"
    role="group"
    tabindex="0"
    @keydown="onKeydown"
  >
    <div class="wf-node-card" :style="categoryPorts.length ? { minHeight: `${64 + sourceOffsets().length * 26}px` } : undefined">
      <header class="wf-node-heading">
        <i class="fa-solid wf-node-icon" :class="icon" aria-hidden="true" />
        <div>
          <span :title="node.title">{{ node.title }}</span>
          <small>{{ kindName }}</small>
        </div>
        <i v-if="isRunning" class="fa-solid fa-spinner wf-node-status-icon running" aria-label="执行中" />
        <i v-else-if="isFailed" class="fa-solid fa-circle-exclamation wf-node-status-icon failed" :title="statusName" />
        <i v-else-if="isWaiting" class="fa-solid fa-circle-pause wf-node-status-icon waiting" :title="statusName" />
        <i v-else-if="isSuccess" class="fa-solid fa-circle-check wf-node-status-icon succeeded" :title="statusName" />
        <i v-else-if="data.issues.length" class="fa-solid fa-triangle-exclamation wf-node-status-icon invalid" :title="data.issues[0]" />
      </header>
      <div class="wf-node-body">
        <template v-if="!categoryPorts.length"><div v-for="(row, index) in bodyRows" :key="index" :title="row"><i v-if="index" class="fa-solid fa-angle-right" aria-hidden="true" />{{ row }}</div></template>
        <div v-if="isCondition" class="wf-node-branches"><span class="wf-branch true">满足</span><span class="wf-branch false">不满足</span></div>
        <template v-if="categoryPorts.length"><div v-for="category in sourceOffsets()" :key="category.port" class="wf-node-category" :title="category.label" style="height:23px;line-height:23px;text-align:right">{{ category.label }}</div></template>
        <div v-if="node.retryConfig?.retryEnabled" class="wf-node-policy"><i class="fa-solid fa-rotate-right" aria-hidden="true" />失败时重试 {{ node.retryConfig.maxRetries }} 次</div>
        <div v-if="node.errorStrategy === 'default-value'" class="wf-node-policy"><i class="fa-solid fa-shield" aria-hidden="true" />失败时使用默认值</div>
        <div v-else-if="node.errorStrategy === 'fail-branch'" class="wf-node-policy is-error"><i class="fa-solid fa-turn-down" aria-hidden="true" />失败分支</div>
      </div>
    </div>

    <div v-if="!data.disabled && !isRunning" class="wf-node-tools nodrag nopan nowheel" :style="toolStyle" @mousedown.stop @click.stop>
      <button type="button" title="运行此节点" aria-label="运行此节点" @click="emit('inspectRun', id)"><i class="fa-solid fa-play" aria-hidden="true" /></button>
      <details class="wf-node-more">
        <summary aria-label="节点操作" title="节点操作"><i class="fa-solid fa-ellipsis" aria-hidden="true" /></summary>
        <div class="wf-node-menu">
          <button v-if="node.kind !== 'start'" type="button" @click="emit('duplicate', id)"><i class="fa-regular fa-copy" aria-hidden="true" />复制</button>
          <button v-if="node.kind !== 'start'" type="button" class="danger" @click="emit('remove', id)"><i class="fa-regular fa-trash-can" aria-hidden="true" />删除</button>
        </div>
      </details>
    </div>

    <template v-for="item in sides" :key="item.side">
      <Handle
        v-if="hasTarget"
        :id="targetId(item.side)"
        type="target"
        :position="item.position"
        :connectable="connectable as HandleConnectable"
        :connectable-start="true"
        :connectable-end="true"
        class="wf-node-handle wf-node-handle-target"
        :class="[`wf-node-handle-${item.side}`, { connected: connected('target', targetId(item.side)) }]"
        :style="axisStyle(item.side, targetOffset())"
        :title="`从${item.side === 'top' ? '上' : item.side === 'right' ? '右' : item.side === 'bottom' ? '下' : '左'}侧接入`"
        @click="requestPalette($event, 'before', item.side, 'out')"
      ><span v-if="!connected('target', targetId(item.side))" class="wf-handle-plus" aria-hidden="true">+</span></Handle>
      <Handle
        v-for="source in hasSource ? sourceOffsets() : []"
        :key="`${item.side}-${source.port}`"
        :id="sourceId(source.port, item.side)"
        type="source"
        :position="item.position"
        :connectable="connectable as HandleConnectable"
        :connectable-start="true"
        :connectable-end="true"
        class="wf-node-handle wf-node-handle-source"
        :class="[`wf-node-handle-${item.side}`, `wf-node-handle-${source.port}`, { connected: connected('source', sourceId(source.port, item.side)) }]"
        :style="axisStyle(item.side, source.offset)"
        :title="`${source.label} · 从${item.side === 'top' ? '上' : item.side === 'right' ? '右' : item.side === 'bottom' ? '下' : '左'}侧连接`"
        @click="requestPalette($event, 'after', item.side, source.port)"
      ><span v-if="!connected('source', sourceId(source.port, item.side))" class="wf-handle-plus" aria-hidden="true">+</span></Handle>
    </template>
  </article>
</template>
