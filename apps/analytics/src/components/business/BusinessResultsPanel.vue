<script setup lang="ts">
/**
 * 业务结果面板（预警/质检通用）：默认展示工作流产出的业务结果，展开后可见人工复核与历史记录。
 * 路由：无独立路由，由 BusinessDeviceWorkspace 在 /alerts、/quality 页面的结果弹窗内渲染。
 * 依赖接口：WorkflowBusinessResults（按 kind 取工作流业务结果）+ AlertWorkbench / QualityWorkbench（results-only 只读模式）。
 * 关键交互：预警域内置"预警报告/问题处置"页签；质检域直接渲染质检记录页签（只读）。
 * 约束：历史记录放在 details 折叠区（默认收起），避免主视图被大量历史数据淹没；本组件不发起写操作。
 */
import { shallowRef } from 'vue'
import type { BusinessDeviceDomain } from '../../api/deviceWorkflows'
import AlertWorkbench from '../alerts/AlertWorkbench.vue'
import QualityWorkbench from '../quality/QualityWorkbench.vue'
import WorkflowBusinessResults from './WorkflowBusinessResults.vue'

defineProps<{ domain: BusinessDeviceDomain }>()
const section = shallowRef<'reports' | 'problems'>('reports')
</script>

<template>
  <div class="workflow-ui">
    <WorkflowBusinessResults :kind="domain === 'alert' ? 'alert' : 'quality'" />
    <details><summary>人工复核与历史检测记录</summary>
    <template v-if="domain === 'alert'">
      <nav class="alert-tabs" aria-label="预警结果">
        <button type="button" :class="{ active: section === 'reports' }" @click="section = 'reports'">预警报告</button>
        <button type="button" :class="{ active: section === 'problems' }" @click="section = 'problems'">问题处置</button>
      </nav>
      <AlertWorkbench :section="section" results-only />
    </template>
    <QualityWorkbench v-else kind="inspections" results-only />
    </details>
  </div>
</template>
