<script setup lang="ts">
/**
 * WorkflowCanvas —— 基于 @vue-flow/core 的可视化编排画布。
 *
 * 位置：WorkflowEditor 的画布区（`/workflows/:id` 与设备工作流入口）。
 * 依赖接口：`api/workflows`（FlowGraph / FlowEdge / FlowSide / copy）、`utils/workflowEdgeState`
 * （orderWorkflowEdges 分层排序、workflowEdgeState 计算连线执行态颜色）、
 * `utils/randomId.createClientId`（生成前端临时 id）；自定义节点/连线为 DifyWorkflowNode / DifyWorkflowEdge。
 * 关键约束：
 * - 本组件不直接持久化：结构变化一律 copy 后派发 `update`，由 useWorkflows 防抖自动保存；
 *   位置修改同理，禁止就地改 props.graph；
 * - 连线合法性由 connectionAllowed 统一裁决（端口必须与节点类型匹配：condition 只能 true/false、
 *   classifier 只能 out/各 case、error 端口要求 errorStrategy === 'fail-branch'；start 不能作目标、end/reply 不能作源），
 *   前端校验只是体验保障，后端仍会再次校验；
 * - handle id 是前后端之间的隐性契约：`source-<port>-<side>` / `target-<side>`，改名会导致历史图的连线丢失；
 * - disabled（只读模式）下禁止拖拽、连线、复制、删除与方向键微调，但允许选中查看；
 * - 画布内键盘快捷键只在焦点位于画布且目标不是输入控件时生效，避免抢走表单按键。
 */
import { computed, nextTick, shallowRef, useTemplateRef, watch } from 'vue'
import {
  ConnectionLineType,
  ConnectionMode,
  VueFlow,
  useVueFlow,
  type Connection,
  type Edge as CanvasEdge,
  type EdgeMouseEvent,
  type Node as CanvasNode,
  type NodeDragEvent,
  type NodeMouseEvent,
  type ValidConnectionFunc,
} from '@vue-flow/core'
import { Background, BackgroundVariant } from '@vue-flow/background'
import { MiniMap } from '@vue-flow/minimap'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/minimap/dist/style.css'
import { copy, type FlowEdge, type FlowGraph, type FlowSide } from '../../api/workflows'
import { createClientId } from '../../utils/randomId'
import { orderWorkflowEdges, workflowEdgeState } from '../../utils/workflowEdgeState.js'
import DifyWorkflowNode from './DifyWorkflowNode.vue'
import DifyWorkflowEdge from './DifyWorkflowEdge.vue'
import WorkflowNodeContextMenu from './WorkflowNodeContextMenu.vue'

const props = defineProps<{
  graph: FlowGraph
  selected: string
  disabled: boolean
  statuses?: Record<string, any>
  validationErrors?: Record<string, string[]>
  canUndo?: boolean
  canRedo?: boolean
}>()
const emit = defineEmits<{
  select: [id: string]
  update: [graph: FlowGraph]
  add: [value?: { clientX: number; clientY: number }]
  insert: [value: { edgeId: string; clientX: number; clientY: number }]
  palette: [value: { nodeId: string; direction: 'before' | 'after'; side: FlowSide; port: FlowEdge['port']; clientX: number; clientY: number }]
  arrange: []
  undo: []
  redo: []
  deleteNode: [id: string]
  inspectRun: [id: string]
}>()

const flowId = `workflow-canvas-${createClientId()}`
const canvasSection = useTemplateRef<HTMLElement>('canvasSection')
const flowNodes = shallowRef<CanvasNode[]>([])
const flowEdges = shallowRef<CanvasEdge[]>([])
const selectedEdge = shallowRef('')
const showMiniMap = shallowRef(true)
const showShortcuts = shallowRef(false)
const nodeMenu = shallowRef<{ id: string; x: number; y: number } | null>(null)
const menuNode = computed(() => props.graph.nodes.find(node => node.id === nodeMenu.value?.id))
const controlMode = shallowRef<'pointer' | 'hand'>('pointer')
const connectionHint = shallowRef('拖动节点四周的连接点，即可从任意方向建立连线')
const connectionNotice = shallowRef('')
let connectionHandled = false
let paneReady = false
let nodesReady = false
let initialViewPositioned = false
const { fitView, zoomIn, zoomOut, zoomTo, viewport, connectionStartHandle, connectionEndHandle } = useVueFlow({ id: flowId, minZoom: 0.25, maxZoom: 2 })
const zoomPercent = computed(() => Math.round(viewport.value.zoom * 100))

/** 源 handle id：`source-<port>-<side>`，port 决定走哪条执行分支，side 决定线从节点哪一侧出去。 */
function sourceHandle(edge: FlowEdge) {
  return `source-${edge.port || 'out'}-${edge.sourceSide || 'right'}`
}
/** 目标 handle id：`target-<side>`；目标端口不区分分支，只保留接入侧。 */
function targetHandle(edge: FlowEdge) {
  return `target-${edge.targetSide || 'left'}`
}
/**
 * 把 props.graph 同步成 Vue Flow 的 nodes/edges。
 * 只渲染两端节点都存在（且未被删除）的边，避免历史数据里的悬挂边导致画布报错；
 * lane 用于同一目标节点的多条入线分层避让；execution 由 workflowEdgeState 依据端口与两端运行状态推导。
 */
function syncElements() {
  const nodeIds = new Set(props.graph.nodes.map(node => node.id))
  if (selectedEdge.value && !props.graph.edges.some(edge => edge.id === selectedEdge.value)) selectedEdge.value = ''
  flowNodes.value = props.graph.nodes.map(node => ({
    id: node.id,
    type: 'workflow',
    position: { ...node.position },
    data: {
      node: copy(node),
      status: props.statuses?.[node.id]?.status,
      issues: props.validationErrors?.[node.id] || [],
      disabled: props.disabled,
      connectedSources: props.graph.edges.filter(edge => edge.source === node.id).map(sourceHandle),
      connectedTargets: props.graph.edges.filter(edge => edge.target === node.id).map(targetHandle),
    },
    selected: node.id === props.selected,
    selectable: true,
    connectable: !props.disabled,
    focusable: true,
    deletable: false,
    dragHandle: '.wf-node-heading',
    ariaLabel: `${node.title} 工作流节点`,
  }))
  flowEdges.value = orderWorkflowEdges(props.graph.edges.filter(edge => nodeIds.has(edge.source) && nodeIds.has(edge.target)).map(edge => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
    sourceHandle: sourceHandle(edge),
    targetHandle: targetHandle(edge),
    type: 'workflow',
    data: {
      edge: copy(edge),
      disabled: props.disabled,
      hovering: false,
      lane: props.graph.edges.filter(item => item.target === edge.target).findIndex(item => item.id === edge.id),
      execution: workflowEdgeState(edge.port, props.statuses?.[edge.source], props.statuses?.[edge.target]),
    },
    selected: edge.id === selectedEdge.value,
    selectable: true,
    focusable: true,
    deletable: false,
    interactionWidth: 20,
    ariaLabel: `${props.graph.nodes.find(node => node.id === edge.source)?.title || edge.source} 到 ${props.graph.nodes.find(node => node.id === edge.target)?.title || edge.target} 的连线`,
  })))
}

watch(
  () => [props.graph, props.disabled, props.statuses, props.validationErrors],
  syncElements,
  { immediate: true, deep: true },
)
watch(() => props.selected, value => {
  flowNodes.value = flowNodes.value.map(node => ({ ...node, selected: node.id === value }))
})

/** 解析源 handle，必须与 sourceHandle() 的拼装格式严格对应；格式不符返回 null 视为非法连接。 */
function parseSource(handle?: string | null): { port: FlowEdge['port']; side: FlowSide } | null {
  const match = /^source-(out|true|false|error|case:[A-Za-z][A-Za-z0-9_-]*)-(left|right|top|bottom)$/.exec(handle || '')
  return match ? { port: match[1] as FlowEdge['port'], side: match[2] as FlowSide } : null
}
/** 解析目标 handle，同样依赖 target-<side> 格式。 */
function parseTarget(handle?: string | null): FlowSide | null {
  const match = /^target-(left|right|top|bottom)$/.exec(handle || '')
  return match ? match[1] as FlowSide : null
}
/**
 * 把下游节点可见的"加号"落点归一化回规范的 target-<side>。
 * 否则用户从上游拖到下游节点的加号时会写入非标准 handle id，重载后连线接不上。
 */
function normalizeConnection(connection: Connection): Connection {
  // Accept a downstream node's visible plus as a drop target, while persisting
  // its canonical input handle and preserving the origin's execution branch.
  const side = parseTarget(connection.targetHandle) || parseSource(connection.targetHandle)?.side
  return { ...connection, targetHandle: side ? `target-${side}` : connection.targetHandle }
}
/**
 * 判定一条连线是否允许建立（Vue Flow 的 is-valid-connection 与 connect 共用）。
 * 依次校验：两端节点存在且不同、方向合法（start 不可作目标、end/reply 不可作源）、
 * 输出端口与节点类型匹配（condition 只允许 true/false，classifier 只允许 out 或已配置 case，
 * error 端口要求该节点已开启 fail-branch）。
 * @returns 允许返回 true；不允许返回 false，不抛异常。
 */
function connectionAllowed(connection: Connection) {
  connection = normalizeConnection(connection)
  const source = props.graph.nodes.find(node => node.id === connection.source)
  const target = props.graph.nodes.find(node => node.id === connection.target)
  const output = parseSource(connection.sourceHandle)
  const input = parseTarget(connection.targetHandle)
  let reason = ''
  if (!source || !target || !output || !input || source.id === target.id) reason = 'missing-or-same'
  else if (['end', 'reply'].includes(source.kind) || target.kind === 'start') reason = 'terminal-direction'
  else if (output.port === 'error' ? source.errorStrategy !== 'fail-branch' : source.kind === 'condition' ? !['true', 'false'].includes(output.port) : source.kind === 'classifier' ? !(output.port === 'out' || [...(source.config.cases || []).map((item: any) => `case:${item.id}`), `case:${source.config.fallback || 'other'}`].includes(output.port)) : output.port !== 'out') reason = 'invalid-port'
  return !reason
}
const isValidConnection: ValidConnectionFunc = connection => connectionAllowed(connection)
/**
 * 建立连线：同源-同目标-同端口的边已存在时只更新连接侧边（避免重复边），否则追加新边。
 * 注意不能在这里拒绝"已存在"的边 —— Vue Flow 水合已有边时也会走校验，
 * 直接拒绝会让历史连线在界面上消失。
 * 副作用：派发 update（整图替换）并写入操作提示文案。
 */
function connect(connection: Connection) {
  if (props.disabled) return
  connection = normalizeConnection(connection)
  const output = parseSource(connection.sourceHandle)
  const input = parseTarget(connection.targetHandle)
  if (!output || !input || !connectionAllowed(connection)) return
  // Vue Flow also validates existing edges while hydrating its store. Duplicate
  // prevention belongs only to creation; rejecting an existing edge hides it.
  const next = copy(props.graph)
  const existing = next.edges.find(edge => edge.source === connection.source && edge.target === connection.target && edge.port === output.port)
  if (existing) {
    existing.sourceSide = output.side
    existing.targetSide = input
  } else next.edges.push({
    id: `edge_${createClientId().replaceAll('-', '')}`,
    source: connection.source,
    target: connection.target,
    port: output.port,
    sourceSide: output.side,
    targetSide: input,
  })
  connectionHandled = true
  connectionHint.value = `已从${sideName(output.side)}侧连接到${sideName(input)}侧`
  connectionNotice.value = existing ? '已更新连线的连接位置' : connectionHint.value
  emit('update', next)
}
function startConnection() {
  connectionHandled = false
  connectionNotice.value = ''
}
/** 连线未成功时给出可读原因；成功路径已由 connect 提示，不重复覆盖。 */
function endConnection() {
  if (connectionHandled || !connectionEndHandle.value) return
  connectionNotice.value = connectionStartHandle.value?.nodeId === connectionEndHandle.value.nodeId
    ? '不能将节点连接到自身，请选择其他节点'
    : '无法建立此连接：请从上游输出连接到下游节点；开始节点不能接入，结束节点不能输出'
}
function sideName(side: FlowSide) {
  return side === 'top' ? '上' : side === 'right' ? '右' : side === 'bottom' ? '下' : '左'
}
function selectNode(event: NodeMouseEvent) {
  nodeMenu.value = null
  selectedEdge.value = ''
  emit('select', event.node.id)
}
function openNodeMenu({ event, node }: NodeMouseEvent) {
  event.preventDefault()
  selectedEdge.value = ''
  emit('select', node.id)
  nodeMenu.value = { id: node.id, x: 'clientX' in event ? event.clientX : 0, y: 'clientY' in event ? event.clientY : 0 }
}
function nodeMenuAction(action: 'properties' | 'duplicate' | 'delete') {
  const id = nodeMenu.value?.id
  nodeMenu.value = null
  if (!id) return
  if (action === 'properties') emit('select', id)
  else if (action === 'duplicate') duplicateNode(id)
  else emit('deleteNode', id)
}
function selectNodeById(id: string) {
  selectedEdge.value = ''
  emit('select', id)
}
function selectEdge(event: EdgeMouseEvent) {
  canvasSection.value?.focus({ preventScroll: true })
  selectedEdge.value = event.edge.id
  flowEdges.value = orderWorkflowEdges(flowEdges.value.map(edge => ({ ...edge, selected: edge.id === event.edge.id })))
  emit('select', '')
}
function clearSelection() {
  nodeMenu.value = null
  canvasSection.value?.focus({ preventScroll: true })
  selectedEdge.value = ''
  flowEdges.value = orderWorkflowEdges(flowEdges.value.map(edge => ({ ...edge, selected: false })))
  emit('select', '')
}
function hoverEdge(event: EdgeMouseEvent, hovering: boolean) {
  flowEdges.value = flowEdges.value.map(edge => edge.id === event.edge.id
    ? { ...edge, data: { ...edge.data, hovering } }
    : edge)
}
/**
 * 拖拽结束后把画布坐标写回图数据。
 * 只提交本次真正移动过的节点，坐标取整，且无实际变化时不派发 update，
 * 避免每次拖拽都触发一次自动保存、放大后端写压力。
 */
function persistPositions(event?: NodeDragEvent) {
  if (props.disabled) return
  const moved = new Set(event?.nodes?.map(node => node.id) || (event?.node ? [event.node.id] : []))
  const positions = new Map(flowNodes.value.map(node => [node.id, node.position]))
  const next = copy(props.graph)
  let changed = false
  next.nodes = next.nodes.map(node => {
    if (moved.size && !moved.has(node.id)) return node
    const position = positions.get(node.id)
    if (!position || (position.x === node.position.x && position.y === node.position.y)) return node
    changed = true
    return { ...node, position: { x: Math.round(position.x), y: Math.round(position.y) } }
  })
  if (changed) emit('update', next)
}
/** 方向键微调节点位置；坐标夹在 ±20000 内，防止用户长按把节点推到画布外无法找回。 */
function nudge(value: { id: string; x: number; y: number }) {
  if (props.disabled) return
  const next = copy(props.graph)
  const node = next.nodes.find(item => item.id === value.id)
  if (!node) return
  node.position = {
    x: Math.max(-20000, Math.min(20000, node.position.x + value.x)),
    y: Math.max(-20000, Math.min(20000, node.position.y + value.y)),
  }
  emit('select', value.id)
  emit('update', next)
}
/**
 * 复制节点：生成新的前端 id 并偏移 48px 落位。
 * 开始节点不可复制（流程只能有一个入口），节点总数上限 100 与后端约束保持一致。
 */
function duplicateNode(id = props.selected) {
  if (props.disabled || !id) return
  const original = props.graph.nodes.find(node => node.id === id)
  if (!original || original.kind === 'start' || props.graph.nodes.length >= 100) return
  const clone = copy(original)
  clone.id = `node_${createClientId().replaceAll('-', '')}`
  clone.title = `${original.title} 副本`
  clone.position = { x: original.position.x + 48, y: original.position.y + 48 }
  const next = copy(props.graph)
  next.nodes.push(clone)
  emit('update', next)
  emit('select', clone.id)
}
/** Delete/Backspace：优先删除选中的连线，否则删除选中节点；开始节点不可删除。 */
function deleteSelected() {
  if (props.disabled) return
  if (selectedEdge.value) {
    const edgeId = selectedEdge.value
    selectedEdge.value = ''
    emit('update', { ...copy(props.graph), edges: props.graph.edges.filter(edge => edge.id !== edgeId) })
    return
  }
  const node = props.graph.nodes.find(item => item.id === props.selected)
  if (node && node.kind !== 'start') emit('deleteNode', node.id)
}
/**
 * 画布快捷键：Ctrl/Cmd+Z 撤销、+Shift 重做、Ctrl+Y 重做、Ctrl+D 复制节点、
 * Delete/Backspace 删除、F 或 2 适应画布、1 恢复 100%、Esc 取消选中并关闭快捷键面板。
 * 焦点在 INPUT/TEXTAREA/SELECT 或可编辑元素上时整体让行，避免影响节点表单输入。
 */
function onKeydown(event: KeyboardEvent) {
  const element = event.target as HTMLElement
  if (['INPUT', 'TEXTAREA', 'SELECT'].includes(element.tagName) || element.isContentEditable) return
  const command = event.ctrlKey || event.metaKey
  if (command && event.key.toLowerCase() === 'z') {
    event.preventDefault()
    event.shiftKey ? emit('redo') : emit('undo')
  } else if (command && event.key.toLowerCase() === 'y') {
    event.preventDefault()
    emit('redo')
  } else if (command && event.key.toLowerCase() === 'd') {
    event.preventDefault()
    duplicateNode()
  } else if (event.key === 'Delete' || event.key === 'Backspace') {
    event.preventDefault()
    deleteSelected()
  } else if (!command && event.key.toLowerCase() === 'f') {
    event.preventDefault()
    void fitCanvas()
  } else if (!command && event.key === '1') {
    event.preventDefault()
    void zoomTo(1, { duration: 180 })
  } else if (!command && event.key === '2') {
    event.preventDefault()
    void fitCanvas()
  } else if (event.key === 'Escape') {
    clearSelection()
    showShortcuts.value = false
  }
}
/** 适应画布：整体缩放并夹取缩放区间，保证长流程不会缩到连线不可见。 */
async function fitCanvas() {
  await fitView({ padding: 0.22, minZoom: 0.25, maxZoom: 1.1, duration: 220 })
}
/**
 * 首次进入时把视野对准前 5 个节点而非整图：长流程整体缩放会让节点文字不可读，
 * 用户更可能需要的是"从上往下读"的可读起点。
 */
async function focusReadableStart() {
  // A long workflow belongs on an infinite canvas.  Opening at a readable
  // scale is more useful than shrinking every node until its wires vanish.
  const visible = flowNodes.value.slice(0, Math.min(5, flowNodes.value.length)).map(node => node.id)
  return fitView({ nodes: visible, padding: 0.2, minZoom: 0.55, maxZoom: 1, duration: 220 })
}
/**
 * 等画布与节点都初始化完成后再定位初始视野（两者完成顺序不固定），只执行一次；
 * 若 fitView 失败则复位标记，允许后续事件重试，避免画布停在未定位状态。
 */
async function positionInitialView() {
  if (!paneReady || !nodesReady || initialViewPositioned) return
  initialViewPositioned = true
  await nextTick()
  if (!await focusReadableStart()) initialViewPositioned = false
}
function initialized() { paneReady = true; void positionInitialView() }
function nodesInitialized() { nodesReady = true; void positionInitialView() }
function insert(value: { edgeId: string; clientX: number; clientY: number }) {
  selectedEdge.value = value.edgeId
  emit('insert', value)
}
</script>

<template>
  <section ref="canvasSection" class="wf-canvas-section" tabindex="0" aria-label="工作流画布" @keydown.capture="onKeydown">
    <div class="wf-canvas-frame wf-canvas-scroll" :class="`is-${controlMode}`">
      <VueFlow
        :id="flowId"
        v-model:nodes="flowNodes"
        v-model:edges="flowEdges"
        class="wf-canvas"
        :min-zoom="0.25"
        :max-zoom="2"
        :nodes-draggable="!disabled && controlMode === 'pointer'"
        :nodes-connectable="!disabled"
        :elements-selectable="true"
        :pan-on-drag="true"
        :pan-on-scroll="true"
        :zoom-on-scroll="true"
        :zoom-on-pinch="true"
        :zoom-on-double-click="false"
        :select-nodes-on-drag="false"
        :connection-radius="30"
        :connection-mode="ConnectionMode.Loose"
        :connection-line-type="ConnectionLineType.Bezier"
        :connection-line-style="{ stroke: '#085afc', strokeWidth: 2 }"
        :is-valid-connection="isValidConnection"
        :delete-key-code="null"
        :multi-selection-key-code="'Shift'"
        :fit-view-on-init="false"
        @init="initialized"
        @nodes-initialized="nodesInitialized"
        @connect="connect"
        @connect-start="startConnection"
        @connect-end="endConnection"
        @node-click="selectNode"
        @node-context-menu="openNodeMenu"
        @edge-click="selectEdge"
        @edge-mouse-enter="hoverEdge($event, true)"
        @edge-mouse-leave="hoverEdge($event, false)"
        @pane-click="clearSelection"
        @node-drag-stop="persistPositions"
        @selection-drag-stop="persistPositions"
      >
        <Background :variant="BackgroundVariant.Dots" :gap="14" :size="1.5" pattern-color="rgba(133, 133, 173, .3)" />
        <template #node-workflow="nodeProps">
          <DifyWorkflowNode
            v-bind="nodeProps"
            @select="selectNodeById"
            @nudge="nudge"
            @palette="emit('palette', $event)"
            @duplicate="duplicateNode"
            @remove="emit('deleteNode', $event)"
            @inspect-run="emit('inspectRun', $event)"
          />
        </template>
        <template #edge-workflow="edgeProps">
          <DifyWorkflowEdge v-bind="edgeProps" @insert="insert" />
        </template>

        <div class="wf-canvas-context wf-visually-hidden" aria-live="polite">
          <span>{{ graph.nodes.length }} 个节点</span><b>·</b><span>{{ graph.edges.length }} 条连线</span>
          <small>{{ connectionHint }}</small>
        </div>
        <div v-if="connectionNotice" class="wf-connection-notice" role="status">{{ connectionNotice }}</div>
        <div class="wf-canvas-control wf-canvas-add" aria-label="画布工具">
          <button type="button" class="wf-primary" :disabled="disabled || graph.nodes.length >= 100" title="添加节点" aria-label="添加节点" @click="emit('add', { clientX: $event.clientX, clientY: $event.clientY })">
            <i class="fa-solid fa-circle-plus" aria-hidden="true" /><span class="wf-visually-hidden">添加节点</span>
          </button>
          <span class="wf-control-divider" aria-hidden="true" />
          <button type="button" :class="{ active: controlMode === 'pointer' }" :disabled="disabled" title="指针模式" aria-label="指针模式" @click="controlMode = 'pointer'">
            <i class="fa-solid fa-arrow-pointer" aria-hidden="true" />
          </button>
          <button type="button" :class="{ active: controlMode === 'hand' }" title="手型模式" aria-label="手型模式" @click="controlMode = 'hand'">
            <i class="fa-regular fa-hand" aria-hidden="true" />
          </button>
          <span class="wf-control-divider" aria-hidden="true" />
          <button type="button" :disabled="disabled" title="自动排布" aria-label="自动排布" @click="emit('arrange')">
            <i class="fa-solid fa-object-group" aria-hidden="true" />
          </button>
        </div>
        <div class="wf-canvas-history" aria-label="编辑历史">
          <button type="button" :disabled="disabled || !canUndo" title="撤销（Ctrl+Z）" aria-label="撤销" @click="emit('undo')"><i class="fa-solid fa-rotate-left" aria-hidden="true" /></button>
          <button type="button" :disabled="disabled || !canRedo" title="重做（Ctrl+Shift+Z）" aria-label="重做" @click="emit('redo')"><i class="fa-solid fa-rotate-right" aria-hidden="true" /></button>
        </div>
        <div class="wf-canvas-operator" aria-label="画布视图">
          <button type="button" title="缩小" aria-label="缩小画布" @click="zoomOut({ duration: 120 })"><i class="fa-solid fa-minus" aria-hidden="true" /></button>
          <button type="button" class="wf-zoom-value" title="恢复 100%" @click="zoomTo(1, { duration: 180 })">{{ zoomPercent }}%</button>
          <button type="button" title="放大" aria-label="放大画布" @click="zoomIn({ duration: 120 })"><i class="fa-solid fa-plus" aria-hidden="true" /></button>
          <button type="button" :class="{ active: showMiniMap }" title="显示或隐藏小地图" aria-label="显示或隐藏小地图" @click="showMiniMap = !showMiniMap"><i class="fa-regular fa-map" aria-hidden="true" /></button>
          <button type="button" title="适应画布（F）" aria-label="适应画布" @click="fitCanvas"><i class="fa-solid fa-expand" aria-hidden="true" /></button>
          <button type="button" :class="{ active: showShortcuts }" title="快捷键" aria-label="快捷键帮助" @click="showShortcuts = !showShortcuts"><i class="fa-solid fa-ellipsis" aria-hidden="true" /></button>
        </div>
        <MiniMap
          v-if="showMiniMap"
          class="wf-minimap"
          :pannable="true"
          :zoomable="true"
          :width="102"
          :height="72"
          :node-border-radius="4"
          :node-stroke-width="2"
          node-color="rgba(200, 206, 218, .3)"
          node-stroke-color="#c8ceda"
          mask-color="#e9ebf0"
          aria-label="工作流小地图"
        />
        <aside v-if="showShortcuts" class="wf-shortcuts nodrag nopan" aria-label="工作流快捷键">
          <header><strong>快捷键</strong><button type="button" aria-label="关闭快捷键" @click="showShortcuts = false"><i class="fa-solid fa-xmark" /></button></header>
          <dl>
            <div><dt>撤销 / 重做</dt><dd>Ctrl Z / Ctrl Shift Z</dd></div>
            <div><dt>复制节点</dt><dd>Ctrl D</dd></div>
            <div><dt>删除选中项</dt><dd>Delete</dd></div>
            <div><dt>适应画布</dt><dd>F 或 2</dd></div>
            <div><dt>恢复 100%</dt><dd>1</dd></div>
            <div><dt>精细移动</dt><dd>Shift + 方向键</dd></div>
            <div><dt>平移画布</dt><dd>拖动空白处 / 手型模式</dd></div>
          </dl>
        </aside>
      </VueFlow>
    </div>
    <WorkflowNodeContextMenu v-if="nodeMenu && menuNode" :node="menuNode" :x="nodeMenu.x" :y="nodeMenu.y" :disabled="disabled" @action="nodeMenuAction" @close="nodeMenu = null" />
  </section>
</template>
