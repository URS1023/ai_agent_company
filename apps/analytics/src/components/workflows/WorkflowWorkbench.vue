<script setup lang="ts">
/**
 * WorkflowWorkbench —— 工作流模块的外壳与状态中枢（列表页 + 沉浸式编排页 + 4 个 section 页）。
 *
 * 所在路由：
 * - `/workflows`（列表）与 `/workflows/:id`（编排）；
 * - `/workflows/:id/(access-point|logs|overview|run)`（访问点/日志/监测/Web 应用）；
 * - 设备入口 `/alerts/devices/:deviceId/workflow[/:section]`、`/quality/devices/:deviceId/workflow[/:section]`
 *   （由 DeviceWorkflowWorkspace 传入 context.domain / context.device）。
 * 依赖接口：`composables/useWorkflows`（加载、自动保存、撤销重做、发布、运行、AI 生成、导入导出等全部动作）、
 * `api/request.can`（权限）、`api/workflows.flowStatus`、`utils/topLevelNavigation.openTopLevel`（跨模块跳转）、
 * `utils/dateTime.formatRecordTime`；样式 `styles/workflows.css`。
 * 关键约束：
 * - 自动保存与冲突：所有编辑经 useWorkflows 防抖保存，界面必须常显保存状态（等待/保存中/失败/时间）；
 *   离开路由或切换工作流前先 flushDraft，仍未完成则让用户确认，避免丢草稿；
 * - 草稿与正式版本严格分离：`canvasRun` 只在运行修订等于当前草稿版本时才把运行结果叠加到画布，
 *   防止旧运行的节点状态被误读为当前图的执行结果；
 * - 发布、运行、写库确认等危险动作只在此处编排（含 Alt+R 快捷键），子组件只派发事件；
 * - 沉浸式布局：designer 模式下隐藏通用页头、改用 WorkflowAppRail 侧栏，并同步 document.title；
 * - URL 参数 execution 直达某次运行结果（需符合 26 位 ID 格式），application 参数决定返回路径。
 */
import { computed, toRef, watch, watchEffect } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import { can } from '../../api/request'
import { flowStatus } from '../../api/workflows'
import { useWorkflows } from '../../composables/useWorkflows'
import { openTopLevel } from '../../utils/topLevelNavigation'
import { formatRecordTime } from '../../utils/dateTime'
import WorkflowList from './WorkflowList.vue'
import WorkflowToolbar from './WorkflowToolbar.vue'
import WorkflowEditor from './WorkflowEditor.vue'
import WorkflowDebugPanel from './WorkflowDebugPanel.vue'
import WorkflowAiGenerator from './WorkflowAiGenerator.vue'
import WorkflowAccessPoint from './WorkflowAccessPoint.vue'
import WorkflowAppRail from './WorkflowAppRail.vue'
import WorkflowLogs from './WorkflowLogs.vue'
import WorkflowOverview from './WorkflowOverview.vue'
import WorkflowPublishedRunner from './WorkflowPublishedRunner.vue'
import '../../styles/workflows.css'
const props = defineProps<{
  id: string
  context?: {
    title: string
    summary: string
    returnPath: string
    returnLabel: string
    immersive?: boolean
    domain?: 'alert' | 'quality'
    device?: Record<string, any>
  }
}>()
const route = useRoute()
const router = useRouter()
const applicationId = computed(() => typeof route.query.application === 'string' && /^[A-Z0-9]{26}$/.test(route.query.application) ? route.query.application : '')
const returnPath = computed(() => props.context?.returnPath || (applicationId.value ? `/applications?id=${encodeURIComponent(applicationId.value)}` : '/workflows'))
const returnLabel = computed(() => props.context?.returnLabel || (applicationId.value ? '返回应用详情' : '返回流程列表'))
const { rows, flow, catalog, sources, resources, loading, busy, saving, lastSavedAt, saveError, error, generationNotice, dirty, keyword, offset, selected, editable, fields,
  canUndo, canRedo,
  result, resultOpen, resultRefreshing, refreshResult, runOpen, runInputs, runMode, confirmWrites, aiOpen, historyOpen, history, revisions, historyOffset,
  load, updateGraph, undoGraph, redoGraph, updateNode, upsertSource, updateMeta, save, flushDraft, validate, status, remove, removeNode, removeNodeImmediate, openHistory, showResult, restore, prepareRun, run, resumeRun, cancelRun, generateAi, exportFlow, importFlow } = useWorkflows(toRef(props, 'id'))
watch(() => !loading.value && flow.value?.id === props.id ? route.query.execution : '', execution => {
  if (typeof execution !== 'string' || !/^[A-Z0-9]{26}$/.test(execution)) return
  selected.value = ''
  void showResult({ id: execution })
}, { immediate: true })
/** 自动保存状态文案：失败/保存中/待保存/已保存四种态必须可区分，用户据此判断能否安全离开。 */
const autoSaveText = computed(() => {
  if (saveError.value) return '自动保存失败'
  if (saving.value) return '正在自动保存…'
  if (dirty.value) return '等待自动保存…'
  if (!lastSavedAt.value) return '等待首次自动保存'
  return `自动保存 ${new Date(lastSavedAt.value).toLocaleTimeString('zh-CN', { hour12: false })}`
})
const publishText = computed(() => flow.value?.publishedRevision ? `已发布 v${flow.value.publishedRevision}` : '未发布')
const designer = computed(() => !!props.id)
/** 仅当运行修订与当前草稿版本一致时才把运行结果叠加到画布，避免旧运行状态被误认为当前图的结果。 */
const canvasRun = computed(() => result.value?.revision === flow.value?.version ? result.value : null)
type WorkspaceSection = 'workflow' | 'access-point' | 'logs' | 'overview' | 'run'
const currentSection = computed<WorkspaceSection>(() => {
  const value = String(route.params.section || '')
  return ['access-point', 'logs', 'overview', 'run'].includes(value) ? value as WorkspaceSection : 'workflow'
})
const workspacePath = computed(() => route.path.replace(/\/(?:access-point|logs|overview|run)$/, ''))
const designerTitle = computed(() => props.context?.title || flow.value?.name || '工作流设计')
const designerSummary = computed(() => props.context?.summary || flow.value?.description || '可视化工作流')
watchEffect(() => {
  if (!designer.value || !flow.value) return
  const label = ({ workflow: '编排', 'access-point': '访问点', logs: '日志', overview: '监测', run: 'Web 应用' } as Record<WorkspaceSection, string>)[currentSection.value]
  document.title = `${label} · ${flow.value.name}`
})
/**
 * 离开前的守卫：先把待保存草稿冲刷到后端，仍失败时由用户确认是否放弃。
 * @returns true 允许离开；false 表示用户选择留在当前页。
 */
const confirmLeave = async () => {
  if (!dirty.value) return true
  await flushDraft(false)
  return !dirty.value || window.confirm('自动保存尚未完成，确定离开？')
}
onBeforeRouteLeave(confirmLeave)
onBeforeRouteUpdate((to, from) => to.params.id === from.params.id || confirmLeave())
function page(value: number) { offset.value = value; void load() }
async function leaveDesigner(path = returnPath.value) {
  if (await confirmLeave()) openTopLevel(path)
}
function openDesigner(id = 'new') { openTopLevel(`/workflows/${id}`) }
function navigateSection(section: WorkspaceSection) {
  const path = section === 'workflow' ? workspacePath.value : `${workspacePath.value}/${section}`
  if (route.path !== path) void router.push({ path, query: route.query })
}
async function openRun() {
  if (designer.value) { selected.value = ''; resultOpen.value = false }
  await prepareRun()
}
/**
 * 运行正式发布版本：先确保草稿已保存（运行按发布修订执行，但仍需避免带着未保存的脏状态离开），
 * 并强制关闭写库确认（正式运行不携带调试期的写库授权勾选）。
 */
async function runPublished() {
  if ((dirty.value || saving.value) && !await flushDraft(false)) return
  runMode.value = 'published'
  confirmWrites.value = false
  await run()
}
function closeDebug() { runOpen.value = false; resultOpen.value = false }
function restartDebug() { resultOpen.value = false; runOpen.value = true }
async function showHistoryResult(item: Parameters<typeof showResult>[0]) {
  await showResult(item)
  if (designer.value) historyOpen.value = false
}
/** Alt+R 快捷键打开测试运行；只响应 Alt 组合，避免与输入框内的普通按键冲突。 */
function shortcut(event: KeyboardEvent) {
  if (!event.altKey || event.key.toLocaleLowerCase() !== 'r') return
  event.preventDefault()
  void openRun()
}
</script>
<template>
  <div class="business-page workflow-ui" :class="{ 'wf-design-page': designer, 'wf-immersive': designer, 'wf-app-shell': designer, 'wf-debug-open': designer && (runOpen || resultOpen), [`wf-workspace-section--${currentSection}`]: designer }" @keydown.capture="shortcut">
    <WorkflowAppRail v-if="designer" :active="currentSection" :title="designerTitle" :domain="context?.domain" :return-label="returnLabel" @navigate="navigateSection" @leave="leaveDesigner()" />
    <header v-if="designer && currentSection === 'workflow'" class="wf-immersive-header">
      <button class="wf-workbench-back" type="button" :aria-label="returnLabel" :title="returnLabel" @click="leaveDesigner()"><i class="fa-solid fa-arrow-left" aria-hidden="true" /></button>
      <span class="wf-workbench-icon" :class="`wf-workbench-icon--${context?.domain || 'workflow'}`" aria-hidden="true"><i class="fa-solid" :class="context?.domain === 'alert' ? 'fa-bell' : context?.domain === 'quality' ? 'fa-shield-halved' : 'fa-diagram-project'" /></span>
      <div class="wf-workbench-identity">
        <div>
          <input v-if="flow && !context" class="wf-workbench-name" :value="flow.name" :disabled="!editable" maxlength="200" aria-label="工作流名称" @input="updateMeta({ name: ($event.target as HTMLInputElement).value })" />
          <h1 v-else>{{ designerTitle }}</h1>
          <span v-if="flow" class="wf-save-state">草稿 v{{ flow.version }}</span>
        </div>
        <p>{{ designerSummary }}</p>
      </div>
      <span class="wf-autosave-state" :class="{ 'is-saving': saving || dirty, 'is-error': saveError }" :title="saveError || undefined">{{ autoSaveText }} · {{ publishText }}</span>
      <WorkflowToolbar v-if="flow" compact :flow="flow" :busy="busy" :saving="saving" :dirty="dirty" @save="save" @run="openRun" @publish="status(true)" @disable="status(false)" @validate="validate" @select-issue="selected = $event" @generate="aiOpen = true" @history="openHistory" @export="exportFlow" />
    </header>
    <div v-if="!designer" class="wf-heading"><div><h1>可视化工作流</h1><p>管理可复用流程、发布版本和实际运行记录。无需进入 AI 对话。</p></div><div class="wf-actions"><button v-if="can('workflows.create')" class="wf-primary" @click="openDesigner()">新建工作流</button><label v-if="can('workflows.create')" class="wf-import">导入流程<input type="file" accept=".json" :disabled="busy" @change="($event.target as HTMLInputElement).files?.[0] && importFlow(($event.target as HTMLInputElement).files![0])" /></label></div></div>
    <p v-if="error && !(resultOpen || runOpen)" class="wf-error" role="alert">{{ error }}</p><section v-if="generationNotice" class="wf-generation-notice" role="status"><strong>AI 草稿已安全生成</strong><span>{{ generationNotice }}</span></section><p v-if="loading" class="wf-designer-loading" role="status">正在加载流程数据…</p>
    <template v-if="!id"><form class="wf-actions wf-list-filter" @submit.prevent="page(0)"><label>流程名称<input v-model="keyword" placeholder="输入关键词查询" /></label><button type="submit" :disabled="loading">查询</button><button type="button" :disabled="loading" @click="load">刷新</button></form><WorkflowList :rows="rows" :loading="loading" :offset="offset" @open="openDesigner(String($event.id))" @remove="remove" @page="page" /></template>
    <template v-else-if="flow && currentSection === 'workflow'"><WorkflowEditor :flow="flow" :catalog="catalog" :sources="sources" :resources="resources" :editable="editable" :selected="selected" :fields="fields" :run="canvasRun" :can-undo="canUndo" :can-redo="canRedo" :domain="context?.domain" :device="context?.device" :immersive="designer" @graph="updateGraph" @undo="undoGraph" @redo="redoGraph" @node="updateNode" @meta="updateMeta" @select="selected = $event" @remove-node="removeNode" @delete-node="removeNodeImmediate" @source-saved="upsertSource" @run="openRun" /></template>
    <WorkflowAccessPoint v-else-if="flow && currentSection === 'access-point'" :flow="flow" :page-path="workspacePath" :device="context?.device" @publish="status(true)" @run="navigateSection('run')" @design="navigateSection('workflow')" />
    <WorkflowLogs v-else-if="flow && currentSection === 'logs'" :flow="flow" @open="showResult" />
    <WorkflowOverview v-else-if="flow && currentSection === 'overview'" :flow="flow" />
    <WorkflowPublishedRunner v-else-if="flow && currentSection === 'run'" v-model="runInputs" :flow="flow" :busy="busy" :device="context?.device" @run="runPublished" @back="navigateSection('access-point')" @design="navigateSection('workflow')" />
    <p v-if="busy && currentSection === 'workflow'" role="status" class="wf-note">操作进行中。已发送的数据请求不会因关闭页面而撤回，请勿重复新建相同运行。</p>
    <WorkflowDebugPanel v-if="designer && (runOpen || resultOpen) && flow" v-model:inputs="runInputs" v-model:mode="runMode" v-model:confirm-writes="confirmWrites" :flow="flow" :busy="busy" :error="error" :domain="context?.domain" :device="context?.device" :result="result" :show-result="resultOpen" :refreshing="resultRefreshing" @close="closeDebug" @restart="restartDebug" @run="run" @refresh="refreshResult" @resume="resumeRun" @cancel="cancelRun" />
    <el-dialog v-model="aiOpen" title="AI 生成工作流草稿" width="min(820px, 95vw)" :close-on-click-modal="false"><div class="workflow-ui"><p v-if="error" role="alert" class="wf-error">{{ error }}</p><WorkflowAiGenerator v-if="flow" :busy="busy" :domains="flow.domains" :sources="sources" @generate="generateAi" @source-saved="upsertSource" /></div></el-dialog>
    <el-dialog v-model="historyOpen" title="版本与运行记录" width="min(1000px, 95vw)"><div class="workflow-ui"><p v-if="error" role="alert" class="wf-error">{{ error }}</p><h2>运行记录</h2><div class="wf-table-wrap"><table class="wf-table"><thead><tr><th>开始时间</th><th>修订</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="item in history" :key="item.id"><td>{{ formatRecordTime(item.createTime) }}</td><td>v{{ item.revision }}</td><td>{{ flowStatus[item.status] }}</td><td><button :disabled="busy" @click="showHistoryResult(item)">查看结果</button></td></tr><tr v-if="!history.length"><td colspan="4">没有可访问的运行记录。</td></tr></tbody></table></div><div class="wf-actions"><button :disabled="busy || !historyOffset" @click="historyOffset -= 30; openHistory()">上一页运行</button><button :disabled="busy || history.length < 30" @click="historyOffset += 30; openHistory()">下一页运行</button><button :disabled="busy" @click="openHistory">刷新记录</button></div><h2>历史修订（最近 100 个）</h2><div class="wf-table-wrap"><table class="wf-table"><thead><tr><th>修订</th><th>变更</th><th>时间</th><th>操作</th></tr></thead><tbody><tr v-for="item in revisions" :key="item.revision"><td>v{{ item.revision }}</td><td>{{ item.reason }}</td><td>{{ formatRecordTime(item.createTime) }}</td><td><button v-if="can('workflows.edit')" :disabled="busy || !item.available" @click="restore(item.revision)">恢复为新草稿</button></td></tr></tbody></table></div></div></el-dialog>
  </div>
</template>
