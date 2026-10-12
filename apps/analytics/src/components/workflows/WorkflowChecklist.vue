<script setup lang="ts">
/**
 * WorkflowChecklist —— 工具栏里的「配置检查」下拉（发布/测试运行前的结构校验结果）。
 *
 * 位置：WorkflowToolbar 的 compact 模式内；数据来自 `flow.validation`（后端 validate 接口写回 Workflow）。
 * 关键约束：
 * - 展开即触发一次校验（派发 validate 事件），busy 期间强制收起，防止并发校验叠加；
 * - 校验只覆盖图结构与节点配置；数据源、外部服务在运行时才会再次验证，因此文案不能承诺"可运行"；
 * - 点击问题项派发 select(nodeId) 以在画布上定位节点；无 nodeId 的结构级问题不可点击。
 */
import { ref } from 'vue'
import type { Workflow } from '../../api/workflows'

const props = defineProps<{ flow: Workflow; busy: boolean }>()
const emit = defineEmits<{ validate: []; select: [nodeId: string] }>()
const root = ref<HTMLDetailsElement | null>(null)

/** details 展开时请求校验；busy 中直接关闭面板，避免用户在旧结果上做发布决策。 */
function toggle(event: Event) {
  const details = event.currentTarget as HTMLDetailsElement
  if (!details.open) return
  if (props.busy) { details.open = false; return }
  emit('validate')
}

/** 定位问题节点后收起面板，使画布上的选中态可见。 */
function openIssue(nodeId?: string) {
  if (!nodeId) return
  emit('select', nodeId)
  if (root.value) root.value.open = false
}
</script>

<template>
  <details ref="root" class="wf-toolbar-checklist" @toggle="toggle">
    <summary class="wf-toolbar-icon wf-toolbar-check" :aria-disabled="busy" aria-label="检查配置" title="检查配置">
      <i class="fa-solid fa-list-check" aria-hidden="true" />
      <b v-if="flow.validation?.errors?.length" class="wf-toolbar-badge">{{ Math.min(flow.validation.errors.length, 99) }}</b>
    </summary>
    <section class="wf-validation" aria-live="polite">
      <header>
        <div>
          <h2>配置检查<span v-if="flow.validation?.errors?.length">（{{ flow.validation.errors.length }}）</span></h2>
          <p v-if="flow.validation?.errors?.length">发布或测试运行前，请完成以下节点配置。</p>
        </div>
        <button type="button" aria-label="关闭配置检查" @click="($event.currentTarget as HTMLElement).closest('details')!.open = false">
          <i class="fa-solid fa-xmark" aria-hidden="true" />
        </button>
      </header>
      <div v-if="busy" class="wf-checklist-empty" role="status">正在检查配置…</div>
      <div v-else-if="flow.validation?.errors?.length" class="wf-checklist-issues">
        <button
          v-for="(issue, index) in flow.validation.errors"
          :key="`${issue.nodeId || 'graph'}-${index}`"
          type="button"
          :disabled="!issue.nodeId"
          @click="openIssue(issue.nodeId)"
        >
          <i class="fa-solid fa-triangle-exclamation" aria-hidden="true" />
          <span>
            <strong>{{ issue.nodeId ? flow.graph.nodes.find(node => node.id === issue.nodeId)?.title || '节点配置' : '工作流结构' }}</strong>
            <small>{{ issue.message }}</small>
          </span>
          <i v-if="issue.nodeId" class="fa-solid fa-chevron-right" aria-hidden="true" />
        </button>
      </div>
      <div v-else class="wf-checklist-empty">
        <i class="fa-regular fa-square-check" aria-hidden="true" />
        <strong>所有问题均已解决</strong>
        <span>结构检查通过；数据源和外部服务将在运行时再次验证。</span>
      </div>
    </section>
  </details>
</template>
