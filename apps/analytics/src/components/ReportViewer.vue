<script setup lang="ts">
/**
 * 大屏/报表查看页（路由 /report/view/:id，权限 dashboards.view）。
 * 职责：加载报表内容并转换为可渲染的大屏模型，渲染查看舞台与工具栏，支持全屏、定时刷新与组件钻取。
 * 依赖接口：api/report.js 的 reportApi.getById；utils/jimuReportAdapter 做"报表内容 → 大屏模型"转换；
 *   钻取由 useReportDrilldown 负责（只提交已保存的报表/组件标识，不接受前端拼写 SQL）。
 * 关键交互：provide('report-data-context', { readOnly: true, ... }) 把只读上下文与刷新信号下发给所有组件（禁止在此域内写库）；
 *   ?workspace_id= 只在格式合法时透传（用于跨工作区分享链接），非法值直接忽略；
 *   刷新分两类：定时按 refreshSeconds 递增 refreshVersion（组件自行重取），手动刷新走同一信号。
 * 约束：加载用 loadSequence 作代际，路由 id 变化时旧响应不得覆盖新报表；错误文案对"记录不存在/越权"给出可操作提示。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, provide, shallowRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import reportApi from '@/api/report'
import { can } from '@/api/request'
import { applyReportIdentity, toJimuScreen } from '@/utils/jimuReportAdapter'
import { chatReportSnapshot } from '@/utils/chatReportSnapshot'
import { useReportDrilldown } from '@/composables/useReportDrilldown'
import DashboardViewerStage from './report-viewer/DashboardViewerStage.vue'
import DashboardViewerToolbar from './report-viewer/DashboardViewerToolbar.vue'
import JimuDrilldownPanel from './jimu-screen/JimuDrilldownPanel.vue'

const route = useRoute()
const router = useRouter()
const loading = shallowRef(true)
const error = shallowRef('')
const screen = shallowRef<Record<string, any>>(toJimuScreen())
const reportId = computed(() => String(route.params.id || ''))
const referenceSampleCount = computed(() => (screen.value.widgets || []).filter((widget: Record<string, any>) =>
  widget.referenceSkin && widget.config?.referenceSample && widget.config?.dataStatus === 'sample').length)
const chatPreview = computed(() => route.query.chat_preview === '1')
const missingSnapshots = shallowRef(0)
const workspaceId = computed(() => {
  const value = String(route.query.workspace_id || '')
  return /^[A-Za-z0-9_-]{1,128}$/.test(value) ? value : ''
})
const refreshSeconds = shallowRef(0)
const refreshVersion = shallowRef(0)
const fullscreen = shallowRef(Boolean(document.fullscreenElement))
const { drilldownState, openDrilldown, closeDrilldown, exportDrilldown } = useReportDrilldown(reportId)

// 只读数据上下文：readOnly=true 让所有组件取数走只读路径，refreshVersion 变更即触发组件重取。
provide('report-data-context', { id: reportId, readOnly: true, refreshSeconds, refreshVersion })
let loadSequence = 0

/** 只有同源聊天宿主接收加载结果，iframe 文档就绪不等于报表已成功渲染。 */
function previewStatus(status: 'ready' | 'error', message = '') {
  if (chatPreview.value && window.parent !== window) {
    window.parent.postMessage({ type: 'cloud-brain-chat-preview', status, message, reportId: reportId.value }, location.origin)
  }
}

function scopedTarget(path: string) {
  return workspaceId.value ? { path, query: { workspace_id: workspaceId.value } } : { path }
}

/**
 * 把后端错误转成用户能行动的提示：越权/记录不存在时区分"缺工作区参数"与"无权限"两种情形，
 * 其余错误原样透出（避免吞掉真正的后端原因）。
 */
function friendlyError(cause: unknown) {
  const message = (cause as Error)?.message || '报表加载失败'
  if (/记录不存在或不在当前操作的数据范围内/.test(message)) {
    return workspaceId.value
      ? '当前账号没有访问这份报表所属工作区的权限。请确认账号后重试。'
      : '这条旧链接缺少工作区信息。请从报表列表重新打开，或重新登录后重试。'
  }
  return message
}

/**
 * 加载报表：GET /api/v1/reports/{id}，转换为大屏模型后写入 screen。
 * 约束：用 loadSequence 作请求代际（路由 id 快速切换时丢弃旧响应）；内容为空直接报错，不渲染空画布；
 *   report.templateId 是目录资源标识、canvas.templateId 是渲染器标识，二者语义不同不可互换。
 */
async function load() {
  const current = ++loadSequence
  loading.value = true
  error.value = ''
  closeDrilldown()
  try {
    const response = await reportApi.getById(reportId.value)
    const report = response.data
    if (!report?.content) throw new Error('报表内容为空')
    if (current !== loadSequence) return
    const nextScreen = applyReportIdentity(toJimuScreen(report.content, {
      preserveSourceWidgets: Boolean(report.lineage?.preserveSourceWidgets),
      snapshotDescription: report.description || ''
    }), report.reportName)
    if (chatPreview.value && !nextScreen.widgets?.length) throw new Error('这份大屏没有可预览的组件，请独立打开检查报表内容。')
    // report.templateId identifies a catalog resource; canvas.templateId is
    // the visual renderer identity.  Replacing the latter breaks template-
    // specific backgrounds for reports created from a source template.
    nextScreen.templateId = nextScreen.templateId || ''
    nextScreen.templateResourceId = report.templateId || ''
    nextScreen.templateMode = report.templateMode || nextScreen.templateMode || 'none'
    // 聊天中仅渲染生成时保存的结果；独立查看仍沿用原有实时刷新行为。
    const snapshot = chatPreview.value ? chatReportSnapshot(nextScreen) : null
    screen.value = snapshot?.screen || nextScreen
    missingSnapshots.value = snapshot?.missing || 0
    refreshSeconds.value = chatPreview.value ? 0 : Number(report.refreshSeconds || 0)
    document.title = (screen.value.name || '数字大屏') + ' · 智慧云脑 Cloud Brain'
  } catch (cause) {
    if (current === loadSequence) {
      error.value = friendlyError(cause)
      previewStatus('error', error.value)
    }
  } finally {
    if (current === loadSequence) {
      loading.value = false
      await nextTick()
      if (!error.value) previewStatus('ready')
    }
  }
}

async function toggleFullscreen() {
  try {
    if (document.fullscreenElement) await document.exitFullscreen()
    else await document.documentElement.requestFullscreen()
  } catch {
    error.value = '浏览器未允许全屏，请检查站点权限后重试。'
  }
}

function backToReports() {
  void router.push('/dashboards')
}

function editReport() {
  void router.push(scopedTarget('/report/designer/' + reportId.value))
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') closeDrilldown()
}

function handleFullscreenChange() {
  fullscreen.value = Boolean(document.fullscreenElement)
}

watch(() => [reportId.value, chatPreview.value], () => { void load() })
onMounted(() => {
  void load()
  window.addEventListener('keydown', handleKeydown)
  document.addEventListener('fullscreenchange', handleFullscreenChange)
})
onBeforeUnmount(() => {
  loadSequence += 1
  window.removeEventListener('keydown', handleKeydown)
  document.removeEventListener('fullscreenchange', handleFullscreenChange)
})
</script>

<template>
  <main class="report-viewer">
    <DashboardViewerStage
      :screen="screen"
      :loading="loading"
      :error="error"
      @retry="load"
      @back="backToReports"
      @drilldown="!chatPreview && openDrilldown($event)"
    />
    <DashboardViewerToolbar
      v-if="!chatPreview"
      :can-edit="can('dashboards.edit')"
      :loading="loading"
      :has-error="Boolean(error)"
      :refresh-seconds="refreshSeconds"
      :fullscreen="fullscreen"
      :reference-sample-count="referenceSampleCount"
      @back="backToReports"
      @refresh="refreshVersion++"
      @edit="editReport"
      @fullscreen="toggleFullscreen"
    />
    <JimuDrilldownPanel
      v-if="!chatPreview && drilldownState"
      v-bind="drilldownState"
      @close="closeDrilldown"
      @export="exportDrilldown"
    />
    <p v-if="chatPreview && missingSnapshots && !loading && !error" class="snapshot-note" role="status">
      {{ missingSnapshots }} 个组件未保存数据，其余显示已保存快照。
    </p>
  </main>
</template>

<style scoped>
.report-viewer { position: fixed; inset: 0; overflow: hidden; color: #edfaff; background: #020713; }
.snapshot-note { position: absolute; right: 10px; bottom: 6px; left: 10px; margin: 0; padding: 6px 10px; color: #bdd0e4; background: #061225e8; font-size: 11px; text-align: center; }
</style>
