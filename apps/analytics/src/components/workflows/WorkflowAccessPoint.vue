<script setup lang="ts">
/**
 * WorkflowAccessPoint —— 工作流「访问点」页，路由 `/workflows/:id/access-point`
 * （设备侧为 `/alerts|/quality/devices/:deviceId/workflow/access-point`）。
 *
 * 职责：把已发布版本的三种消费方式（Web 应用页、后端服务 API、被其他流程当子流程复用）
 * 以及触发器入口集中呈现给使用者，并提供地址复制。
 * 依赖接口：`api/request.can`（发布权限）、`api/workflows` 的 Workflow 类型；
 * 后端固定入口 `POST /api/v1/workflows/{id}/runs`，请求体需带 version / mode:"published" / idempotencyKey / inputs。
 * 关键约束：
 * - 未发布（status !== 'published' 或无 publishedRevision）时所有入口一律置灰并显示占位文案，
 *   不允许悄悄回退到草稿版本运行；
 * - 组件只做展示与复制，发布、运行均通过事件交给 WorkflowWorkbench，避免在此处触发写操作；
 * - Web 应用地址由父级传入的 pagePath 拼装，组件不自行推断当前路由。
 */
import { computed } from 'vue'
import { ElMessage } from 'element-plus'
import { can } from '../../api/request'
import type { Workflow } from '../../api/workflows'

const props = defineProps<{
  flow: Workflow
  pagePath: string
  device?: Record<string, any>
}>()
defineEmits<{ publish: []; run: []; design: [] }>()

const published = computed(() => props.flow.status === 'published' && !!props.flow.publishedRevision)
const serviceUrl = computed(() => `${location.origin}/api/v1/workflows/${props.flow.id}/runs`)
const webUrl = computed(() => `${location.origin}${props.pagePath}/run`)
const entryUrl = computed(() => `${location.origin}${props.pagePath}`)

/**
 * 复制文本的降级实现：非安全上下文（如内网 http）下 Clipboard API 不可用，
 * 用临时 textarea + execCommand 兜底，保证复制按钮始终可用。
 */
function fallbackCopy(value: string) {
  const area = document.createElement('textarea')
  area.value = value
  area.style.position = 'fixed'
  area.style.opacity = '0'
  document.body.appendChild(area)
  area.select()
  document.execCommand('copy')
  area.remove()
}
/** 复制并给出中文提示；失败路径不抛错，改用 fallbackCopy 后仍提示成功（复制内容不敏感）。 */
async function copyText(value: string, label: string) {
  try {
    if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(value)
    else fallbackCopy(value)
    ElMessage.success(`${label}已复制`)
  } catch {
    fallbackCopy(value)
    ElMessage.success(`${label}已复制`)
  }
}
</script>

<template>
  <main class="wf-workspace-surface wf-access-page" aria-labelledby="wf-access-title">
    <header class="wf-workspace-heading">
      <div><h1 id="wf-access-title">访问点</h1><p>发布后，可通过网页、服务 API、子流程和设备入口运行这个工作流。</p></div>
      <span class="wf-workspace-status" :class="{ published }"><i class="fa-solid" :class="published ? 'fa-circle-check' : 'fa-circle'" aria-hidden="true" />{{ published ? `已发布 v${flow.publishedRevision}` : '尚未发布' }}</span>
    </header>

    <section class="wf-access-grid">
      <article class="wf-access-card" :class="{ disabled: !published }">
        <header><span class="is-blue"><i class="fa-solid fa-window-maximize" aria-hidden="true" /></span><div><h2>Web 应用</h2><p>为业务用户提供简洁的正式运行页面。</p></div><b>{{ published ? '可用' : '未发布' }}</b></header>
        <div class="wf-access-card-body">
          <code>{{ published ? webUrl : '发布工作流后生成访问地址' }}</code>
          <div class="wf-access-actions">
            <button v-if="published" type="button" class="wf-primary" @click="$emit('run')">打开 Web 应用</button>
            <button v-if="published" type="button" @click="copyText(webUrl, 'Web 应用地址')">复制地址</button>
            <button v-else-if="can('workflows.publish')" type="button" class="wf-primary" @click="$emit('publish')">发布工作流</button>
          </div>
        </div>
      </article>

      <article class="wf-access-card" :class="{ disabled: !published }">
        <header><span class="is-violet"><i class="fa-solid fa-code" aria-hidden="true" /></span><div><h2>后端服务 API</h2><p>使用当前会话或授权令牌调用固定发布版本。</p></div><b>{{ published ? '可用' : '未发布' }}</b></header>
        <div class="wf-access-card-body">
          <div class="wf-access-endpoint"><em>POST</em><code>{{ serviceUrl }}</code></div>
          <p>请求体包含 <code>version</code>、<code>mode: "published"</code>、<code>idempotencyKey</code> 和 <code>inputs</code>。</p>
          <div class="wf-access-actions"><button type="button" :disabled="!published" @click="copyText(serviceUrl, 'API 地址')">复制 API 地址</button><a href="/docs" target="_blank" rel="noopener">查看接口文档 <i class="fa-solid fa-arrow-up-right-from-square" aria-hidden="true" /></a></div>
        </div>
      </article>

      <article class="wf-access-card" :class="{ disabled: !published }">
        <header><span class="is-green"><i class="fa-solid fa-puzzle-piece" aria-hidden="true" /></span><div><h2>工作流工具</h2><p>在其他流程中通过“子流程”节点复用此发布版本。</p></div><b>{{ published ? '可复用' : '未发布' }}</b></header>
        <div class="wf-access-card-body">
          <dl><div><dt>工作流 ID</dt><dd><code>{{ flow.id }}</code></dd></div><div><dt>发布修订</dt><dd>{{ flow.publishedRevision ? `v${flow.publishedRevision}` : '—' }}</dd></div></dl>
          <div class="wf-access-actions"><button type="button" :disabled="!published" @click="copyText(String(flow.id), '工作流 ID')">复制工作流 ID</button><button type="button" @click="$emit('design')">返回编排</button></div>
        </div>
      </article>

      <article class="wf-access-card" :class="{ disabled: !published }">
        <header><span class="is-orange"><i class="fa-solid fa-bolt" aria-hidden="true" /></span><div><h2>触发器</h2><p>{{ device ? '从当前设备入口或业务任务触发执行。' : '从业务入口或服务调用触发执行。' }}</p></div><b>{{ device ? '已绑定设备' : '按需调用' }}</b></header>
        <div class="wf-access-card-body">
          <dl v-if="device"><div><dt>设备</dt><dd>{{ device.name || device.code }}</dd></div><div><dt>设备编号</dt><dd><code>{{ device.code || device.id }}</code></dd></div></dl>
          <p v-else>本项目不额外创建无用途的定时模块；需要定时采集时，由任务中心调用上面的正式 API，并使用唯一键避免重复执行。</p>
          <div class="wf-access-actions"><button type="button" :disabled="!published" @click="$emit('run')">立即运行正式版本</button><button v-if="device" type="button" @click="copyText(entryUrl, '设备工作流入口')">复制设备入口</button></div>
        </div>
      </article>
    </section>
  </main>
</template>
