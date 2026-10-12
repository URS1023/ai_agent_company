<script setup lang="ts">
/**
 * WorkflowVariableInspect —— 「变量检查」面板：查看最近一次测试运行的真实输入与各节点输出。
 *
 * 位置：仅沉浸式编排（WorkflowEditor 的 immersive 分支）渲染，路由 `/workflows/:id`。
 * 依赖接口：`api/workflows` 的 FlowRun / FlowNode；数据全部来自 run（只读，不发起请求）。
 * 关键约束：
 * - 纯只读视图：不提供编辑或重跑入口，避免用户在查看历史值时误触发真实运行；
 * - 无运行记录时给出空态说明而不是空白面板；
 * - 切换运行（run.id 变化）时重置到"开始节点输入"，否则会停留在上一次运行的节点上；
 * - 节点已被删除时以 nodeId 兜底显示，保证输出仍可追溯。
 */
import { computed, shallowRef, watch } from 'vue'
import type { FlowNode, FlowRun } from '../../api/workflows'

const props = defineProps<{ run: FlowRun | null; nodes: FlowNode[] }>()
const active = shallowRef('inputs')
watch(() => props.run?.id, () => { active.value = 'inputs' })
const outputEntries = computed(() => Object.entries(props.run?.result?.outputs || {}))
const activeValue = computed(() => {
  if (active.value === 'inputs') return props.run?.inputs || {}
  return props.run?.result?.outputs?.[active.value] ?? {}
})
const activeTitle = computed(() => {
  if (active.value === 'inputs') return '开始节点输入'
  return props.nodes.find(node => node.id === active.value)?.title || active.value
})
function text(value: unknown) {
  try { return JSON.stringify(value, null, 2) }
  catch { return String(value) }
}
</script>

<template>
  <details class="wf-variable-inspect">
    <summary>变量检查</summary>
    <section>
      <header>
        <div><strong>变量检查</strong><small>查看最近一次测试运行的真实输入与节点输出</small></div>
        <button type="button" aria-label="关闭变量检查" @click="($event.currentTarget as HTMLElement).closest('details')!.open = false"><i class="fa-solid fa-xmark" /></button>
      </header>
      <div v-if="!run" class="wf-variable-empty"><i class="fa-solid fa-code-branch" /><strong>暂无运行变量</strong><span>测试运行后可在这里检查每个节点的真实值。</span></div>
      <div v-else class="wf-variable-body">
        <nav aria-label="运行变量">
          <button type="button" :class="{ active: active === 'inputs' }" @click="active = 'inputs'"><span>开始节点输入</span><small>系统</small></button>
          <button v-for="[nodeId] in outputEntries" :key="nodeId" type="button" :class="{ active: active === nodeId }" @click="active = nodeId">
            <span>{{ nodes.find(node => node.id === nodeId)?.title || nodeId }}</span><small>{{ nodeId }}</small>
          </button>
        </nav>
        <article><header><strong>{{ activeTitle }}</strong><small>{{ run.status }} · {{ run.durationMs || run.result?.durationMs || 0 }} ms</small></header><pre>{{ text(activeValue) }}</pre></article>
      </div>
    </section>
  </details>
</template>
