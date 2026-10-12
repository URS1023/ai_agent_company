<script setup lang="ts">
/**
 * DifyWorkflowEdge —— 画布连线（Dify 风格），由 WorkflowCanvas 以 `edge-workflow` 模板注入。
 *
 * 依赖接口：`utils/workflowEdgeState.workflowEdgeColors`（执行态 → 颜色映射，是唯一的颜色来源）、
 * `utils/workflowEdgeRouting.routeWorkflowEdge`（在节点包围盒之间绕行的路由算法）。
 * 关键约束：
 * - 颜色只允许来自 workflowEdgeColors；选中态用固定高亮色覆盖，不要在模板里写死其他语义色；
 * - 分支标签来源：condition 为 满足/不满足、error 端口为 ERROR、classifier 取 case 配置的 label
 *   （fallback 分支显示"未匹配"），拿不到配置时退化为 port 原文，避免显示空白；
 * - 连线计数（execution.count）由 WorkflowCanvas 依据运行状态推导，count 为 null 时不拼"条数"，
 *   以免把"未运行"误读成"0 条命中"；
 * - 只在选中或悬停时显示"插入节点"按钮，避免画布被按钮淹没；disabled（只读）时按钮不渲染。
 */
import { computed, useId } from 'vue'
import { BaseEdge, EdgeLabelRenderer, getBezierPath, useVueFlow, type EdgeProps } from '@vue-flow/core'
import type { FlowEdge } from '../../api/workflows'
import { workflowEdgeColors } from '../../utils/workflowEdgeState.js'
import { routeWorkflowEdge } from '../../utils/workflowEdgeRouting.js'

interface WorkflowEdgeData {
  edge: FlowEdge
  disabled: boolean
  hovering?: boolean
  lane: number
  execution: { status: keyof typeof workflowEdgeColors; count: number | null }
}

const props = defineProps<EdgeProps<WorkflowEdgeData>>()
const markerId = `workflow-arrow-${useId().replaceAll(':', '')}`
const { getNodes } = useVueFlow()
const emit = defineEmits<{
  insert: [value: { edgeId: string; clientX: number; clientY: number }]
}>()
/** 基准贝塞尔曲线；实际路径由 routeWorkflowEdge 在此基础上按障碍节点绕行。 */
const bezier = computed(() => getBezierPath({
  sourceX: props.sourceX,
  sourceY: props.sourceY,
  targetX: props.targetX,
  targetY: props.targetY,
  sourcePosition: props.sourcePosition,
  targetPosition: props.targetPosition,
  curvature: 0.16,
}))
const path = computed(() => routeWorkflowEdge({
  source: { x: props.sourceX, y: props.sourceY }, target: { x: props.targetX, y: props.targetY },
  sourceSide: props.sourcePosition, targetSide: props.targetPosition,
  sourceId: props.source, targetId: props.target, bezier: bezier.value, lane: props.data.lane,
  nodes: getNodes.value.filter(node => !node.hidden).map(node => ({
    id: node.id, x: node.position.x, y: node.position.y,
    width: node.dimensions.width, height: node.dimensions.height,
  })),
}))
/** 连线分支文案：端口语义 → 用户可读文本；classifier 分支优先取节点配置里的中文标签。 */
const branch = computed(() => {
  const port = props.data.edge.port
  if (port.startsWith('case:')) {
    const source = getNodes.value.find(node => node.id === props.source)?.data?.node
    return source?.config?.cases?.find((item: any) => item.id === port.slice(5))?.label || (port.slice(5) === source?.config?.fallback ? '未匹配' : port.slice(5))
  }
  return port === 'true' ? '满足' : port === 'false' ? '不满足' : port === 'error' ? 'ERROR' : ''
})
const visible = computed(() => props.selected || props.data.hovering)
const stroke = computed(() => props.selected ? '#085afc' : workflowEdgeColors[props.data.execution.status])
const hasResult = computed(() => !!branch.value && props.data.execution.count !== null)
/** 分支文案 + 命中条数；无运行结果时只显示分支名，不显示"0 条"。 */
const branchLabel = computed(() => `${branch.value}${hasResult.value ? ` · ${props.data.execution.count} 条` : ''}`)
function insert(event: MouseEvent) {
  emit('insert', { edgeId: props.id, clientX: event.clientX, clientY: event.clientY })
}
</script>

<template>
  <defs>
    <marker :id="markerId" viewBox="0 0 12 12" refX="11" refY="6" markerWidth="12" markerHeight="12" markerUnits="userSpaceOnUse" orient="auto">
      <path d="M 1 1 L 11 6 L 1 11 Z" :fill="stroke" :data-edge-id="id" />
    </marker>
  </defs>
  <BaseEdge
    :id="id"
    :path="path.path"
    :interaction-width="20"
    :marker-end="`url(#${markerId})`"
    :data-execution-state="data.execution.status"
    :data-rerouted="path.rerouted"
    :style="{ stroke, strokeWidth: 2.25, strokeLinecap: 'round', vectorEffect: 'non-scaling-stroke' }"
  />
  <EdgeLabelRenderer>
    <div
      class="wf-edge-actions nodrag nopan"
      :class="{ selected, visible, 'has-result': hasResult }"
      :style="{ transform: `translate(-50%, -50%) translate(${path.labelX}px, ${path.labelY}px)` }"
    >
      <span v-if="branch" class="wf-edge-branch" :style="{ color: stroke }">{{ branchLabel }}</span>
      <button
        v-if="!data.disabled && visible"
        type="button"
        title="在连线上插入节点"
        aria-label="在连线上插入节点"
        @click.stop="insert"
      ><i class="fa-solid fa-plus" aria-hidden="true" /></button>
    </div>
  </EdgeLabelRenderer>
</template>

<style scoped>
.wf-edge-actions.has-result { opacity: 1; }
</style>
