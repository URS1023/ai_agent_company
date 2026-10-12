<script setup lang="ts">
/**
 * 设备工作流页（路由 /alerts/devices/:deviceId/workflow[/section] 与 /quality/devices/:deviceId/workflow[/section]）。
 * 职责：按 deviceId + domain 打开（必要时创建）设备专属工作流，并把上下文（标题、返回路径、域、设备信息）交给工作流工作台。
 * 关键交互：加载中展示"首次进入会创建草稿"的说明（明确不会发布/运行/请求设备）；失败时提供"重试"与"返回设备"两个出口；
 *   成功后用 entry.workflow.id 作为 key 挂载 WorkflowWorkbench，切换设备时强制重建编辑器状态。
 * 权限：由路由 meta.permission = 'workflows.view' 控制。
 * 错误呈现：错误文案取自 composable 的 error（后端 message）。
 */
import { computed, toRef } from 'vue'
import { useRouter } from 'vue-router'
import type { BusinessDeviceDomain } from '../api/deviceWorkflows'
import { useDeviceWorkflowEntry } from '../composables/useDeviceWorkflowEntry'
import WorkflowWorkbench from '../components/workflows/WorkflowWorkbench.vue'
import '../styles/business-devices.css'

const props = defineProps<{ deviceId: string; domain: BusinessDeviceDomain }>()
const router = useRouter()
const domain = toRef(props, 'domain')
const { entry, loading, error, load } = useDeviceWorkflowEntry(toRef(props, 'deviceId'), domain)
const returnPath = computed(() => props.domain === 'alert' ? '/alerts' : '/quality')
const context = computed(() => entry.value ? {
  title: `${entry.value.device.name} · ${props.domain === 'alert' ? '预警工作流' : '质检工作流'}`,
  summary: `${entry.value.device.code}${entry.value.device.location ? ` · ${entry.value.device.location}` : ''}`,
  returnPath: returnPath.value,
  returnLabel: '返回设备',
  immersive: true,
  domain: props.domain,
  device: entry.value.device,
} : undefined)
</script>

<template>
  <div v-if="loading" class="business-page workflow-ui device-workflow-loading" role="status">
    <span class="device-workflow-spinner" aria-hidden="true" />
    <h1>正在打开设备工作流</h1>
    <p>首次进入会创建一份草稿，不会发布、运行或请求设备。</p>
  </div>
  <div v-else-if="error" class="business-page workflow-ui device-workflow-error" role="alert">
    <h1>无法打开设备工作流</h1>
    <p>{{ error }}</p>
    <div class="wf-actions"><button class="wf-primary" type="button" @click="load">重试</button><button type="button" @click="router.push(returnPath)">返回设备</button></div>
  </div>
  <WorkflowWorkbench v-else-if="entry" :key="entry.workflow.id" :id="String(entry.workflow.id)" :context="context" />
</template>
