<script setup lang="ts">
/**
 * WorkflowEditor —— 工作流编排主体：画布 + 节点面板 + 插入节点浮层。
 *
 * 所在路由：`/workflows/:id`（WorkflowWorkspace → WorkflowWorkbench，section === 'workflow'），
 * 设备侧由 `/alerts|/quality/devices/:deviceId/workflow` 进入并带 domain/device 上下文。
 * 依赖接口：`api/workflows` 的 Workflow / FlowNode / FlowGraph / NodeKind / FlowRun，
 * `utils/workflowLayout.arrangeWorkflowGraph`（自动排布）、`utils/workflowGraphEditing.buildInsertionEdges`
 * （在已有连线上插入节点时的断线重连）、`utils/randomId.createClientId`（前端临时 id）。
 * 关键约束：
 * - 本组件不直接落库：所有图/节点/元信息修改都以事件上抛，由 useWorkflows 统一防抖自动保存，
 *   因此这里必须用不可变更新（copy 后替换），不得就地修改 props.flow；
 * - `editable === false`（无 workflows.edit 权限或只读访问）时必须整体禁用编辑入口：
 *   打开节点面板、自动排布、拖拽改位置都会被拦截，只允许查看与选中；
 * - 设备上下文下新建的 read-values / 判定类节点必须自动带上 deviceId，避免用户在设备入口里
 *   再手选设备导致配置与上下文不一致；
 * - 插入浮层位置按点击坐标计算并夹取在画布可视区域内，超界时回退到左上角默认位置。
 */
import { computed, shallowRef, useTemplateRef } from 'vue'
import { copy, domainNames, newNode, type Workflow, type FlowNode, type FlowGraph, type FlowSide, type FlowPort, type NodeKind, type FlowRun } from '../../api/workflows'
import { createClientId } from '../../utils/randomId'
import WorkflowCanvas from './WorkflowCanvas.vue'
import WorkflowNodeForm from './WorkflowNodeForm.vue'
import WorkflowNodePalette from './WorkflowNodePalette.vue'
import WorkflowVariableInspect from './WorkflowVariableInspect.vue'
import { arrangeWorkflowGraph } from '../../utils/workflowLayout'
import { buildInsertionEdges } from '../../utils/workflowGraphEditing.js'

const props = defineProps<{
  flow: Workflow
  catalog: NodeKind[]
  sources: any[]
  resources: { knowledgeBases: any[]; bots: any[]; tools: any[]; workflows: Workflow[]; models: any[] }
  editable: boolean
  selected: string
  fields: string[]
  run: FlowRun | null
  canUndo: boolean
  canRedo: boolean
  domain?: 'alert' | 'quality'
  device?: Record<string, any>
  immersive?: boolean
}>()
const emit = defineEmits<{
  graph: [graph: FlowGraph]
  undo: []
  redo: []
  node: [node: FlowNode]
  meta: [value: Partial<Workflow>]
  select: [id: string]
  removeNode: [id: string]
  deleteNode: [id: string]
  sourceSaved: [source: any]
  run: []
}>()
type PaletteRequest = {
  edgeId?: string
  nodeId?: string
  direction?: 'before' | 'after'
  side?: FlowSide
  port?: FlowPort
  clientX?: number
  clientY?: number
}
const palette = shallowRef(false)
const paletteError = shallowRef('')
const insertEdgeId = shallowRef('')
const paletteRequest = shallowRef<PaletteRequest>({})
const palettePosition = shallowRef({ left: '58px', top: '12px' })
const canvasStack = useTemplateRef<HTMLElement>('canvasStack')
const panelTab = shallowRef<'settings' | 'lastRun'>('settings')
const selectedNode = computed(() => props.flow.graph.nodes.find(node => node.id === props.selected))
const selectedContract = computed(() => props.catalog.find(item => item.kind === selectedNode.value?.kind))
/** 校验错误按 nodeId 归组，供画布给对应节点打标；无 nodeId 的结构级问题不在此展示。 */
const validationErrors = computed<Record<string, string[]>>(() => {
  const grouped: Record<string, string[]> = {}
  for (const issue of props.flow.validation?.errors || []) {
    if (!issue.nodeId) continue
    if (!grouped[issue.nodeId]) grouped[issue.nodeId] = []
    grouped[issue.nodeId].push(issue.message)
  }
  return grouped
})
const selectedErrors = computed(() => validationErrors.value[props.selected] || [])
const paletteCatalog = computed(() => props.catalog)
const paletteScope = computed(() => '')
function opposite(side: FlowSide): FlowSide {
  return side === 'left' ? 'right' : side === 'right' ? 'left' : side === 'top' ? 'bottom' : 'top'
}
/**
 * 打开节点选择浮层。请求携带来源（连线 edgeId 或节点 before/after + 侧边），
 * 决定新增节点落位与连线方式；只读模式直接返回，不产生任何 UI 反馈。
 */
function openPalette(request: PaletteRequest = {}) {
  if (!props.editable) return
  if (!request?.nodeId && !request?.edgeId) emit('select', '')
  paletteRequest.value = request || {}
  paletteError.value = ''
  insertEdgeId.value = request?.edgeId || ''
  const bounds = canvasStack.value?.getBoundingClientRect()
  if (bounds && request?.clientX !== undefined && request?.clientY !== undefined) {
    const width = Math.min(400, Math.max(280, bounds.width - 16))
    const desiredLeft = request.clientX - bounds.left + 50
    const desiredTop = request.clientY - bounds.top - 80
    palettePosition.value = {
      left: `${Math.max(8, Math.min(desiredLeft, bounds.width - width - 8))}px`,
      top: `${Math.max(8, Math.min(desiredTop, bounds.height - 260))}px`,
    }
  } else {
    palettePosition.value = { left: '58px', top: '12px' }
  }
  palette.value = true
}
function closePalette() {
  palette.value = false
  paletteError.value = ''
  insertEdgeId.value = ''
  paletteRequest.value = {}
}
function sideVector(side: FlowSide) {
  if (side === 'left') return { x: -1, y: 0 }
  if (side === 'right') return { x: 1, y: 0 }
  if (side === 'top') return { x: 0, y: -1 }
  return { x: 0, y: 1 }
}
/**
 * 批量新增节点并接回原图。
 * 定位优先级：连线上插入（edgeId）> 指定节点的 after/before 侧边 > 当前选中节点右侧；
 * 连线上插入由 buildInsertionEdges 负责断开原边并重连，返回 error 时只提示不落图。
 * 副作用：派发 graph（整图替换）与 select（选中第一个新节点），并关闭浮层。
 */
function addMany(kinds: NodeKind[]) {
  if (!kinds.length) return
  const graph = props.flow.graph
  const edge = insertEdgeId.value ? graph.edges.find(item => item.id === insertEdgeId.value) : undefined
  const request = paletteRequest.value
  const source = edge
    ? graph.nodes.find(item => item.id === edge.source)
    : request.direction === 'after' ? graph.nodes.find(item => item.id === request.nodeId) : undefined
  const target = edge
    ? graph.nodes.find(item => item.id === edge.target)
    : request.direction === 'before' ? graph.nodes.find(item => item.id === request.nodeId) : undefined
  const fallbackAnchor = !request.direction && !edge ? graph.nodes.find(item => item.id === props.selected) : undefined
  const nodes = kinds.map((kind, index) => {
    const node = newNode(kind.kind, kind.title, graph.nodes.length + index, kind.defaultConfig)
    if (node.kind === 'condition' && graph.nodes.some(n => n.kind === 'read-values')) node.config.mode = 'rows'
    if (node.kind === 'read-values' && props.device?.id) node.config = { ...node.config, deviceId: props.device.id, datasourceId: props.device.datasourceId || '' }
    if (props.device?.id && ['alert-evaluate', 'qc-appearance', 'qc-component', 'qc-evaluate'].includes(node.kind))
      node.config = { ...node.config, deviceId: props.device.id }
    return node
  })
  if (edge && source && target) {
    nodes.forEach((node, index) => {
      node.position = { x: source.position.x + (index + 1) * 300, y: source.position.y + (nodes.length > 1 ? 180 : 0) }
    })
  } else if (source) {
    const side = request.side || 'right'
    const vector = sideVector(side)
    nodes.forEach((node, index) => {
      node.position = { x: source.position.x + vector.x * 300 * (index + 1), y: source.position.y + vector.y * 180 * (index + 1) }
    })
  } else if (target) {
    const side = opposite(request.side || 'left')
    const vector = sideVector(side)
    nodes.forEach((node, index) => {
      const distance = nodes.length - index
      node.position = { x: target.position.x - vector.x * 300 * distance, y: target.position.y - vector.y * 180 * distance }
    })
  } else if (fallbackAnchor) {
    nodes.forEach((node, index) => { node.position = { x: fallbackAnchor.position.x + (index + 1) * 300, y: fallbackAnchor.position.y } })
  }
  const insertion = buildInsertionEdges(graph, request, nodes, () => `edge_${createClientId().replaceAll('-', '')}`)
  if (insertion.error) {
    paletteError.value = insertion.error
    return
  }
  const first = nodes[0]!
  emit('graph', { ...graph, nodes: [...graph.nodes, ...nodes], edges: insertion.edges })
  emit('select', first.id)
  closePalette()
}
function add(kind: NodeKind) { addMany([kind]) }
function addSnippet(kinds: NodeKind[]) { addMany(kinds) }
function updateBoundNode(node: FlowNode) {
  if (props.device?.id && ['alert-evaluate', 'qc-appearance', 'qc-component', 'qc-evaluate'].includes(node.kind))
    node = { ...node, config: { ...node.config, deviceId: props.device.id } }
  emit('node', node)
}
/**
 * 切换节点的错误分支策略。取消 fail-branch 时必须同时清理该节点已存在的 error 端口连线，
 * 否则会留下指向不存在端口的悬挂边（后端校验与画布渲染都会报错）。
 */
function updateErrorStrategy(node: FlowNode) {
  const graph = copy(props.flow.graph)
  graph.nodes = graph.nodes.map(item => item.id === node.id ? node : item)
  if (node.errorStrategy !== 'fail-branch') graph.edges = graph.edges.filter(edge => edge.source !== node.id || edge.port !== 'error')
  emit('graph', graph)
}
function selectNode(id: string) {
  panelTab.value = 'settings'
  emit('select', id)
}
function inspectRun(id: string) {
  panelTab.value = 'lastRun'
  emit('select', id)
}
function toggleDomain(name: string, checked: boolean) {
  const values = checked ? [...props.flow.domains, name] : props.flow.domains.filter(value => value !== name)
  emit('meta', { domains: values })
}
/** 自动排布只改坐标不改结构：先深拷贝再交给纯函数，避免污染 props.flow.graph。 */
function arrange() {
  emit('graph', arrangeWorkflowGraph(copy(props.flow.graph)))
}
</script>

<template>
  <section class="wf-editor">
    <fieldset :disabled="!editable" class="wf-meta">
      <label>流程名称<input :value="flow.name" maxlength="200" required @input="$emit('meta', { name: ($event.target as HTMLInputElement).value })" /></label>
      <details class="wf-meta-details">
        <summary>用途与适用业务</summary>
        <label>用途说明<input :value="flow.description" maxlength="2000" @input="$emit('meta', { description: ($event.target as HTMLInputElement).value })" /></label>
        <div class="wf-domains"><span>适用业务</span><label v-for="(name, key) in domainNames" :key="key"><input type="checkbox" :checked="flow.domains.includes(key)" @change="toggleDomain(key, ($event.target as HTMLInputElement).checked)" />{{ name }}</label></div>
      </details>
    </fieldset>
    <div class="wf-editor-layout" :class="{ 'panel-open': selectedNode }">
      <div ref="canvasStack" class="wf-canvas-stack">
        <WorkflowCanvas
          :graph="flow.graph"
          :selected="selected"
          :disabled="!editable"
          :statuses="run?.result?.nodes"
          :validation-errors="validationErrors"
          :can-undo="canUndo"
          :can-redo="canRedo"
          @select="selectNode"
          @update="$emit('graph', $event)"
          @add="openPalette($event)"
          @insert="openPalette($event)"
          @palette="openPalette"
          @arrange="arrange"
          @undo="$emit('undo')"
          @redo="$emit('redo')"
          @delete-node="$emit('deleteNode', $event)"
          @inspect-run="inspectRun"
        />
        <div v-if="palette" class="wf-palette-layer" @mousedown.self="closePalette">
          <p v-if="paletteError" class="wf-palette-error" role="alert">{{ paletteError }}</p>
          <div class="wf-palette-dialog" :style="palettePosition" role="dialog" aria-modal="false" aria-label="选择工作流节点">
            <WorkflowNodePalette :catalog="paletteCatalog" :disabled="!editable" :scope-label="paletteScope" :domain="domain" @add="add" @add-snippet="addSnippet" @close="closePalette" />
          </div>
        </div>
      </div>
      <WorkflowNodeForm
        v-if="selectedNode"
        :key="selectedNode.id"
        :node="selectedNode"
        :nodes="flow.graph.nodes"
        :edges="flow.graph.edges"
        :sources="sources"
        :resources="resources"
        :catalog="catalog"
        :disabled="!editable"
        :fields="fields"
        :contract="selectedContract"
        :errors="selectedErrors"
        :validation-known="!!flow.validation"
        :initial-tab="panelTab"
        :last-run="run?.result?.nodes?.[selectedNode.id]"
        :device="device"
        @update="updateBoundNode"
        @remove="$emit('removeNode', $event)"
        @close="$emit('select', '')"
        @run="$emit('run')"
        @source-saved="$emit('sourceSaved', $event)"
        @add-next="openPalette($event)"
        @error-strategy="updateErrorStrategy"
      />
    </div>
    <details v-if="flow.validation && !immersive" class="wf-validation" :class="{ 'wf-error': !flow.validation.valid }" aria-live="polite">
      <summary><i class="fa-solid" :class="flow.validation.valid ? 'fa-circle-check' : 'fa-triangle-exclamation'" aria-hidden="true" /><span>{{ flow.validation.valid ? '配置检查通过' : `${flow.validation.errors.length} 项配置待处理` }}</span><i class="fa-solid fa-chevron-up wf-validation-chevron" aria-hidden="true" /></summary>
      <div class="wf-validation-detail"><strong>{{ flow.validation.valid ? '结构检查通过；数据源和外部服务将在运行时再次验证。' : '请修正以下配置后再发布或运行：' }}</strong>
        <ul v-if="!flow.validation.valid"><li v-for="(item, index) in flow.validation.errors" :key="index"><button v-if="item.nodeId" class="wf-link" @click="$emit('select', item.nodeId)">{{ flow.graph.nodes.find(node => node.id === item.nodeId)?.title }}：</button>{{ item.message }}</li></ul>
      </div>
    </details>
    <WorkflowVariableInspect v-if="immersive" :run="run" :nodes="flow.graph.nodes" />
  </section>
</template>
