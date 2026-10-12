<script setup lang="ts">
/**
 * WorkflowPublishedRunner —— 工作流「Web 应用」页，路由 `/workflows/:id/run`
 * （设备侧 `/alerts|/quality/devices/:deviceId/workflow/run`）。
 *
 * 职责：面向业务用户的最简运行入口 —— 只填写开始节点变量并运行**已发布**修订，
 * 不暴露草稿、节点配置或调试开关。
 * 依赖接口：`api/workflows` 的 Workflow / StartInputVariable；输入表单由 WorkflowStartInputForm 提供
 * 并回传 validity；运行本身由父级（WorkflowWorkbench.runPublished）以 mode=published 提交。
 * 关键约束：
 * - 未发布（status !== 'published' 或无 publishedRevision）时禁止运行：按钮禁用且不派发 run 事件，
 *   不得回退到草稿版本；
 * - 必须等 WorkflowStartInputForm 报 validity 为 true 才允许提交，避免用空必填项发起真实运行；
 * - 支持通过 URL 参数 windowStart/windowEnd/windowAfterKey 预填采集窗口（来自上一次运行的
 *   collectionWindow 续跑链接），时间需把空格换成 'T' 以符合 ISO 格式；
 * - 正式运行可能访问已授权的数据源/外部服务，界面需提示幂等（相同请求用唯一键避免重复执行）。
 */
import { computed, shallowRef, onMounted, toRef } from 'vue'
import { useRoute } from 'vue-router'
import type { StartInputVariable, Workflow } from '../../api/workflows'
import WorkflowStartInputForm from './WorkflowStartInputForm.vue'
import { usePublishedWorkflow } from '../../composables/usePublishedWorkflow'

const props = defineProps<{ flow: Workflow; modelValue: string; busy: boolean; device?: Record<string, any> }>()
const emit = defineEmits<{ 'update:modelValue': [value: string]; run: []; back: []; design: [] }>()
const route = useRoute()
onMounted(() => {
  if (typeof route.query.windowStart === 'string' && typeof route.query.windowEnd === 'string') {
    emit('update:modelValue', JSON.stringify({ windowStart: route.query.windowStart.replace(' ', 'T'),
      windowEnd: route.query.windowEnd.replace(' ', 'T'), windowAfterKey: Number(route.query.windowAfterKey || 0) }))
  }
})
const published = computed(() => props.flow.status === 'published' && !!props.flow.publishedRevision)
const { contract, loading: contractLoading, error: contractError, ready: contractReady } = usePublishedWorkflow(toRef(props, 'flow'))
const inputsValid = shallowRef(false)
/** 输入表单来自发布修订，当前草稿即使修改开始节点也不会改变正式运行契约。 */
const startVariables = computed<StartInputVariable[]>(() => (contract.value?.graph.nodes.find(node => node.kind === 'start')?.config.variables || []))
const example = computed(() => props.device?.id
  ? JSON.stringify({ deviceId: props.device.id, deviceCode: props.device.code || '' }, null, 2)
  : JSON.stringify({ query: '请输入本次运行参数' }, null, 2))
</script>

<template>
  <main class="wf-workspace-surface wf-run-page" aria-labelledby="wf-run-title">
    <header class="wf-run-header"><button type="button" aria-label="返回访问点" @click="$emit('back')"><i class="fa-solid fa-arrow-left" aria-hidden="true" /></button><span><i class="fa-solid fa-diagram-project" aria-hidden="true" /></span><div><h1 id="wf-run-title">{{ flow.name }}</h1><p>Web 应用 · 正式发布版本</p></div><button type="button" @click="$emit('design')">打开编排</button></header>
    <section class="wf-run-card">
      <header><div><h2>运行工作流</h2><p>输入会发送给开始节点，并固定执行当前发布修订。</p></div><b :class="{ published }">{{ published ? `发布修订 v${flow.publishedRevision}` : '尚未发布' }}</b></header>
      <div v-if="device" class="wf-run-device"><span>当前设备</span><strong>{{ device.name || device.code }}</strong><small>{{ device.code }}{{ device.location ? ` · ${device.location}` : '' }}</small></div>
      <p v-if="contractLoading" role="status">正在加载发布版本的输入字段…</p>
      <p v-if="contractError" class="wf-error" role="alert">{{ contractError }}</p>
      <WorkflowStartInputForm :model-value="modelValue" :variables="startVariables" :disabled="busy || !contractReady" @update:model-value="$emit('update:modelValue', $event)" @validity="inputsValid = $event" />
      <div class="wf-run-example"><button type="button" :disabled="busy || !contractReady" @click="$emit('update:modelValue', example)">填入示例</button><small>正式运行可能访问已授权的数据源或外部服务；相同请求使用唯一键避免重复执行。</small></div>
      <footer><button type="button" :disabled="busy" @click="$emit('back')">返回</button><button type="button" class="wf-primary" :disabled="busy || !contractReady || !inputsValid" @click="contractReady && inputsValid && $emit('run')"><i class="fa-solid" :class="busy ? 'fa-spinner fa-spin' : 'fa-play'" aria-hidden="true" />{{ busy ? '正在运行' : '运行正式版本' }}</button></footer>
    </section>
  </main>
</template>
