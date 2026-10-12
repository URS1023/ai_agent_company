<script setup lang="ts">
/**
 * WorkflowToolbar —— 工作流编排/工作台顶部的操作工具栏。
 *
 * 所在路由：`/workflows/:id` 及 `/workflows/:id/(access-point|logs|overview|run)`（沉浸式外壳），
 * 设备侧由 `/alerts/devices/:deviceId/workflow`、`/quality/devices/:deviceId/workflow` 进入；
 * 实际渲染方为 WorkflowWorkbench。
 * 依赖接口：`api/request.can`（权限判定）、`api/workflows.flowStatus`（状态中文映射）、
 * WorkflowChecklist（发布前配置检查下拉）。
 * 关键约束：
 * - 本组件只派发事件，不直接调用后端；保存、发布、运行时机由 WorkflowWorkbench/useWorkflows 决定；
 * - 草稿版本与正式发布版本必须分开呈现，按钮禁用态同时受 busy/saving/dirty 与 flow.status 影响，
 *   避免在自动保存未完成时发布出与画布不一致的版本；
 * - compact 模式（沉浸式外壳内）收敛为图标按钮并展示 Alt+R 快捷键，非 compact 用于列表页/内嵌。
 */
import { can } from '../../api/request'
import { flowStatus, type Workflow } from '../../api/workflows'
import WorkflowChecklist from './WorkflowChecklist.vue'

withDefaults(defineProps<{ flow: Workflow; busy: boolean; dirty: boolean; saving?: boolean; compact?: boolean }>(), { compact: false, saving: false })
defineEmits<{ save: []; run: []; publish: []; disable: []; validate: []; generate: []; history: []; export: []; selectIssue: [nodeId: string] }>()
</script>

<template>
  <div class="wf-toolbar" :class="{ 'wf-toolbar--compact': compact }">
    <div class="wf-version-state" aria-live="polite">
      <strong>{{ flow.version ? '编辑草稿 v' + flow.version : '新草稿 · 尚未保存' }}</strong>
      <span :class="{ 'wf-unsaved': dirty }">{{ dirty ? '有未保存修改' : flow.id ? '修改已保存' : '请先保存草稿' }}</span>
      <span>{{ flow.publishedRevision ? '正式 v' + flow.publishedRevision + ' · ' + flowStatus[flow.status] : '尚未发布正式版本' }}</span>
    </div>
    <div class="wf-actions wf-main-actions" aria-label="流程操作">
      <button v-if="!compact && can(flow.id ? 'workflows.edit' : 'workflows.create')" class="wf-toolbar-save" :disabled="busy || saving || (!!flow.id && !dirty)" @click="$emit('save')">保存草稿</button>
      <button v-if="can('workflows.execute')" class="wf-primary wf-toolbar-run" :disabled="busy || !flow.id" :title="!flow.id ? '等待自动保存完成' : '测试运行工作流（Alt+R）'" @click="$emit('run')"><i class="fa-solid fa-play" aria-hidden="true" /><span>{{ compact ? '测试运行' : '运行' }}</span><kbd v-if="compact">Alt R</kbd></button>
      <WorkflowChecklist v-if="compact" :flow="flow" :busy="busy" @validate="$emit('validate')" @select="$emit('selectIssue', $event)" />
      <button v-if="compact" type="button" class="wf-toolbar-icon" :disabled="busy || !flow.id" title="版本与运行" aria-label="版本与运行" @click="$emit('history')"><i class="fa-regular fa-clock" aria-hidden="true" /><span class="wf-visually-hidden">版本与运行</span></button>
      <div v-if="can('workflows.publish')" class="wf-toolbar-publish-group">
        <button class="wf-toolbar-publish" :disabled="busy || !flow.id || (flow.status === 'published' && flow.publishedRevision === flow.version && !dirty)" title="自动保存后发布当前草稿" @click="$emit('publish')"><i v-if="compact" class="fa-solid fa-cloud-arrow-up" aria-hidden="true" />发布</button>
        <details v-if="compact" class="wf-publish-actions">
          <summary aria-label="发布选项" title="发布选项"><i class="fa-solid fa-chevron-down" aria-hidden="true" /></summary>
          <div class="wf-more-menu" @click="($event.currentTarget as HTMLElement).closest('details')!.open = false">
            <button :disabled="busy || !flow.id" @click="$emit('publish')">发布当前草稿</button>
            <button v-if="flow.status === 'published'" :disabled="busy" @click="$emit('disable')">停用正式版本</button>
          </div>
        </details>
      </div>
      <details class="wf-more-actions" @keydown.esc="($event.currentTarget as HTMLDetailsElement).open = false">
        <summary :aria-label="compact ? '更多操作' : undefined"><span>{{ compact ? '' : '更多操作' }}</span><i v-if="compact" class="fa-solid fa-ellipsis" aria-hidden="true" /></summary>
        <div class="wf-more-menu" @click="($event.currentTarget as HTMLElement).closest('details')!.open = false">
          <button v-if="compact && can(flow.id ? 'workflows.edit' : 'workflows.create')" :disabled="busy || saving || (!!flow.id && !dirty)" @click="$emit('save')">保存草稿</button>
          <button v-if="!compact" :disabled="busy" @click="$emit('validate')">检查配置</button>
          <button v-if="can(flow.id ? 'workflows.edit' : 'workflows.create') && can('workflows.create')" :disabled="busy" @click="$emit('generate')">AI 生成</button>
          <button v-if="!compact" :disabled="busy || !flow.id" @click="$emit('history')">版本与运行</button>
          <button v-if="can('workflows.export')" :disabled="busy || !flow.id" :title="dirty ? '导出的是已保存的定义，请先保存当前修改' : '下载已保存的流程定义'" @click="$emit('export')">导出定义</button>
          <button v-if="can('workflows.publish') && flow.status === 'published'" :disabled="busy || dirty" @click="$emit('disable')">停用</button>
        </div>
      </details>
    </div>
  </div>
</template>
