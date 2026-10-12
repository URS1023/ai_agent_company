<script setup lang="ts">
/**
 * WorkflowSubgraphEditor —— 子流程节点的内嵌编排弹窗（在子流程节点上编辑其内部图，不跳转页面）。
 *
 * 位置：WorkflowAdvancedNodeForm 中 kind === 'subflow' 时打开（异步加载）。
 * 依赖接口：`api/workflows`（newNode / FlowGraph / NodeKind / Workflow）、`utils/randomId.createClientId`、
 * `utils/workflowLayout.arrangeWorkflowGraph`、`utils/workflowGraphEditing.buildInsertionEdges`、
 * WorkflowCanvas / WorkflowNodeForm / WorkflowNodePalette（与主画布同一套组件）。
 * 关键约束：
 * - 编辑的是本地副本：只有点击保存才以 save 事件提交整图，取消则丢弃；因此内部维护 past/future 撤销栈
 *   与主画布的全局撤销互不影响；
 * - 空图默认给出 start → end 的最小可用结构，避免用户面对完全空白的画布；
 * - 目录中不存在的节点类型标记为不可执行契约（unsupportedNodes），画布上直接报错而不是静默保留；
 * - disabled 时 commit 直接返回，保证只读模式下连本地状态也不变。
 */
import { computed, ref } from 'vue'
import { copy, newNode, type FlowEdge, type FlowGraph, type FlowNode, type FlowSide, type NodeKind, type Workflow } from '../../api/workflows'
import { createClientId } from '../../utils/randomId'
import { arrangeWorkflowGraph } from '../../utils/workflowLayout'
import WorkflowCanvas from './WorkflowCanvas.vue'
import WorkflowNodeForm from './WorkflowNodeForm.vue'
import WorkflowNodePalette from './WorkflowNodePalette.vue'
import { buildInsertionEdges } from '../../utils/workflowGraphEditing.js'

interface ResourceOptions {
  knowledgeBases: any[]
  bots: any[]
  tools: any[]
  workflows: Workflow[]
  models: any[]
}

type PaletteRequest = {
  edgeId?: string
  nodeId?: string
  direction?: 'before' | 'after'
  side?: FlowSide
  port?: FlowEdge['port']
  clientX?: number
  clientY?: number
}

const props = defineProps<{
  graph?: FlowGraph | null
  catalog: NodeKind[]
  sources: any[]
  resources: ResourceOptions
  fields: string[]
  disabled: boolean
}>()
const emit = defineEmits<{ save: [graph: FlowGraph]; cancel: [] }>()

/** 空图兜底：预置 开始 → 结束 的最小结构，避免用户面对完全空白画布无从下手。 */
function emptyGraph(): FlowGraph {
  const start = newNode('start', '开始', 0)
  const end = newNode('end', '结束', 1)
  start.position = { x: 120, y: 180 }
  end.position = { x: 500, y: 180 }
  return { nodes: [start, end], edges: [{ id: `edge_${createClientId().replaceAll('-', '')}`, source: start.id, target: end.id, port: 'out', sourceSide: 'right', targetSide: 'left' }] }
}

const graph = ref<FlowGraph>(copy(props.graph && props.graph.nodes?.length ? props.graph : emptyGraph()))
const selected = ref(graph.value.nodes.find(node => node.kind !== 'start' && node.kind !== 'end')?.id || graph.value.nodes[0]?.id || '')
const past = ref<FlowGraph[]>([])
const future = ref<FlowGraph[]>([])
const palette = ref(false)
const paletteRequest = ref<PaletteRequest>({})
const notice = ref('')
const paletteError = ref('')

const selectedNode = computed(() => graph.value.nodes.find(node => node.id === selected.value))
const selectedContract = computed(() => props.catalog.find(item => item.kind === selectedNode.value?.kind))
const subgraphCatalog = computed(() => props.catalog)
/** 目录中不存在的节点类型：直接标记为"未提供可执行契约"，不静默保留不可运行节点。 */
const unsupportedNodes = computed(() => graph.value.nodes.filter(node => !props.catalog.some(item => item.kind === node.kind)))
const validationErrors = computed<Record<string, string[]>>(() => Object.fromEntries(
  unsupportedNodes.value.map(node => [node.id, ['此节点未提供可执行契约。']]),
))

/**
 * 提交本地图变更：只读模式直接返回；每次提交前把当前图压入 past 并清空 future，
 * 且用 copy 存入快照，避免后续就地修改污染撤销栈。
 */
function commit(next: FlowGraph) {
  if (props.disabled) return
  past.value.push(copy(graph.value))
  future.value = []
  graph.value = copy(next)
}

function selectNode(id: string) { selected.value = id }
function updateNode(node: FlowNode) {
  const next = copy(graph.value)
  next.nodes = next.nodes.map(item => item.id === node.id ? copy(node) : item)
  commit(next)
}
function updateErrorStrategy(node: FlowNode) {
  const next = copy(graph.value)
  next.nodes = next.nodes.map(item => item.id === node.id ? copy(node) : item)
  if (node.errorStrategy !== 'fail-branch') next.edges = next.edges.filter(edge => edge.source !== node.id || edge.port !== 'error')
  commit(next)
}
function deleteNode(id: string) {
  if (props.disabled) return
  const target = graph.value.nodes.find(node => node.id === id)
  if (!target || target.kind === 'start') return
  const next = copy(graph.value)
  next.nodes = next.nodes.filter(node => node.id !== id)
  next.edges = next.edges.filter(edge => edge.source !== id && edge.target !== id)
  commit(next)
  selected.value = next.nodes.find(node => node.kind === 'end')?.id || next.nodes[0]?.id || ''
}
function arrange() { if (!props.disabled) commit(arrangeWorkflowGraph(copy(graph.value))) }
function undo() {
  if (props.disabled) return
  const previous = past.value.pop()
  if (!previous) return
  future.value.push(copy(graph.value))
  graph.value = previous
  if (!graph.value.nodes.some(node => node.id === selected.value)) selected.value = graph.value.nodes[0]?.id || ''
}
function redo() {
  if (props.disabled) return
  const next = future.value.pop()
  if (!next) return
  past.value.push(copy(graph.value))
  graph.value = next
  if (!graph.value.nodes.some(node => node.id === selected.value)) selected.value = graph.value.nodes[0]?.id || ''
}

function opposite(side: FlowSide): FlowSide {
  return side === 'left' ? 'right' : side === 'right' ? 'left' : side === 'top' ? 'bottom' : 'top'
}
function sideVector(side: FlowSide) {
  if (side === 'left') return { x: -1, y: 0 }
  if (side === 'right') return { x: 1, y: 0 }
  if (side === 'top') return { x: 0, y: -1 }
  return { x: 0, y: 1 }
}
function openPalette(request: PaletteRequest = {}) {
  if (props.disabled) return
  paletteRequest.value = request
  notice.value = ''
  paletteError.value = ''
  palette.value = true
}
function closePalette() {
  palette.value = false
  paletteRequest.value = {}
  paletteError.value = ''
}
function addMany(kinds: NodeKind[]) {
  if (props.disabled || !kinds.length) return
  const base = graph.value
  const request = paletteRequest.value
  const edge = request.edgeId ? base.edges.find(item => item.id === request.edgeId) : undefined
  const source = edge
    ? base.nodes.find(item => item.id === edge.source)
    : request.direction === 'after' ? base.nodes.find(item => item.id === request.nodeId) : undefined
  const target = edge
    ? base.nodes.find(item => item.id === edge.target)
    : request.direction === 'before' ? base.nodes.find(item => item.id === request.nodeId) : undefined
  const anchor = !request.direction && !edge ? base.nodes.find(item => item.id === selected.value) : undefined
  const nodes = kinds.map((item, index) => newNode(item.kind, item.title, base.nodes.length + index, item.defaultConfig))
  if (edge && source) {
    nodes.forEach((node, index) => { node.position = { x: source.position.x + (index + 1) * 280, y: source.position.y + (nodes.length > 1 ? 150 : 0) } })
  } else if (source) {
    const side = request.side || 'right'
    const vector = sideVector(side)
    nodes.forEach((node, index) => { node.position = { x: source.position.x + vector.x * 280 * (index + 1), y: source.position.y + vector.y * 150 * (index + 1) } })
  } else if (target) {
    const side = opposite(request.side || 'left')
    const vector = sideVector(side)
    nodes.forEach((node, index) => { const distance = nodes.length - index; node.position = { x: target.position.x - vector.x * 280 * distance, y: target.position.y - vector.y * 150 * distance } })
  } else if (anchor) {
    nodes.forEach((node, index) => { node.position = { x: anchor.position.x + (index + 1) * 280, y: anchor.position.y } })
  }
  const insertion = buildInsertionEdges(base, request, nodes, () => `edge_${createClientId().replaceAll('-', '')}`)
  if (insertion.error) {
    paletteError.value = insertion.error
    return
  }
  commit({ ...base, nodes: [...base.nodes, ...nodes], edges: insertion.edges })
  selected.value = nodes[0]!.id
  closePalette()
}
function save() {
  if (props.disabled) return
  if (unsupportedNodes.value.length) {
    notice.value = '请先删除循环体中的人工提问或延迟节点，再保存子图。'
    return
  }
  const starts = graph.value.nodes.filter(node => node.kind === 'start').length
  const terminals = graph.value.nodes.filter(node => ['end', 'reply'].includes(node.kind)).length
  if (starts !== 1 || terminals < 1) {
    notice.value = '子图必须包含一个开始节点和至少一个结束或回复节点。'
    return
  }
  emit('save', copy(graph.value))
}
</script>

<template>
  <Teleport to="body">
    <section class="wf-subgraph-layer" role="dialog" aria-modal="true" aria-label="编辑循环体子图">
      <div class="wf-subgraph-dialog">
        <header class="wf-subgraph-heading">
          <div><strong>编辑循环体子图</strong><p>在循环、迭代或条件循环内配置真实节点和连线；保存后才写入外层工作流。</p></div>
          <div class="wf-subgraph-actions"><button type="button" @click="emit('cancel')">取消</button><button type="button" class="wf-primary" :disabled="disabled" @click="save">保存子图</button></div>
        </header>
        <p v-if="notice" class="wf-subgraph-notice" role="alert">{{ notice }}</p>
        <p v-if="unsupportedNodes.length" class="wf-subgraph-warning" role="alert">当前图中已有不支持的节点：{{ unsupportedNodes.map(node => node.title).join('、') }}。删除后才能保存。</p>
        <div class="wf-subgraph-layout">
          <div class="wf-subgraph-canvas" :class="{ 'is-disabled': disabled }">
            <WorkflowCanvas
              :graph="graph"
              :selected="selected"
              :disabled="disabled"
              :validation-errors="validationErrors"
              :can-undo="past.length > 0"
              :can-redo="future.length > 0"
              @select="selectNode"
              @update="commit"
              @add="openPalette"
              @insert="openPalette"
              @palette="openPalette"
              @arrange="arrange"
              @undo="undo"
              @redo="redo"
              @delete-node="deleteNode"
            />
            <div v-if="palette" class="wf-subgraph-palette-layer" @mousedown.self="closePalette">
              <p v-if="paletteError" class="wf-subgraph-palette-error" role="alert">{{ paletteError }}</p>
              <div class="wf-subgraph-palette" role="dialog" aria-label="选择子图节点"><WorkflowNodePalette :catalog="subgraphCatalog" :disabled="disabled" scope-label="循环体可用节点；人工提问和延迟暂不支持" @add="addMany([$event])" @add-snippet="addMany" @close="closePalette" /></div>
            </div>
          </div>
          <WorkflowNodeForm
            v-if="selectedNode"
            :key="selectedNode.id"
            :node="selectedNode"
            :nodes="graph.nodes"
            :edges="graph.edges"
            :sources="sources"
            :resources="resources"
            :disabled="disabled"
            :fields="fields"
            :contract="selectedContract"
            :errors="validationErrors[selectedNode.id] || []"
            :validation-known="unsupportedNodes.length > 0"
            @update="updateNode"
            @remove="deleteNode"
            @close="selected = ''"
            @add-next="openPalette"
            @error-strategy="updateErrorStrategy"
          />
          <p v-else class="wf-subgraph-empty-panel">请选择一个节点编辑属性。<br />点击画布上的加号添加节点。</p>
        </div>
      </div>
    </section>
  </Teleport>
</template>

<style scoped>
.wf-subgraph-layer { position: fixed; inset: 0; z-index: 1200; display: grid; place-items: center; padding: 22px; background: rgba(16, 24, 40, .42); backdrop-filter: blur(2px); }
.wf-subgraph-dialog { display: flex; width: min(1320px, 100%); height: min(900px, 94dvh); min-height: 520px; flex-direction: column; overflow: hidden; border: 1px solid #d0d5dd; border-radius: 16px; background: #f8fafc; box-shadow: 0 24px 80px rgba(16, 24, 40, .28); }
.wf-subgraph-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; padding: 16px 20px 13px; border-bottom: 1px solid #eaecf0; background: #fff; }
.wf-subgraph-heading strong { color: #101828; font-size: 15px; }
.wf-subgraph-heading p { margin: 4px 0 0; color: #667085; font-size: 10px; line-height: 14px; }
.wf-subgraph-actions { display: flex; flex: 0 0 auto; gap: 8px; }
.wf-subgraph-actions button { min-height: 32px; padding: 6px 12px; border: 1px solid #d0d5dd; border-radius: 8px; background: #fff; color: #344054; font-size: 10px; cursor: pointer; }
.wf-subgraph-actions button:hover { border-color: #84adff; color: #155eef; }
.wf-subgraph-actions .wf-primary { border-color: #155eef; background: #155eef; color: #fff; }
.wf-subgraph-actions button:disabled { cursor: not-allowed; opacity: .55; }
.wf-subgraph-notice, .wf-subgraph-warning { margin: 10px 16px 0; padding: 8px 10px; border: 1px solid #fecdca; border-radius: 8px; background: #fef3f2; color: #b42318; font-size: 10px; line-height: 14px; }
.wf-subgraph-warning { border-color: #fedf89; background: #fffaeb; color: #b54708; }
.wf-subgraph-layout { display: grid; min-height: 0; flex: 1; grid-template-columns: minmax(0, 1fr) minmax(300px, 360px); gap: 12px; padding: 12px; }
.wf-subgraph-canvas { position: relative; min-width: 0; min-height: 0; overflow: hidden; border: 1px solid #d0d5dd; border-radius: 12px; background: #fff; }
.wf-subgraph-canvas :deep(.wf-canvas-section), .wf-subgraph-canvas :deep(.wf-canvas-frame), .wf-subgraph-canvas :deep(.wf-canvas-scroll) { height: 100%; min-height: 0; border: 0; border-radius: 0; }
.wf-subgraph-canvas :deep(.wf-canvas-frame) { overflow: hidden; }
.wf-subgraph-layout :deep(.wf-properties) { height: 100%; max-height: none; overflow: auto; }
.wf-subgraph-palette-layer { position: absolute; inset: 0; z-index: 50; display: grid; place-items: center; padding: 14px; background: rgba(247, 248, 250, .36); }
.wf-subgraph-palette-error { position: absolute; z-index: 2; top: 14px; right: 14px; left: 14px; margin: 0; padding: 8px 10px; border: 1px solid #fecdca; border-radius: 8px; background: #fef3f2; color: #b42318; font-size: 10px; line-height: 14px; }
.wf-subgraph-palette { width: min(430px, 100%); max-height: 100%; overflow: auto; border: 1px solid #d0d5dd; border-radius: 12px; background: #fff; box-shadow: 0 18px 45px rgba(16, 24, 40, .2); }
.wf-subgraph-empty-panel { display: grid; min-height: 160px; place-items: center; align-content: center; margin: 0; padding: 22px; border: 1px solid #d0d5dd; border-radius: 12px; background: #fff; color: #98a2b3; font-size: 10px; line-height: 16px; text-align: center; }
@media (max-width: 900px) { .wf-subgraph-layer { padding: 8px; }.wf-subgraph-dialog { height: 98dvh; }.wf-subgraph-layout { grid-template-columns: minmax(0, 1fr); grid-template-rows: minmax(300px, 1fr) minmax(260px, .75fr); overflow: auto; }.wf-subgraph-layout :deep(.wf-properties) { min-height: 260px; }.wf-subgraph-heading { padding: 12px; } }
@media (max-width: 560px) { .wf-subgraph-heading { align-items: stretch; flex-direction: column; gap: 10px; }.wf-subgraph-actions { justify-content: flex-end; }.wf-subgraph-layout { padding: 8px; gap: 8px; } }
</style>
