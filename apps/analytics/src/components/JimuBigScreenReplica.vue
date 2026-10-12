<script setup>
/**
 * JimuBigScreenReplica —— 大屏/报表设计器主容器（编辑、AI 生成、版本、下钻、草稿保存的中枢）。
 *
 * 所在路由：`/report/designer/:id?`（`/jimu-screen` 重定向到此）。无 id 为新建（需 dashboards.create），
 * 有 id 为编辑现有报表（需 dashboards.edit）；只读用户会被路由守卫改送到 `/report/view/:id`。
 * 依赖接口：`api/request`（get / can / scopedKey）、`api/report.reportApi`（报表读写与版本）、
 * `utils/jimuReportAdapter`（报表结构 ↔ 大屏页/组件模型互转）、`utils/reportAiContext`（构造 AI 请求）、
 * `utils/reportPromptLibrary`、`utils/dashboardAdjustment`（解析并应用"换成绿色科技风"这类调整指令）、
 * `composables/useReportDrilldown`（下钻面板与导出）、`composables/useDraftCheckpoint`（未保存变更检测），
 * 子组件 JimuTopToolbar / JimuLayerPanel / JimuCanvas / JimuPropertyPanel / JimuWidgetRenderer /
 * JimuDrilldownPanel / ReportPromptLibraryPanel / GenerationStatusPanel / ReportRevisionDialog，
 * 组件实例由 `jimu-screen/widgetFactory.createCanvasWidget` 创建。
 * 关键约束：
 * - 视觉/模板锁定：页面带 templateStyleLocked（来自模板来源）且当前用户无编辑权限时，
 *   isTemplateStyleLocked 为真 —— 此时只允许查看与运行，禁止改样式；有权限的用户可自定义自己的实例，
 *   但不能改动原模板本身（模板保真只在生成阶段生效）；
 * - 撤销/重做：用快照字符串（snapshot）记录状态，交互开始/结束（beginInteraction/endInteraction）把
 *   连续拖拽合并为一次历史记录，HISTORY_LIMIT 限制栈深；interactionSnapshot 用于判断"当前是否已回到基线"；
 * - 离开守卫：有未保存变更时弹窗确认，保存成功且期间无新改动才放行；另注册 beforeunload 兜底；
 * - AI 生成是长流程：生成期间展示 GenerationStatusPanel，失败只提示错误不自动重试；
 * - 组件坐标/尺寸单位均为舞台像素，缩放由画布负责，此处不做二次换算。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, provide, ref, watch } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import pageSeed from '@/data/jimuScreenPage.json'
import componentLibrary from '@/data/jimuComponentLibrary.json'
import { get, can, scopedKey } from '@/api/request'
import { reportApi } from '@/api/report'
import { applyReportIdentity, toJimuScreen, toJimuWidget, toReportContent } from '@/utils/jimuReportAdapter'
import { buildScreenAiRequest, buildWidgetAiRequirement } from '@/utils/reportAiContext'
import { appendPromptText } from '@/utils/reportPromptLibrary'
import { useReportDrilldown } from '@/composables/useReportDrilldown'
import { useDraftCheckpoint } from '@/composables/useDraftCheckpoint'
import {
  applyDashboardAdjustment,
  parseDashboardAdjustment
} from '@/utils/dashboardAdjustment'
import { SCREEN_AI_SCENARIOS, WIDGET_AI_SCENARIOS } from '@/data/reportAiScenarios'
import JimuTopToolbar from './jimu-screen/JimuTopToolbar.vue'
import JimuLayerPanel from './jimu-screen/JimuLayerPanel.vue'
import JimuCanvas from './jimu-screen/JimuCanvas.vue'
import JimuPropertyPanel from './jimu-screen/JimuPropertyPanel.vue'
import JimuWidgetRenderer from './jimu-screen/JimuWidgetRenderer.vue'
import GenerationStatusPanel from './business/GenerationStatusPanel.vue'
import ReportRevisionDialog from './business/ReportRevisionDialog.vue'
import JimuDrilldownPanel from './jimu-screen/JimuDrilldownPanel.vue'
import ReportPromptLibraryPanel from './jimu-screen/ReportPromptLibraryPanel.vue'
import { createCanvasWidget } from './jimu-screen/widgetFactory'

const STORAGE_KEY = scopedKey('report-editor-draft')
const HISTORY_LIMIT = 60
const AI_ADJUSTMENT_EXAMPLES = Object.freeze([
  '整体换成绿色科技风并增强发光',
  '当前组件换一个玻璃全息样式',
  '把当前组件换成面积图',
  '整张大屏换成紫色并增加圆角'
])
const route = useRoute()
const router = useRouter()
const isReportDesignerRoute = true

const savedState = isReportDesignerRoute ? null : loadState()
const page = ref(savedState?.page || (isReportDesignerRoute ? createEmptyPage() : clone(pageSeed)))
const widgets = ref(savedState?.widgets || (isReportDesignerRoute ? [] : clone(pageSeed.widgets)))
const selectedId = ref('')
const selectedIds = ref([])
const zoom = ref(0.72)
const showLayers = ref(true)
const showProperties = ref(true)
const isPreview = ref(route.query.preview === '1')
const undoStack = ref([])
const redoStack = ref([])
const showHelp = ref(false)
const showRevisions = ref(false)
const showDataset = ref(false)
const showAi = ref(false)
const datasetLoading = ref(false)
const dataSources = ref([])
const tables = ref([])
const activeDataSourceId = ref('')
const reportDatasourceId = ref(null)
const reportMeta = ref(null)
const loadError = ref('')
const savingReport = ref(false)
const generationRecord = ref(null)
const templateMode = ref('auto')
const aiMode = ref('screen')
const aiDatasourceId = ref('')
const aiPrompt = ref('')
const aiLoading = ref(false)
const aiRunState = ref('idle')
const aiError = ref('')
const aiIdleNotice = ref('')
const selectedAiScenarioId = ref('')
const selectedDashboardTemplateId = ref('')
const clipboardWidget = ref(null)
const widgetPreview = ref(null)
const { drilldownState, openDrilldown, closeDrilldown, exportDrilldown } = useReportDrilldown(
  computed(() => String(reportMeta.value?.id || '')), () => saveScreen(false),
)
const interactionSnapshot = ref('')
const { capture: captureDraft, hasUnsavedChanges, markSaved } = useDraftCheckpoint(() => ({
  page: page.value, widgets: widgets.value, datasourceId: reportDatasourceId.value,
  templateId: selectedDashboardTemplateId.value,
}))
const leaveDialogVisible = ref(false)
const leaveSaveError = ref('')
let leaveDecision = null
let leavePromise = null
let assigningCreatedId = false

/**
 * 离开守卫：无未保存变更直接放行；否则弹出确认框并返回一个 promise，
 * 由 resolveLeave 决定是否真的离开（同一时刻只维护一个 leavePromise，避免多次导航重复弹窗）。
 */
function confirmLeave() {
  if (!hasUnsavedChanges.value) return true
  if (!leavePromise) {
    leaveSaveError.value = ''
    leaveDialogVisible.value = true
    leavePromise = new Promise(resolve => { leaveDecision = resolve })
  }
  return leavePromise
}

function resolveLeave(allowed) {
  leaveDialogVisible.value = false
  leaveDecision?.(allowed)
  leaveDecision = null
  leavePromise = null
}

/**
 * 保存后离开：保存成功但期间又产生新改动时拒绝离开并提示用户，避免丢掉这段修改。
 * 保存失败把错误写入 leaveSaveError 供弹窗展示。
 */
async function saveAndLeave() {
  leaveSaveError.value = ''
  if (await saveScreen()) {
    if (hasUnsavedChanges.value) {
      ElMessage.warning('保存期间又有修改，请保存最新修改后离开')
      return
    }
    resolveLeave(true)
  } else leaveSaveError.value = aiError.value || '保存未完成，请稍后重试'
}

function beforeUnload(event) {
  if (!hasUnsavedChanges.value) return
  event.preventDefault()
  event.returnValue = ''
}

onBeforeRouteLeave(confirmLeave)
onBeforeRouteUpdate((to, from) => assigningCreatedId || to.params.id === from.params.id || confirmLeave())

const selectedWidget = computed(() => widgets.value.find(widget => widget.id === selectedId.value) || null)
const hasSourceTemplate = computed(() => Boolean(
  page.value.templateStyleLocked
  || page.value.sourceCanvas?.styleLocked
  || page.value.sourceContent?.canvas?.styleLocked
))
const isReferenceTemplate = computed(() => String(
  page.value.templateResourceId
  || selectedDashboardTemplateId.value
  || reportMeta.value?.templateId
  || '',
).startsWith('hifi-template-'))
// Template fidelity applies during generation. Users with editor permission
// can customize their report instance without changing the original template.
/**
 * 模板视觉锁定：20 套参考模板始终只允许改数据；旧来源模板继续沿用权限控制。
 * 这样既保证参考图复刻不漂移，也保留历史报表的兼容编辑行为。
 */
const isTemplateStyleLocked = computed(() => hasSourceTemplate.value && (
  isReferenceTemplate.value || !can(route.params.id ? 'dashboards.edit' : 'dashboards.create')
))
const aiScenarios = computed(() => aiMode.value === 'screen' ? SCREEN_AI_SCENARIOS : WIDGET_AI_SCENARIOS)
const activeAiScenario = computed(() => aiScenarios.value.find(item => item.id === selectedAiScenarioId.value) || null)
const dashboardTemplates = ref([])
const activeDashboardTemplate = computed(() => dashboardTemplates.value.find(template => template.id === selectedDashboardTemplateId.value))
provide('report-data-context', {
  id: computed(() => String(reportMeta.value?.id || '')),
  readOnly: false,
  refreshSeconds: computed(() => Number(page.value.refreshSeconds || 0)),
})
const aiDatasourceName = computed(() => {
  const source = dataSources.value.find(item => String(item.id) === String(aiDatasourceId.value))
  return source?.name || source?.datasourceName || source?.databaseName || '按需求从已授权数据源中解析'
})
const canUndo = computed(() => undoStack.value.length > 0 || Boolean(interactionSnapshot.value && interactionSnapshot.value !== snapshot()))
const canRedo = computed(() => redoStack.value.length > 0)
const editorColumns = computed(() => `${showLayers.value ? '180px' : '0px'} minmax(0, 1fr) ${showProperties.value ? '280px' : '0px'}`)
const widgetPreviewScale = computed(() => {
  if (!widgetPreview.value) return 1
  return Math.min(1, 820 / widgetPreview.value.w, 520 / widgetPreview.value.h)
})
const widgetPreviewViewportStyle = computed(() => widgetPreview.value ? ({
  width: `${widgetPreview.value.w * widgetPreviewScale.value}px`,
  height: `${widgetPreview.value.h * widgetPreviewScale.value}px`
}) : undefined)
const widgetPreviewModelStyle = computed(() => widgetPreview.value ? ({
  width: `${widgetPreview.value.w}px`,
  height: `${widgetPreview.value.h}px`,
  transform: `scale(${widgetPreviewScale.value})`
}) : undefined)

function clone(value) {
  return JSON.parse(JSON.stringify(value))
}

function createEmptyPage() {
  const empty = clone(pageSeed)
  empty.id = ''
  empty.name = '未命名报表'
  empty.backgroundColor = '#07111f'
  empty.backgroundImage = ''
  empty.backgroundLayers = ''
  empty.backgroundBlendMode = ''
  empty.widgets = []
  return empty
}

function loadState() {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (!saved) return null
    const parsed = JSON.parse(saved)
    if (!parsed.page || !Array.isArray(parsed.widgets)) return null
    return parsed
  } catch {
    return null
  }
}

/** 当前编辑状态的快照字符串，用于撤销栈与"是否有变更"判定。 */
function snapshot() {
  return JSON.stringify({ page: page.value, widgets: widgets.value })
}

/**
 * 恢复快照：同时收敛选中态 —— 过滤掉已不存在的组件 id，主选中项失效时退回最后一个可选项，
 * 避免撤销后属性面板停留在已删除的组件上。
 */
function restoreSnapshot(value) {
  const parsed = JSON.parse(value)
  page.value = parsed.page
  widgets.value = parsed.widgets
  const availableIds = new Set(widgets.value.map(widget => widget.id))
  selectedIds.value = selectedIds.value.filter(id => availableIds.has(id))
  if (!availableIds.has(selectedId.value)) selectedId.value = selectedIds.value.at(-1) || ''
}

/**
 * 记录一次历史（在修改前调用）。交互进行中（interactionSnapshot 非空）不记录，
 * 交给 endInteraction 统一落栈，避免拖拽过程中产生几十条历史。
 */
function commitHistory() {
  if (interactionSnapshot.value) return
  undoStack.value.push(snapshot())
  if (undoStack.value.length > HISTORY_LIMIT) undoStack.value.shift()
  redoStack.value = []
}

function beginInteraction() {
  if (!interactionSnapshot.value) interactionSnapshot.value = snapshot()
}

/**
 * 结束交互：仅当状态确实变化时才把交互开始时的快照压栈，
 * 并把历史栈限制在 HISTORY_LIMIT 内（超出丢弃最旧记录）。
 */
function endInteraction() {
  if (interactionSnapshot.value && interactionSnapshot.value !== snapshot()) {
    undoStack.value.push(interactionSnapshot.value)
    if (undoStack.value.length > HISTORY_LIMIT) undoStack.value.shift()
    redoStack.value = []
  }
  interactionSnapshot.value = ''
}

function undo() {
  endInteraction()
  if (!canUndo.value) return
  redoStack.value.push(snapshot())
  restoreSnapshot(undoStack.value.pop())
}

function redo() {
  endInteraction()
  if (!canRedo.value) return
  undoStack.value.push(snapshot())
  restoreSnapshot(redoStack.value.pop())
}

/**
 * 按路径写入配置：逐段拦截 __proto__ / prototype / constructor，防止原型链污染（路径可能来自 AI 输出）。
 */
function setByPath(target, path, value) {
  const parts = path.split('.')
  if (parts.some(part => ['__proto__', 'prototype', 'constructor'].includes(part))) return
  let cursor = target
  for (let index = 0; index < parts.length - 1; index += 1) {
    const part = parts[index]
    if (!cursor[part] || typeof cursor[part] !== 'object') cursor[part] = {}
    cursor = cursor[part]
  }
  cursor[parts.at(-1)] = value
}

/**
 * 修改页面属性。视觉锁定时只允许改 name / refreshSeconds（其余属性给出中文提示并拒绝），
 * 与 isTemplateStyleLocked 的"只能改数据源、SQL、参数与查询结果"约束保持一致。
 */
function patchPage(path, value) {
  if (isTemplateStyleLocked.value && !['name', 'refreshSeconds'].includes(path)) {
    ElMessage.warning('源模板视觉属性已锁定，只能修改数据源、只读 SQL、参数和查询结果')
    return
  }
  commitHistory()
  setByPath(page.value, path, value)
  if (path === 'design.width' || path === 'design.height') nextTick(fitCanvas)
}

function patchWidget(path, value) {
  if (!selectedWidget.value) return
  const dataPaths = [
    'data', 'datasourceId', 'query', 'generatedSql', 'dataStatus',
    'config.dataType', 'config.datasourceId', 'config.tableName', 'config.sql',
    'config.parameters', 'config.chartData', 'config.chartDataParsed',
    'config.resultColumns', 'config.queryId', 'config.verifiedAt',
    'config.dataStatus', 'config.dataMapping'
  ]
  if (isTemplateStyleLocked.value && !dataPaths.some(prefix => path === prefix || path.startsWith(`${prefix}.`))) {
    ElMessage.warning('该模板只允许修改数据绑定，不能修改组件、位置或样式')
    return
  }
  commitHistory()
  setByPath(selectedWidget.value, path, value)
}

function updateWidget(id, patch) {
  if (isTemplateStyleLocked.value) return
  const widget = widgets.value.find(item => item.id === id)
  if (widget) Object.assign(widget, patch)
}

function updateWidgets(patches) {
  if (isTemplateStyleLocked.value) return
  patches.forEach(({ id, patch }) => updateWidget(id, patch))
}

function patchChartData(value) {
  if (!selectedWidget.value) return
  commitHistory()
  selectedWidget.value.config.chartDataParsed = value
  selectedWidget.value.config.chartData = JSON.stringify(value)
  if (selectedWidget.value.referenceSkin) {
    // 用户主动应用数据即退出原图示例；即使数值恰好相同，也必须按当前输入重绘。
    selectedWidget.value.config.referenceSample = false
    selectedWidget.value.config.dataStatus = 'local'
  }
}

function setSelection(ids, primaryId = '') {
  const availableIds = new Set(widgets.value.map(widget => widget.id))
  selectedIds.value = [...new Set(ids)].filter(id => availableIds.has(id))
  selectedId.value = selectedIds.value.includes(primaryId) ? primaryId : (selectedIds.value.at(-1) || '')
}

function groupMemberIds(id) {
  const widget = widgets.value.find(item => item.id === id)
  if (!widget?.groupId) return id ? [id] : []
  return widgets.value.filter(item => item.groupId === widget.groupId).map(item => item.id)
}

function selectWidget(selection) {
  if (Array.isArray(selection)) {
    setSelection(selection, selection.at(-1) || '')
    return
  }
  if (selection && typeof selection === 'object') {
    if (Array.isArray(selection.ids)) {
      const expanded = selection.ids.flatMap(groupMemberIds)
      setSelection(expanded, selection.primaryId || expanded.at(-1) || '')
      return
    }
    if (selection.toggle) {
      const ids = groupMemberIds(selection.id)
      const allSelected = ids.every(id => selectedIds.value.includes(id))
      const next = allSelected
        ? selectedIds.value.filter(id => !ids.includes(id))
        : [...selectedIds.value, ...ids]
      setSelection(next, allSelected ? selectedId.value : selection.id)
      return
    }
  }
  const id = String(selection || '')
  setSelection(groupMemberIds(id), id)
}

function clearSelection() {
  setSelection([])
}

function createWidgetConfig(item) {
  const sameType = pageSeed.widgets.find(widget => widget.component === item.compType)
  if (sameType) return clone(sameType.config)
  if (item.compType === 'JImg') {
    return {
      dataType: 1,
      background: '#FFFFFF00',
      borderColor: '#FFFFFF00',
      imageUrl: item.imageUrl,
      option: { fit: 'contain' }
    }
  }
  if (/Text|Title/i.test(item.compType)) {
    return {
      dataType: 1,
      background: '#FFFFFF00',
      borderColor: '#FFFFFF00',
      chartData: { value: item.name },
      option: { body: { text: item.name, color: '#d0e0f0', fontSize: 34, fontWeight: 'bold', textAlign: 'center' } }
    }
  }
  if (/Border/i.test(item.compType)) {
    return {
      dataType: 1,
      background: '#FFFFFF00',
      borderColor: '#FFFFFF00',
      chartData: [],
      option: { type: '7', mainColor: '#05f8d6', subColor: '#0082fc', title: item.name }
    }
  }
  if (/Decoration/i.test(item.compType)) {
    return {
      dataType: 1,
      background: '#FFFFFF00',
      borderColor: '#FFFFFF00',
      chartData: [],
      option: { type: '5', mainColor: '#05f8d6', subColor: '#0082fc' }
    }
  }
  if (/Table|Board/i.test(item.compType)) {
    const rows = [
      ['14:32:18', 'DD20260716001', '华为Mate70 Pro', '¥6,299'],
      ['14:31:55', 'DD20260716002', '戴森吸尘器V15', '¥4,590'],
      ['14:31:22', 'DD20260716003', '雅诗兰黛小棕瓶', '¥1,280'],
      ['14:30:48', 'DD20260716004', '三只松鼠坚果礼盒', '¥168']
    ]
    return {
      dataType: 1,
      background: '#FFFFFF00',
      borderColor: '#FFFFFF00',
      chartData: JSON.stringify(rows),
      chartDataParsed: rows,
      option: {
        header: [{ label: '时间' }, { label: '订单号' }, { label: '商品名' }, { label: '金额' }],
        headerBGC: '#1e3048', oddRowBGC: '#1e3048aa', evenRowBGC: '#1e304833', fontColor: '#c0d4ee'
      }
    }
  }
  const chartData = [
    { name: '一月', value: 36 },
    { name: '二月', value: 58 },
    { name: '三月', value: 42 },
    { name: '四月', value: 76 },
    { name: '五月', value: 64 }
  ]
  return {
    dataType: 1,
    background: '#FFFFFF00',
    borderColor: '#FFFFFF00',
    chartData: JSON.stringify(chartData),
    chartDataParsed: chartData,
    option: {
      color: ['#36d7b7', '#4a9eff', '#f7c948'],
      title: { show: true, text: item.name, textStyle: { color: '#5ba8f0', fontSize: 13, fontWeight: 'normal' } },
      xAxis: { axisLabel: { color: '#EEF1FA' } },
      yAxis: { axisLabel: { color: '#EEF1FA' }, splitLine: { show: false } },
      series: [{}]
    }
  }
}

function addComponent(item, point = null) {
  if (isTemplateStyleLocked.value) {
    ElMessage.warning('锁定模板不能新增组件；如需自由设计，请新建无模板大屏')
    return
  }
  commitHistory()
  const stageWidth = Number(page.value.design.width || 1920)
  const stageHeight = Number(page.value.design.height || 1080)
  const maxOrder = Math.max(...widgets.value.map(widget => Number(widget.orderNum || 0)), 0)
  const widget = createCanvasWidget(item, {
    stageWidth,
    stageHeight,
    orderNum: maxOrder + 1
  })
  if (point) {
    widget.x = Math.max(0, Math.round(point.x - widget.w / 2))
    widget.y = Math.max(0, Math.round(point.y - widget.h / 2))
  }
  widgets.value.push(widget)
  setSelection([widget.id], widget.id)
  ElMessage.success(`已添加：${item.name}`)
}

function addDroppedComponent({ item, point }) {
  addComponent(item, point)
}

function toggleVisible(id) {
  if (isTemplateStyleLocked.value) return
  const widget = widgets.value.find(item => item.id === id)
  if (!widget) return
  commitHistory()
  widget.visible = widget.visible === false
}

function toggleLock(id) {
  if (isTemplateStyleLocked.value) return
  const widget = widgets.value.find(item => item.id === id)
  if (!widget) return
  const ids = widget.groupId ? groupMemberIds(id) : [id]
  const lock = !ids.every(itemId => widgets.value.find(item => item.id === itemId)?.locked)
  commitHistory()
  widgets.value.forEach(item => {
    if (ids.includes(item.id)) item.locked = lock
  })
}

function reorder(id, direction) {
  if (isTemplateStyleLocked.value) return
  const ordered = [...widgets.value].sort((left, right) => Number(left.orderNum || 0) - Number(right.orderNum || 0))
  const index = ordered.findIndex(item => item.id === id)
  const targetIndex = index + (direction > 0 ? 1 : -1)
  if (index < 0 || targetIndex < 0 || targetIndex >= ordered.length) return
  commitHistory()
  const currentOrder = Number(ordered[index].orderNum || index)
  ordered[index].orderNum = Number(ordered[targetIndex].orderNum || targetIndex)
  ordered[targetIndex].orderNum = currentOrder
}

function moveToExtreme(id, position) {
  if (isTemplateStyleLocked.value) return
  const widget = widgets.value.find(item => item.id === id)
  if (!widget) return
  commitHistory()
  const orders = widgets.value.map(item => Number(item.orderNum || 0))
  widget.orderNum = position === 'top' ? Math.max(...orders, 0) + 1 : Math.min(...orders, 0) - 1
}

function renameWidget(id, value) {
  if (isTemplateStyleLocked.value) return
  const widget = widgets.value.find(item => item.id === id)
  if (!widget) return
  commitHistory()
  widget.componentName = value
}

function duplicateWidget(id = selectedId.value) {
  if (isTemplateStyleLocked.value) return
  const source = widgets.value.find(item => item.id === id)
  if (!source) return
  commitHistory()
  const copy = clone(source)
  copy.id = `copy-${Date.now()}-${Math.random().toString(16).slice(2, 7)}`
  copy.i = copy.id
  copy.pageCompId = ''
  copy.componentName = `${copy.componentName} 副本`
  copy.x += 24
  copy.y += 24
  copy.orderNum = Math.max(...widgets.value.map(widget => Number(widget.orderNum || 0)), 0) + 1
  copy.locked = false
  delete copy.groupId
  widgets.value.push(copy)
  setSelection([copy.id], copy.id)
}

function removeWidget(id = selectedId.value) {
  if (isTemplateStyleLocked.value) return
  const targetIds = selectedIds.value.includes(id) && selectedIds.value.length > 1 ? selectedIds.value : groupMemberIds(id)
  const removableIds = targetIds.filter(itemId => !widgets.value.find(item => item.id === itemId)?.locked)
  if (!removableIds.length) return
  commitHistory()
  widgets.value = widgets.value.filter(item => !removableIds.includes(item.id))
  clearSelection()
}

function copySelectedWidget(id = selectedId.value) {
  const source = widgets.value.find(item => item.id === id)
  if (!source) return
  clipboardWidget.value = clone(source)
}

function cutSelectedWidget(id = selectedId.value) {
  const source = widgets.value.find(item => item.id === id)
  if (!source || source.locked) return
  copySelectedWidget(id)
  removeWidget(id)
}

function pasteClipboard(payload = {}) {
  if (isTemplateStyleLocked.value) return
  if (!clipboardWidget.value) return
  commitHistory()
  const copy = clone(clipboardWidget.value)
  copy.id = `paste-${Date.now()}-${Math.random().toString(16).slice(2, 7)}`
  copy.i = copy.id
  copy.pageCompId = ''
  copy.locked = false
  copy.visible = true
  delete copy.groupId
  if (payload.blank && Number.isFinite(payload.stageX) && Number.isFinite(payload.stageY)) {
    copy.x = Math.round(payload.stageX)
    copy.y = Math.round(payload.stageY)
  } else {
    copy.x = Number(copy.x || 0) + 10
    copy.y = Number(copy.y || 0) + 10
  }
  copy.orderNum = Math.max(...widgets.value.map(widget => Number(widget.orderNum || 0)), 0) + 1
  widgets.value.push(copy)
  setSelection([copy.id], copy.id)
  clipboardWidget.value = clone(copy)
}

function clearWidgetLinkage(id) {
  const widget = widgets.value.find(item => item.id === id)
  if (!widget?.config?.linkageConfig?.length) return
  commitHistory()
  widget.config.linkageConfig = []
}

function groupSelectedWidgets() {
  if (isTemplateStyleLocked.value) return
  if (selectedIds.value.length < 2) return
  commitHistory()
  const groupId = `group-${Date.now()}-${Math.random().toString(16).slice(2, 7)}`
  widgets.value.forEach(widget => {
    if (selectedIds.value.includes(widget.id)) widget.groupId = groupId
  })
}

function ungroupWidgets(id = selectedId.value) {
  if (isTemplateStyleLocked.value) return
  const target = widgets.value.find(widget => widget.id === id)
  if (!target?.groupId) return
  commitHistory()
  const groupId = target.groupId
  widgets.value.forEach(widget => {
    if (widget.groupId === groupId) delete widget.groupId
  })
}

function handleContextAction(payload) {
  const id = payload.id || selectedId.value
  if (payload.action === 'group') groupSelectedWidgets()
  else if (payload.action === 'ungroup') ungroupWidgets(id)
  else if (payload.action === 'top') moveToExtreme(id, 'top')
  else if (payload.action === 'bottom') moveToExtreme(id, 'bottom')
  else if (payload.action === 'move-up') reorder(id, 1)
  else if (payload.action === 'move-down') reorder(id, -1)
  else if (payload.action === 'copy') copySelectedWidget(id)
  else if (payload.action === 'cut') cutSelectedWidget(id)
  else if (payload.action === 'paste') pasteClipboard(payload)
  else if (payload.action === 'preview') widgetPreview.value = clone(widgets.value.find(item => item.id === id))
  else if (payload.action === 'drilldown') {
    const widget = widgets.value.find(item => item.id === id)
    if (widget) openDrilldown({ widget, point: {} })
  }
  else if (payload.action === 'edit') {
    selectWidget(id)
    showProperties.value = true
    nextTick(fitCanvas)
  } else if (payload.action === 'clear-linkage') clearWidgetLinkage(id)
  else if (payload.action === 'remove') removeWidget(id)
  else if (payload.action === 'lock') toggleLock(id)
}

function resetScreen() {
  commitHistory()
  page.value = isReportDesignerRoute ? createEmptyPage() : clone(pageSeed)
  widgets.value = isReportDesignerRoute ? [] : clone(pageSeed.widgets)
  clearSelection()
  localStorage.removeItem(STORAGE_KEY)
  nextTick(fitCanvas)
  page.value.templateId = ''
  page.value.templateResourceId = ''
  page.value.templateMode = 'none'
  templateMode.value = 'auto'
  selectedDashboardTemplateId.value = ''
  ElMessage.success('已清空当前草稿画布；保存后才会更新报表')
}

async function saveScreen(showMessage = true) {
  if (savingReport.value || loadError.value) return false
  const reportId = route.params.id
  if (!can(reportId ? 'dashboards.edit' : 'dashboards.create')) {
    ElMessage.error('没有保存此报表的权限')
    return false
  }
  savingReport.value = true
  try {
    const submittedDraft = captureDraft()
    try { localStorage.setItem(STORAGE_KEY, snapshot()) } catch { /* large drafts still save on the server */ }
    // A locked source template keeps its canvas title and every other visual
    // property byte-for-byte.  The user-facing report name is separate
    // metadata and must not be overwritten by that source canvas title.
    const reportName = isTemplateStyleLocked.value
      ? (reportMeta.value?.reportName || page.value.name || '未命名报表')
      : (page.value.name || reportMeta.value?.reportName || '未命名报表')
    const persistedTemplateMode = page.value.templateMode || reportMeta.value?.templateMode || 'none'
    const persistedTemplateId = persistedTemplateMode === 'none'
      ? null
      : (selectedDashboardTemplateId.value || reportMeta.value?.templateId || null)
    const payload = {
      reportName,
      description: reportMeta.value?.description || '',
      datasourceId: reportDatasourceId.value || null,
      datasourceIds: [...new Set(widgets.value.map(widget => widget.config?.datasourceId).filter(Boolean))],
      width: page.value.design.width,
      height: page.value.design.height,
      backgroundColor: page.value.backgroundColor,
      backgroundImage: page.value.backgroundImage,
      refreshSeconds: page.value.refreshSeconds || 0,
      templateMode: persistedTemplateMode,
      // page.templateId is the visual identity embedded in the canvas (for
      // example factory-monitoring-center).  Persist only the independently
      // selected template catalog resource here.
      templateId: persistedTemplateId,
      version: reportMeta.value?.version,
      // 参考高保真模板必须保持原画布；只有自由设计或历史模板才允许解锁布局。
      editTemplateLayout: !isTemplateStyleLocked.value,
      content: JSON.stringify(toReportContent(
        { ...page.value, widgets: widgets.value },
        { editTemplateLayout: !isTemplateStyleLocked.value },
      ))
    }
    if (!reportId) {
      const response = await reportApi.create(payload)
      const created = response.data || response.result || response
      if (!created?.id) throw new Error('Report creation returned no id')
      reportMeta.value = created
      reportDatasourceId.value = created.datasourceId || payload.datasourceId
      markSaved(submittedDraft)
      assigningCreatedId = true
      try { await router.replace(`/report/designer/${created.id}`) } finally { assigningCreatedId = false }
      if (showMessage) ElMessage.success('报表已保存')
      return true
    }
    if (!reportMeta.value?.version) throw new Error('报表尚未加载完成，不能覆盖保存')
    const response = await reportApi.update(reportId, payload)
    reportMeta.value = response.data
    markSaved(submittedDraft)
    if (showMessage) ElMessage.success('报表配置已保存')
    return true
  } catch (error) {
    aiError.value = error.message || '报表保存失败'
    ElMessage.error(aiError.value)
    return false
  } finally {
    savingReport.value = false
  }
}

function exportScreen() {
  if (!can('dashboards.export')) { ElMessage.warning('没有导出报表配置的权限'); return }
  const blob = new Blob([JSON.stringify({ page: page.value, widgets: widgets.value }, null, 2)], { type: 'application/json' })
  const href = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = href
  anchor.download = `${page.value.name || '积木大屏'}-配置.json`
  anchor.click()
  URL.revokeObjectURL(href)
}

async function publishScreen() {
  if (!can('dashboards.publish')) return
  const status = reportMeta.value?.status === 'published' ? 'draft' : 'published'
  try {
    await ElMessageBox.confirm(status === 'published' ? '保存并发布当前报表？仅有相应数据权限的用户可以访问，不会公开到互联网。' : '将此报表撤回为草稿？', status === 'published' ? '发布报表' : '撤回发布')
    if (!(await saveScreen(false))) return
    reportMeta.value = (await reportApi.update(reportMeta.value.id, {
      reportName: isTemplateStyleLocked.value
        ? (reportMeta.value.reportName || page.value.name)
        : page.value.name,
      description: reportMeta.value.description,
      version: reportMeta.value.version, status,
    })).data
    ElMessage.success(status === 'published' ? '报表已发布' : '已撤回为草稿')
  } catch (error) { if (error !== 'cancel' && error !== 'close') ElMessage.error(error.message || '发布状态更新失败') }
}

function fitCanvas() {
  const stageWidth = Number(page.value.design.width || 1920)
  const stageHeight = Number(page.value.design.height || 1080)
  const availableWidth = isPreview.value
    ? window.innerWidth
    : window.innerWidth - (showLayers.value ? 180 : 0) - (showProperties.value ? 280 : 0) - 80
  const toolbarHeight = document.querySelector('.jimu-toolbar')?.getBoundingClientRect().height || 45
  const availableHeight = isPreview.value ? window.innerHeight : window.innerHeight - toolbarHeight - 100
  const fittedZoom = Math.max(0.1, Math.min(1, availableWidth / stageWidth, availableHeight / stageHeight))
  zoom.value = Math.round(fittedZoom * 100) / 100
}

async function openPreview(mode) {
  const saved = await saveScreen(false)
  if (!saved) return
  if (mode === 'new') {
    const href = router.resolve(`/report/view/${route.params.id}`).href
    window.open(href, '_blank', 'noopener')
    return
  }
  isPreview.value = true
  clearSelection()
  nextTick(fitCanvas)
}

function exitPreview() {
  if (route.query.preview === '1' && window.opener && !window.opener.closed) {
    window.close()
    return
  }
  isPreview.value = false
  if (route.query.preview === '1') {
    const query = { ...route.query }
    delete query.preview
    router.replace({ query })
  }
  nextTick(fitCanvas)
}

function goDatasource() {
  router.push('/datasources')
}

function closeEditor() {
  router.push('/dashboards')
}

async function ensureDataSources() {
  if (dataSources.value.length) return
  datasetLoading.value = true
  try {
    const response = await get('/api/data-analysis/datasources')
    dataSources.value = (response.data || []).filter(source => source.enabled)
  } catch (error) {
    dataSources.value = []
    aiIdleNotice.value = error?.message || '数据源列表加载失败，请检查后端服务连接'
  } finally {
    datasetLoading.value = false
  }
}

async function openDatasetDialog() {
  showDataset.value = true
  await ensureDataSources()
}

async function openAiDialog(mode = 'screen') {
  if (!can('dashboards.execute')) { ElMessage.warning('没有 AI 生成与组件执行权限'); return }
  setAiMode(mode)
  resetAiExecution()
  showAi.value = true
  await ensureDataSources()
  aiDatasourceId.value = String(reportDatasourceId.value || activeDataSourceId.value || '')
  if (can('templates.view')) {
    try { dashboardTemplates.value = (await get('/api/v1/report-templates')).data.filter(template => template.enabled) }
    catch (error) { aiIdleNotice.value = error.message }
  }
  if (!dashboardTemplates.value.length) {
    templateMode.value = 'auto'
    selectedDashboardTemplateId.value = ''
  }
  if (!aiDatasourceId.value && !dataSources.value.length) {
    aiIdleNotice.value = aiIdleNotice.value || '当前没有可用数据源，请先检查数据源服务后再生成'
  }
}

function setAiMode(mode) {
  if (aiMode.value === mode) return
  aiMode.value = mode
  selectedAiScenarioId.value = ''
}

function selectAiScenario(scenario) {
  if (!scenario) return
  selectedAiScenarioId.value = scenario.id
  aiPrompt.value = scenario.prompt
}

function dashboardTemplatePreview(template) {
  return template.preview || ''
}

function dashboardTemplateOptionLabel(template) {
  const source = template.sourceKind === 'lynx-report-screen'
    ? '智慧云脑 Cloud Brain 实际报表'
    : String(template.sourceKind || '').startsWith('sql-') ? 'SQL 来源' : '视觉模板'
  return `${template.shortName || template.name} · ${source} #${template.sourceRecordId || template.id}`
}

function resetAiExecution() {
  aiRunState.value = 'idle'
  aiError.value = ''
  aiIdleNotice.value = ''
  generationRecord.value = null
}

function startAiProgress() {
  resetAiExecution()
  aiRunState.value = 'running'
}

function finishAiProgress(state) {
  aiRunState.value = state
}

function closeAiDialog() {
  if (aiLoading.value) return
  resetAiExecution()
  showAi.value = false
}

async function loadTables(source) {
  activeDataSourceId.value = String(source.id)
  datasetLoading.value = true
  try {
    const response = await get(`/api/data-analysis/datasources/${source.id}/tables/info`)
    tables.value = response.data || response.result || response || []
  } catch (error) {
    tables.value = []
    ElMessage.error(error.message || '表结构加载失败')
  } finally {
    datasetLoading.value = false
  }
}

function bindTable(table) {
  if (!selectedWidget.value || !activeDataSourceId.value) {
    ElMessage.warning('请先在画布中选中需要绑定数据的组件')
    return
  }
  const tableName = table.tableName || table.name
  if (!tableName) return
  commitHistory()
  selectedWidget.value.config.dataType = 3
  selectedWidget.value.config.datasourceId = activeDataSourceId.value
  selectedWidget.value.config.tableName = tableName
  const source = dataSources.value.find(item => String(item.id) === activeDataSourceId.value)
  const quote = source?.type === 'mysql' ? String.fromCharCode(96) : '"'
  selectedWidget.value.config.sql = 'SELECT * FROM ' + quote + tableName.replaceAll(quote, quote + quote) + quote + ' LIMIT 100'
  showDataset.value = false
  ElMessage.success(`已绑定数据表：${tableName}`)
}

function adaptGeneratedWidgets(generatedWidgets) {
  return generatedWidgets.map((widget, index) => {
    const theme = widget.theme || {}
    const background = widget.config?.background
      ?? widget.style?.background
      ?? widget.style?.backgroundColor
      ?? theme.panel
      ?? 'transparent'
    const borderColor = widget.config?.borderColor
      ?? widget.style?.borderColor
      ?? theme.grid
      ?? 'transparent'
    return toJimuWidget({
      ...widget,
      visualMode: 'ai',
      id: widget.id || `ai-${Date.now()}-${index}`,
      position: widget.position || { left: 80, top: 80, width: 520, height: 300 },
      style: { ...(widget.style || {}), background, borderColor },
      config: {
        ...(widget.config || {}),
        datasourceId: widget.config?.datasourceId || widget.datasourceId || (aiDatasourceId.value ? String(aiDatasourceId.value) : null),
        sql: widget.config?.sql || widget.query?.sql || widget.generatedSql || '',
        background,
        borderColor
      }
    }, index)
  })
}

function findAvailableWidgetPosition(width, height) {
  const gap = 24
  const stepX = Math.max(120, Math.round(width / 2))
  const stepY = Math.max(90, Math.round(height / 2))
  const stageWidth = Math.max(Number(page.value.design.width || 1920), width + gap * 2)
  const maxBottom = Math.max(
    Number(page.value.design.height || 1080),
    ...widgets.value.map(widget => Number(widget.y || 0) + Number(widget.h || 0))
  )

  for (let y = gap; y <= maxBottom + height + gap; y += stepY) {
    for (let x = gap; x + width <= stageWidth - gap; x += stepX) {
      const overlaps = widgets.value.some(widget => (
        x < Number(widget.x || 0) + Number(widget.w || 0) + gap
        && x + width + gap > Number(widget.x || 0)
        && y < Number(widget.y || 0) + Number(widget.h || 0) + gap
        && y + height + gap > Number(widget.y || 0)
      ))
      if (!overlaps) return { x, y }
    }
  }
  return { x: gap, y: maxBottom + gap }
}

async function applyAiConfig() {
  if (aiLoading.value || !can('dashboards.execute')) return
  if (!aiPrompt.value.trim()) {
    aiIdleNotice.value = '请输入生成需求后再启动 AI 编排'
    ElMessage.warning({ message: '请输入生成需求', zIndex: 4200 })
    return
  }
  const adjustment = parseDashboardAdjustment(aiPrompt.value.trim(), {
    selectedId: selectedId.value,
    widgets: widgets.value
  })
  if (aiMode.value !== 'screen' && adjustment.handled) {
    if (isTemplateStyleLocked.value) {
      aiIdleNotice.value = '锁定模板仅允许重新绑定数据，不能执行样式调整'
      return
    }
    aiLoading.value = true
    startAiProgress()
    try {
      commitHistory()
      const adjusted = applyDashboardAdjustment(page.value, widgets.value, adjustment)
      page.value = adjusted.page
      widgets.value = adjusted.widgets
      await nextTick()
      fitCanvas()
      const saved = await saveScreen(false)
      if (!saved) {
        aiError.value = '页面调整已应用，但远端保存失败，请检查连接后重试'
        finishAiProgress('error')
        ElMessage.error(aiError.value)
        return
      }
      finishAiProgress('success')
      showAi.value = false
      ElMessage.success(adjustment.summary || '快捷规则已完成页面样式调整')
    } finally {
      aiLoading.value = false
    }
    return
  }
  if (aiMode.value === 'screen' && templateMode.value === 'manual' && !selectedDashboardTemplateId.value) {
    aiIdleNotice.value = '手动模板模式需要明确选择一套模板'
    return
  }
  if (aiMode.value === 'screen' && widgets.value.length) {
    try {
      await ElMessageBox.confirm('整屏生成会替换当前画布中的全部组件，是否继续？', '替换当前报表', {
        confirmButtonText: '继续生成',
        cancelButtonText: '取消',
        type: 'warning',
        zIndex: 4200
      })
    } catch {
      return
    }
  }
  aiLoading.value = true
  startAiProgress()
  try {
    if (aiMode.value === 'screen') {
      const response = await reportApi.aiGenerateScreen(buildScreenAiRequest({
        datasourceId: aiDatasourceId.value,
        userPrompt: aiPrompt.value.trim(),
        page: page.value,
        widgets: widgets.value,
        templateMode: templateMode.value,
        templateId: selectedDashboardTemplateId.value,
        templateReference: activeDashboardTemplate.value ? JSON.stringify({ name: activeDashboardTemplate.value.name, description: activeDashboardTemplate.value.description }) : ''
      }))
      const generated = response.data || response.result || response
      if (generated?.success && Array.isArray(generated.widgets)) {
        commitHistory()
        if (generated.title) page.value.name = generated.title
        if (generated.canvas?.backgroundColor) page.value.backgroundColor = generated.canvas.backgroundColor
        page.value.backgroundImage = generated.canvas?.backgroundImage || ''
        page.value.backgroundLayers = generated.canvas?.backgroundLayers || ''
        page.value.backgroundBlendMode = generated.canvas?.backgroundBlendMode || ''
        if (generated.canvas?.themeId) page.value.themeId = generated.canvas.themeId
        if (generated.canvas?.theme) page.value.theme = generated.canvas.theme
        const generatedStyleLocked = Boolean(generated.templateVisualHash || generated.canvas?.styleLocked)
        page.value.templateStyleLocked = generatedStyleLocked
        page.value.visualMode = generatedStyleLocked ? (generated.canvas?.visualMode || '') : 'ai'
        page.value.templateId = generated.canvas?.templateId || ''
        page.value.templateResourceId = generated.templateId || ''
        page.value.templateMode = templateMode.value
        selectedDashboardTemplateId.value = generated.templateId || ''
        page.value.visualBlueprint = generated.canvas?.visualBlueprint || ''
        page.value.sourceCanvas = generated.canvas || {}
        if (generatedStyleLocked) {
          page.value.sourceContent = clone({ canvas: generated.canvas, widgets: generated.widgets })
        }
        page.value.design.width = generated.canvas.width
        page.value.design.height = generated.canvas.height
        page.value.heroAsset = ''
        widgets.value = generatedStyleLocked ? clone(generated.widgets) : adaptGeneratedWidgets(generated.widgets)
      } else {
        throw new Error(generated?.errorMessage || generated?.message || 'AI 未返回有效大屏')
      }
    } else {
      const current = selectedWidget.value ? clone(selectedWidget.value) : null
      const response = await reportApi.aiGenerate({
        datasourceId: aiDatasourceId.value ? String(aiDatasourceId.value) : null,
        requirement: buildWidgetAiRequirement({
          userPrompt: aiPrompt.value.trim(),
          page: page.value,
          widgets: widgets.value,
          selectedWidget: current,
          datasourceId: aiDatasourceId.value
        })
      })
      const generated = response.data || response.result || response
      if (!generated?.success) {
        throw new Error(generated?.errorMessage || generated?.message || 'AI 未返回有效组件')
      }
      const generatedOption = generated.echartsOption || {}
      const generatedStyle = generated.style || {}
      const generatedBackground = generatedStyle.background
        ?? generatedOption.backgroundColor
        ?? 'transparent'
      const generatedBorderColor = generatedStyle.borderColor ?? 'transparent'
      const converted = toJimuWidget({
        ...generated,
        type: 'chart',
        visualMode: 'ai',
        title: generated.title || current?.componentName || 'AI 分析组件',
        config: {
          ...(generated.config || {}),
          dataType: 3,
          datasourceId: generated.datasourceId || generated.config?.datasourceId,
          sql: generated.generatedSql || '',
          background: generatedBackground,
          borderColor: generatedBorderColor
        },
        position: current
          ? { left: current.x, top: current.y, width: current.w, height: current.h }
          : { left: 80, top: 80, width: 560, height: 320 },
        style: { ...generatedStyle, background: generatedBackground, borderColor: generatedBorderColor }
      }, widgets.value.length)
      commitHistory()
      if (current) {
        const index = widgets.value.findIndex(widget => widget.id === current.id)
        converted.id = current.id
        converted.i = current.i || current.id
        converted.orderNum = current.orderNum
        converted.groupId = current.groupId
        widgets.value.splice(index, 1, converted)
        setSelection([converted.id], converted.id)
      } else {
        const position = findAvailableWidgetPosition(converted.w, converted.h)
        converted.x = position.x
        converted.y = position.y
        widgets.value.push(converted)
        setSelection([converted.id], converted.id)
      }
    }
    reportDatasourceId.value = aiDatasourceId.value || widgets.value.find(widget => widget.config?.datasourceId)?.config?.datasourceId || null
    const saved = await saveScreen(false)
    if (!saved) {
      throw new Error('AI generation completed, but report save failed')
    }
    finishAiProgress('success')
    showAi.value = false
    await nextTick()
    fitCanvas()
    ElMessage.success(aiMode.value === 'screen' ? 'AI 大屏已生成' : 'AI 组件已生成')
  } catch (error) {
    aiError.value = error.message || 'AI 生成失败'
    const generationId = error.response?.data?.generation_id
    if (generationId) {
      try { generationRecord.value = (await get('/api/v1/reports/generations/' + generationId)).data } catch { /* original failure remains visible */ }
    }
    finishAiProgress('error')
    ElMessage.error(error.message || 'AI 生成失败')
  } finally {
    aiLoading.value = false
  }
}

function handleKeydown(event) {
  if (loadError.value) return
  if (drilldownState.value && event.key === 'Escape') {
    closeDrilldown()
    return
  }
  if (showAi.value || showDataset.value || showHelp.value || showRevisions.value || widgetPreview.value || drilldownState.value) return
  const modifier = event.ctrlKey || event.metaKey
  const editingText = ['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName)
  if (modifier && event.key.toLowerCase() === 's') {
    event.preventDefault()
    saveScreen()
  } else if (modifier && event.key.toLowerCase() === 'z') {
    event.preventDefault()
    event.shiftKey ? redo() : undo()
  } else if (modifier && event.key.toLowerCase() === 'y') {
    event.preventDefault()
    redo()
  } else if (modifier && event.key.toLowerCase() === 'd') {
    event.preventDefault()
    duplicateWidget()
  } else if (modifier && event.key.toLowerCase() === 'c' && selectedId.value && !editingText) {
    event.preventDefault()
    copySelectedWidget()
  } else if (modifier && event.key.toLowerCase() === 'x' && selectedId.value && !editingText) {
    event.preventDefault()
    cutSelectedWidget()
  } else if (modifier && event.key.toLowerCase() === 'v' && !editingText) {
    event.preventDefault()
    pasteClipboard()
  } else if (modifier && event.key.toLowerCase() === 'l' && selectedId.value && !editingText) {
    event.preventDefault()
    toggleLock(selectedId.value)
  } else if (modifier && event.key === 'ArrowUp' && selectedId.value && !editingText) {
    event.preventDefault()
    moveToExtreme(selectedId.value, 'top')
  } else if (modifier && event.key === 'ArrowDown' && selectedId.value && !editingText) {
    event.preventDefault()
    moveToExtreme(selectedId.value, 'bottom')
  } else if (event.shiftKey && event.key === 'ArrowUp' && selectedId.value && !editingText) {
    event.preventDefault()
    reorder(selectedId.value, 1)
  } else if (event.shiftKey && event.key === 'ArrowDown' && selectedId.value && !editingText) {
    event.preventDefault()
    reorder(selectedId.value, -1)
  } else if ((event.key === 'Delete' || event.key === 'Backspace') && selectedId.value) {
    if (!editingText) removeWidget()
  } else if (event.key === 'Escape' && isPreview.value) {
    exitPreview()
  } else if (selectedWidget.value && ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) {
    if (editingText) return
    if (isTemplateStyleLocked.value) return
    event.preventDefault()
    commitHistory()
    const step = 1
    if (event.key === 'ArrowLeft') selectedWidget.value.x -= step
    if (event.key === 'ArrowRight') selectedWidget.value.x += step
    if (event.key === 'ArrowUp') selectedWidget.value.y -= step
    if (event.key === 'ArrowDown') selectedWidget.value.y += step
  }
}

async function loadReport() {
  loadError.value = ''
  if (route.params.id) {
    try {
      const response = await reportApi.getById(route.params.id)
      const report = response.data || response
      reportMeta.value = report
      templateMode.value = report.templateMode === 'manual' ? 'manual' : 'auto'
      reportDatasourceId.value = report?.datasourceId || null
      if (report?.content) {
        const converted = toJimuScreen(report.content, {
          preserveSourceWidgets: Boolean(report.lineage?.preserveSourceWidgets)
        })
        converted.templateStyleLocked = Boolean(report.lineage?.templateVisualHash)
        applyReportIdentity(converted, report.reportName)
        page.value = converted
        page.value.name = report.reportName || converted.name
        page.value.templateMode = report.templateMode || 'none'
        // Canvas visual identity and template-catalog identity are different
        // namespaces.  Keep the embedded identity for rendering, and keep the
        // relational template ID solely in the selector/save state.
        page.value.templateId = converted.templateId || ''
        page.value.templateResourceId = report.templateId || ''
        page.value.refreshSeconds = report.refreshSeconds
        widgets.value = converted.widgets
        selectedDashboardTemplateId.value = report.templateId || ''
      }
      if (report?.reportName) applyReportIdentity(page.value, report.reportName)
      undoStack.value = []
      redoStack.value = []
      clearSelection()
      markSaved()
    } catch (error) {
      loadError.value = error.message || '报表加载失败'
      ElMessage.error(loadError.value)
    }
  } else markSaved()
}
watch(() => route.params.id, async id => {
  if (id && id !== reportMeta.value?.id) { await loadReport(); await nextTick(); fitCanvas() }
})
onMounted(async () => {
  await loadReport()
  window.addEventListener('resize', fitCanvas)
  window.addEventListener('keydown', handleKeydown)
  window.addEventListener('beforeunload', beforeUnload)
  nextTick(fitCanvas)
  const promptKey = scopedKey('report-prompt')
  const savedPrompt = sessionStorage.getItem(promptKey)
  if (savedPrompt) {
    try {
      const prompt = JSON.parse(savedPrompt)
      aiPrompt.value = prompt.content || ''
      await openAiDialog(prompt.mode === 'widget' ? 'widget' : 'screen')
      sessionStorage.removeItem(promptKey)
    } catch { ElMessage.warning('提示词交接数据无效，请从提示词库重新选择') }
  } else if (route.query.ai === '1') await openAiDialog()
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', fitCanvas)
  window.removeEventListener('keydown', handleKeydown)
  window.removeEventListener('beforeunload', beforeUnload)
  resolveLeave(false)
})
</script>

<template>
  <main class="jimu-replica" :class="{ preview: isPreview }">
    <section v-if="loadError" class="editor-load-error" role="alert">
      <h1>无法打开报表</h1><p>{{ loadError }}</p>
      <button @click="loadReport">重新加载</button><button @click="router.push('/dashboards')">返回报表列表</button>
    </section>
    <template v-else-if="!isPreview">
      <JimuTopToolbar
        :groups="componentLibrary.toolbarGroups"
        :can-undo="canUndo"
        :can-redo="canRedo"
        :saving="savingReport"
        :dirty="hasUnsavedChanges"
        :has-report="Boolean(reportMeta?.id)"
        :status="reportMeta?.status || 'draft'"
        @add="addComponent"
        @save="saveScreen"
        @save-as="exportScreen"
        @undo="undo"
        @redo="redo"
        @preview="openPreview"
        @ai="openAiDialog('screen')"
        @help="showHelp = true"
        @datasource="goDatasource"
        @dataset="openDatasetDialog"
        @close="closeEditor"
        @history="showRevisions = true"
        @publish="publishScreen"
      />

      <div v-if="hasSourceTemplate && !isTemplateStyleLocked" class="template-style-lock" role="status">
        <i class="fa-solid fa-pen-ruler"></i>
        <div><strong>模板生成 · 可自由编辑</strong><span>拖动组件调整位置，使用边缘控制点调整大小；保存只更新当前报表。</span></div>
        <code>{{ page.sourceCanvas?.sourceSchema }}</code>
      </div>

      <div v-if="isReferenceTemplate" class="template-style-lock" role="status">
        <i class="fa-solid fa-palette"></i>
        <div><strong>参考布局 · 数据可编辑</strong><span>点击图层，在右侧修改数据。原图示例未绑定企业数据；应用新数据后，该区域按当前结果重绘。</span></div>
      </div>

      <div class="editor-workspace" :style="{ gridTemplateColumns: editorColumns }">
        <JimuLayerPanel
          v-if="showLayers"
          :widgets="widgets"
          :selected-id="selectedId"
          :selected-ids="selectedIds"
          :visual-locked="isTemplateStyleLocked"
          @select="selectWidget"
          @toggle-visible="toggleVisible"
          @toggle-lock="toggleLock"
          @reorder="reorder"
          @duplicate="duplicateWidget"
          @remove="removeWidget"
          @rename="renameWidget"
        />
        <div v-else></div>

        <section class="canvas-column">
          <button v-if="showLayers" class="left-collapse" title="收起图层" @click="showLayers = false; nextTick(fitCanvas)"><i class="fa-solid fa-angle-left"></i></button>
          <button v-else class="left-expand" title="展开图层" @click="showLayers = true; nextTick(fitCanvas)"><i class="fa-solid fa-angle-right"></i></button>
          <button v-if="!showProperties" class="right-expand" title="展开配置" @click="showProperties = true; nextTick(fitCanvas)"><i class="fa-solid fa-angle-left"></i></button>
          <JimuCanvas
            :page="page"
            :widgets="widgets"
            :selected-id="selectedId"
            :selected-ids="selectedIds"
            :zoom="zoom"
            :can-paste="Boolean(clipboardWidget)"
            @select="selectWidget"
            @update-widget="updateWidget"
            @update-widgets="updateWidgets"
            @interaction-start="beginInteraction"
            @interaction-end="endInteraction"
            @context-action="handleContextAction"
            @drilldown="openDrilldown"
            @drop-component="addDroppedComponent"
            @zoom-change="zoom = $event"
            @fit="fitCanvas"
          />
        </section>

        <JimuPropertyPanel
          v-if="showProperties"
          :page="page"
          :selected="selectedWidget"
          :visual-locked="isTemplateStyleLocked"
          @patch-page="patchPage"
          @patch-widget="patchWidget"
          @chart-data="patchChartData"
          @interaction-start="beginInteraction"
          @interaction-end="endInteraction"
          @collapse="showProperties = false; nextTick(fitCanvas)"
          @open-ai="openAiDialog('widget')"
        />
        <div v-else></div>
      </div>
    </template>

    <template v-else>
      <JimuCanvas
        :page="page"
        :widgets="widgets"
        :selected-id="''"
        :zoom="zoom"
        preview
        @drilldown="openDrilldown"
        @zoom-change="zoom = $event"
      />
      <button class="exit-preview" title="退出预览" @click="exitPreview"><i class="fa-solid fa-xmark"></i></button>
    </template>

    <el-dialog :model-value="leaveDialogVisible" title="有未保存的修改" width="min(480px, 94vw)" :close-on-click-modal="false" :close-on-press-escape="!savingReport" :show-close="!savingReport" @update:model-value="value => { if (!value) resolveLeave(false) }">
      <p>当前大屏的修改还没有保存，离开前可以保存，或继续编辑。</p>
      <el-alert v-if="leaveSaveError" :title="leaveSaveError" type="error" :closable="false" />
      <template #footer>
        <el-button :disabled="savingReport" @click="resolveLeave(false)">继续编辑</el-button>
        <el-button :disabled="savingReport" @click="resolveLeave(true)">不保存离开</el-button>
        <el-button type="primary" :loading="savingReport" @click="saveAndLeave">保存并离开</el-button>
      </template>
    </el-dialog>
    <ReportRevisionDialog v-if="showRevisions && reportMeta" :report="reportMeta" @close="showRevisions = false" @restored="showRevisions = false; loadReport()" />
    <JimuDrilldownPanel
      v-if="drilldownState"
      :widget="drilldownState.widget"
      :title="drilldownState.title"
      :point="drilldownState.point"
      :rows="drilldownState.rows"
      :headers="drilldownState.headers"
      :loading="drilldownState.loading"
      :error="drilldownState.error"
      :sql="drilldownState.sql"
      :source-sql="drilldownState.sourceSql"
      :execution-time="drilldownState.executionTime"
      :executed="drilldownState.executed"
      :truncated="drilldownState.truncated"
      :statistics="drilldownState.statistics"
      @close="closeDrilldown"
      @export="exportDrilldown"
    />

    <div v-if="widgetPreview" class="modal-backdrop" @mousedown.self="widgetPreview = null">
      <section class="dialog widget-preview-dialog">
        <header>
          <strong>{{ widgetPreview.componentName }}</strong>
          <button @click="widgetPreview = null"><i class="fa-solid fa-xmark"></i></button>
        </header>
        <div class="widget-preview-body">
          <div class="widget-preview-viewport" :style="widgetPreviewViewportStyle">
            <div class="widget-preview-model" :style="widgetPreviewModelStyle">
              <JimuWidgetRenderer :widget="widgetPreview" />
            </div>
          </div>
        </div>
      </section>
    </div>

    <div v-if="showHelp" class="modal-backdrop" @mousedown.self="showHelp = false">
      <section class="dialog help-dialog">
        <header><strong>大屏设计帮助</strong><button @click="showHelp = false"><i class="fa-solid fa-xmark"></i></button></header>
        <div class="dialog-body">
          <div><i class="fa-solid fa-chart-column"></i><strong>添加组件</strong><span>将鼠标悬浮在顶部组件入口，选择分类和组件即可加入画布。</span></div>
          <div><i class="fa-solid fa-up-down-left-right"></i><strong>编辑布局</strong><span>选中组件后可拖动、八向缩放，并在右侧修改精确位置与样式。</span></div>
          <div><i class="fa-solid fa-layer-group"></i><strong>管理图层</strong><span>左侧支持显示、锁定、复制、删除和上下层级调整。</span></div>
          <div><i class="fa-solid fa-wand-magic-sparkles"></i><strong>AI配置</strong><span>使用自然语言调整标题、背景和选中组件，或调用项目AI报表接口。</span></div>
          <button class="reset-button" @click="resetScreen(); showHelp = false">恢复远端原始布局</button>
        </div>
      </section>
    </div>

    <div v-if="showDataset" class="modal-backdrop" @mousedown.self="showDataset = false">
      <section class="dialog dataset-dialog">
        <header><strong>数据集</strong><button @click="showDataset = false"><i class="fa-solid fa-xmark"></i></button></header>
        <div class="dataset-body">
          <aside>
            <button
              v-for="source in dataSources"
              :key="source.id"
              :class="{ active: String(source.id) === activeDataSourceId }"
              @click="loadTables(source)"
            ><i class="fa-solid fa-database"></i><span>{{ source.name || source.datasourceName || source.databaseName }}</span></button>
            <p v-if="!datasetLoading && !dataSources.length">暂无数据源</p>
          </aside>
          <main>
            <div v-if="datasetLoading" class="loading"><i class="fa-solid fa-spinner fa-spin"></i>正在读取数据</div>
            <button v-for="table in tables" v-else :key="table.tableName || table.name" @click="bindTable(table)"><i class="fa-solid fa-table"></i><strong>{{ table.tableName || table.name }}</strong><span>{{ table.tableComment || table.comment || '数据表' }}</span></button>
            <p v-if="!datasetLoading && activeDataSourceId && !tables.length">该数据源暂无可用表</p>
            <p v-if="!datasetLoading && !activeDataSourceId">选择左侧数据源查看数据表</p>
          </main>
        </div>
      </section>
    </div>

    <div v-if="showAi" class="modal-backdrop ai-modal-backdrop" @mousedown.self="closeAiDialog">
      <section class="dialog ai-dialog">
        <header class="ai-dialog-header">
          <div class="ai-header-brand">
            <span class="ai-header-orb"><i class="fa-solid fa-wand-magic-sparkles"></i></span>
            <div>
              <strong>AI 数据分析编排中心</strong>
              <small>INTELLIGENCE WORKSPACE · {{ aiMode === 'screen' ? 'SCREEN ORCHESTRATION' : 'WIDGET ORCHESTRATION' }}</small>
            </div>
          </div>
          <div class="ai-header-meta">
            <span><i class="fa-solid fa-circle"></i> LIVE</span>
            <b>{{ aiDatasourceName }}</b>
          </div>
          <button :disabled="aiLoading" title="关闭" @click="closeAiDialog"><i class="fa-solid fa-xmark"></i></button>
        </header>
        <div v-if="aiRunState === 'idle'" class="dialog-body ai-config-body">
          <div class="ai-context-strip">
            <div class="ai-strip-mode">
              <span class="ai-strip-label">工作模式</span>
              <div class="ai-mode-pills">
                <button :class="{ active: aiMode === 'screen' }" @click="setAiMode('screen')"><i class="fa-solid fa-display"></i>整屏生成</button>
                <button :class="{ active: aiMode === 'widget' }" @click="setAiMode('widget')"><i class="fa-solid fa-chart-line"></i>组件生成</button>
              </div>
            </div>
            <label class="ai-strip-source">
              <span class="ai-strip-label">分析数据源</span>
              <span class="ai-strip-select"><i class="fa-solid fa-database"></i><select v-model="aiDatasourceId" aria-label="分析数据源" :disabled="datasetLoading">
                <option value="">按需求解析已授权数据源</option>
                <option v-for="source in dataSources" :key="source.id" :value="String(source.id)">
                  {{ source.name || source.datasourceName || source.databaseName }}
                </option>
              </select><i class="fa-solid fa-chevron-down"></i></span>
            </label>
            <label v-if="aiMode === 'screen' && dashboardTemplates.length" class="ai-strip-source ai-strip-template dashboard-template-picker">
              <span class="ai-strip-label">模板策略</span>
              <span class="ai-strip-select"><select v-model="templateMode" aria-label="模板策略" @change="templateMode !== 'manual' && (selectedDashboardTemplateId = '')">
                <option value="auto">自动参考模板</option>
                <option v-if="can('templates.view')" value="manual">指定模板 · 只生成 SQL</option>
              </select></span>
            </label>
            <label v-if="aiMode === 'screen' && templateMode === 'manual'" class="ai-strip-source ai-strip-template dashboard-template-picker">
              <span class="ai-strip-label">指定模板</span>
              <span class="ai-strip-select"><i class="fa-solid fa-layer-group"></i><select v-model="selectedDashboardTemplateId" aria-label="指定模板">
                <option value="">请手动选择，无默认模板</option>
                <option v-for="template in dashboardTemplates" :key="template.id" :value="template.id">{{ dashboardTemplateOptionLabel(template) }}</option>
              </select><i class="fa-solid fa-chevron-down"></i></span>
            </label>
            <div class="ai-strip-context"><i class="fa-solid fa-cube"></i><span>当前对象</span><b>{{ aiMode === 'screen' ? '整张分析大屏' : (selectedWidget?.componentName || '新建数据组件') }}</b></div>
            <div class="ai-strip-context ai-strip-canvas"><i class="fa-solid fa-vector-square"></i><span>画布</span><b>{{ widgets.length }} 组件 · {{ page.design.width }}×{{ page.design.height }}</b></div>
            <div class="ai-strip-ready"><i></i><span>执行时读取真实结构</span></div>
          </div>

          <div class="ai-workspace-grid">
            <section class="ai-composer-panel">
              <div class="ai-composer-head">
                <div>
                  <span class="ai-catalog-kicker">COMMAND COMPOSER</span>
                  <h2>告诉 AI 你想看到什么</h2>
                  <p>{{ templateMode === 'manual' && aiMode === 'screen' ? '只生成 SQL，保留所选模板的布局、组件和样式。' : '描述业务目标与指标，AI 会参考模板生成查询、组件与布局。' }}</p>
                </div>
                <button type="button" class="ai-composer-badge ai-composer-run" @click.stop="applyAiConfig"><i class="fa-solid fa-wand-magic-sparkles"></i><span>{{ aiMode === 'screen' ? '立即生成' : '生成组件' }}</span><small>AI READY</small></button>
              </div>
            <div v-if="aiMode === 'screen' && templateMode === 'manual' && activeDashboardTemplate" class="ai-template-reference-banner">
              <img v-if="dashboardTemplatePreview(activeDashboardTemplate)" :src="dashboardTemplatePreview(activeDashboardTemplate)" :alt="`${activeDashboardTemplate.name}参考图`">
              <span v-else class="ai-template-source-lock"><i class="fa-solid fa-lock"></i><b>LYNX</b><small>ORIGINAL</small></span>
              <div><small>SQL ONLY · {{ activeDashboardTemplate.code }}</small><strong>{{ activeDashboardTemplate.name }}</strong><span>只生成 SQL，保留原有画布、组件、标题、单位和样式</span></div>
              <button type="button" title="改为自动参考模板" @click="selectedDashboardTemplateId = ''; templateMode = 'auto'"><i class="fa-solid fa-xmark"></i></button>
            </div>
            <label class="ai-requirement ai-composer-card">
              <span class="ai-requirement-label"><i class="fa-solid fa-pen-nib"></i><b>生成或调整需求</b><em>{{ aiPrompt.length }} CHARACTERS</em></span>
              <textarea v-model="aiPrompt" :placeholder="aiMode === 'screen' ? '例如：生成销售经营驾驶舱，包含收入趋势、区域分布、客户贡献和异常提醒' : '例如：生成最近12个月核心业务指标趋势图，使用真实表字段并适配当前大屏'"></textarea>
              <span class="ai-composer-hint"><i class="fa-solid fa-wand-magic-sparkles"></i> 支持自然语言、指标口径、布局和配色描述</span>
            </label>
            <div class="ai-adjustment-block">
              <div class="ai-adjustment-heading"><span><i class="fa-solid fa-bolt"></i> 快速调整</span><small>点击后追加到输入框</small></div>
              <div class="ai-adjustment-examples">
                <button v-for="example in AI_ADJUSTMENT_EXAMPLES" :key="example" @click="aiPrompt = appendPromptText(aiPrompt, example)">{{ example }}</button>
              </div>
            </div>
            <div class="ai-context-status">
              <i class="fa-solid fa-circle-check"></i>
              <span v-if="aiMode === 'screen'">将分析真实表结构并编排整张画布</span>
              <span v-else>{{ selectedWidget ? `将更新：${selectedWidget.componentName}` : '将在空闲位置创建新组件' }}</span>
            </div>
            <div class="ai-capabilities">
              <span><i class="fa-solid fa-cubes-stacked"></i><b>真实表结构</b><small>按当前授权范围读取</small></span>
              <span><i class="fa-solid fa-diagram-project"></i><b>只读 SQL</b><small>逐项校验表字段与结果</small></span>
              <span><i class="fa-solid fa-palette"></i><b>{{ activeDashboardTemplate?.styleLocked ? '视觉锁定' : '可编辑布局' }}</b><small>{{ activeDashboardTemplate?.styleLocked ? '禁止生成过程改动视觉参数' : '支持模板与自由设计' }}</small></span>
            </div>
            <div v-if="aiIdleNotice" class="ai-idle-notice" role="status">
              <i class="fa-solid fa-circle-exclamation"></i>
              <span>{{ aiIdleNotice }}</span>
            </div>
            <div class="dialog-actions">
              <button type="button" class="secondary-action" @click.stop="closeAiDialog">取消</button>
              <button type="button" class="primary" @click.stop="applyAiConfig"><i class="fa-solid fa-wand-magic-sparkles"></i>{{ aiMode === 'screen' ? '生成看板' : '生成组件' }}<i class="fa-solid fa-arrow-right"></i></button>
            </div>
            </section>

            <aside class="scenario-catalog">
              <div class="ai-assets-head"><div><span class="ai-catalog-kicker">PROMPT ASSETS</span><strong>提示词资产</strong></div><span class="ai-asset-count">{{ aiScenarios.length }} 个方案</span></div>
              <ReportPromptLibraryPanel v-if="can('prompts.view')" v-model="aiPrompt" :mode="aiMode" />
              <div class="scenario-catalog-divider">
                <span><i class="fa-solid fa-sparkles"></i> 内置业务方案</span>
                <small>QUICK START</small>
              </div>
              <div class="scenario-list">
              <button
                v-for="(scenario, scenarioIndex) in aiScenarios"
                :key="scenario.id"
                :class="{ active: selectedAiScenarioId === scenario.id }"
                :style="{ '--scenario-accent': scenario.accent }"
                @click="selectAiScenario(scenario)"
              >
                <i class="scenario-signal"></i>
                <span class="scenario-index">{{ String(scenarioIndex + 1).padStart(2, '0') }}</span>
                <strong>{{ scenario.title }}</strong>
                <p>{{ scenario.summary }}</p>
                <span class="scenario-facts"><b v-for="fact in scenario.facts" :key="fact">{{ fact }}</b></span>
                <i class="fa-solid fa-arrow-right scenario-arrow"></i>
              </button>
              </div>
            </aside>
          </div>
        </div>
        <GenerationStatusPanel
          v-else
          :state="aiRunState"
          :record="generationRecord"
          :scenario-title="activeAiScenario?.title || '自定义分析任务'"
          :datasource-name="aiDatasourceName"
          :mode="aiMode"
          :error="aiError"
          @back="resetAiExecution"
          @retry="applyAiConfig"
        />
      </section>
    </div>
  </main>
</template>

<style scoped>
.editor-load-error { padding: 64px; color: #edf3fa; background: #152336; }
.editor-load-error h1 { font-size: 24px; }
.editor-load-error button { margin: 12px 12px 0 0; padding: 8px 16px; border: 1px solid #869ab6; border-radius: 5px; color: #fff; background: #28415f; cursor: pointer; }
.jimu-replica {
  position: fixed;
  inset: 0;
  z-index: 999;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  min-width: 0;
  overflow: hidden;
  background: #1c2025;
  color: #d8dde2;
  font-family: 'Microsoft YaHei', Arial, sans-serif;
}

.jimu-replica.preview {
  display: block;
  min-width: 0;
  background: #000;
}

.editor-workspace {
  display: grid;
  min-width: 0;
  min-height: 0;
  transition: grid-template-columns .18s ease;
}

.canvas-column {
  position: relative;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}

.left-collapse,
.left-expand,
.right-expand {
  position: absolute;
  z-index: 100;
  display: grid;
  place-items: center;
  width: 20px;
  height: 26px;
  border: 1px solid #555b63;
  background: #272b31;
  color: #aeb5bc;
  cursor: pointer;
}

.left-collapse { top: 505px; left: 0; border-radius: 0 13px 13px 0; }
.left-expand { top: 505px; left: 0; border-radius: 0 13px 13px 0; }
.right-expand { top: 505px; right: 0; border-radius: 13px 0 0 13px; }

.exit-preview {
  position: fixed;
  top: 18px;
  right: 18px;
  z-index: 1000;
  display: grid;
  place-items: center;
  width: 36px;
  height: 36px;
  border: 1px solid rgba(255, 255, 255, .28);
  border-radius: 50%;
  background: rgba(18, 23, 30, .78);
  color: #fff;
  cursor: pointer;
}

.modal-backdrop {
  position: fixed;
  inset: 0;
  z-index: 3000;
  display: grid;
  place-items: center;
  background: rgba(0, 0, 0, .62);
}

.dialog {
  width: 620px;
  border: 1px solid #41464e;
  background: #20242a;
  box-shadow: 0 18px 55px rgba(0, 0, 0, .55);
  color: #dce1e6;
}

.dialog > header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 46px;
  padding: 0 16px;
  border-bottom: 1px solid #393e45;
}

.dialog > header strong { color: #f3f5f7; font-size: 15px; }
.dialog > header button {
  width: 30px;
  height: 30px;
  border: 0;
  background: transparent;
  color: #9fa7af;
  cursor: pointer;
}

.dialog-body { padding: 18px; }

.widget-preview-dialog { width: min(900px, calc(100vw - 80px)); }
.widget-preview-body {
  display: grid;
  place-items: center;
  min-height: 590px;
  padding: 24px;
  overflow: auto;
  background-color: #11161d;
  background-image: radial-gradient(circle, rgba(117, 142, 160, .4) 1px, transparent 1.2px);
  background-size: 16px 16px;
}
.widget-preview-viewport { position: relative; overflow: hidden; background: #000; }
.widget-preview-model { transform-origin: top left; }

.help-dialog .dialog-body {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.help-dialog .dialog-body > div {
  display: grid;
  grid-template-columns: 32px 1fr;
  gap: 5px 10px;
  padding: 14px;
  border: 1px solid #373c43;
  background: #1a1e23;
}

.help-dialog .dialog-body i { grid-row: span 2; color: #168bff; font-size: 22px; }
.help-dialog .dialog-body strong { font-size: 13px; }
.help-dialog .dialog-body span { color: #9fa7af; font-size: 12px; line-height: 1.6; }
.reset-button {
  grid-column: 1 / -1;
  height: 34px;
  border: 1px solid #5f3333;
  background: #2a1c1c;
  color: #ffb6b6;
  cursor: pointer;
}

.dataset-dialog { width: 820px; }
.dataset-body { display: grid; grid-template-columns: 240px 1fr; min-height: 440px; }
.dataset-body aside { padding: 10px; border-right: 1px solid #393e45; background: #1b1f24; }
.dataset-body aside button {
  display: flex;
  align-items: center;
  gap: 9px;
  width: 100%;
  height: 38px;
  border: 0;
  background: transparent;
  color: #cfd4d9;
  text-align: left;
  cursor: pointer;
}
.dataset-body aside button.active,
.dataset-body aside button:hover { background: #30353c; color: #fff; }
.dataset-body aside i { color: #168bff; }
.dataset-body main { display: grid; grid-template-columns: 1fr 1fr; align-content: start; gap: 10px; padding: 14px; }
.dataset-body main button {
  display: grid;
  grid-template-columns: 26px 1fr;
  gap: 4px 8px;
  min-height: 64px;
  padding: 10px;
  border: 1px solid #3a4048;
  background: #1b1f24;
  color: #dbe0e5;
  text-align: left;
}
.dataset-body main button i { grid-row: span 2; color: #43b9ff; font-size: 18px; }
.dataset-body main button span { color: #818b95; font-size: 11px; }
.dataset-body p,
.loading { grid-column: 1 / -1; margin: 60px 0; color: #88919a; text-align: center; }

.ai-dialog {
  width: 680px;
  border-color: #285d82;
  box-shadow: 0 22px 70px rgba(0, 0, 0, .62), 0 0 36px rgba(20, 158, 230, .12);
}
.ai-dialog > header {
  background: #182b3a;
  border-bottom-color: #285d82;
}
.ai-mode-switch {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1px;
  margin-bottom: 16px;
  padding: 3px;
  border: 1px solid #364a59;
  background: #151b20;
}
.ai-mode-switch button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  height: 38px;
  border: 0;
  background: transparent;
  color: #8796a1;
  cursor: pointer;
}
.ai-mode-switch button.active {
  background: #173c56;
  color: #d8f4ff;
  box-shadow: inset 0 0 0 1px #347eaa;
}
.ai-field {
  display: grid;
  grid-template-columns: 70px 1fr;
  align-items: center;
  gap: 12px;
  margin-bottom: 14px;
  color: #aeb9c2;
  font-size: 12px;
}
.ai-field select {
  height: 34px;
  padding: 0 10px;
  border: 1px solid #3d5262;
  background: #151b20;
  color: #e4edf3;
  outline: none;
}
.prompt-examples { display: flex; gap: 8px; margin-bottom: 12px; }
.prompt-examples button {
  height: 28px;
  padding: 0 10px;
  border: 1px solid #424850;
  background: #292e34;
  color: #cbd1d7;
  cursor: pointer;
}
.prompt-examples button:hover { border-color: #45b7e9; color: #fff; }
.ai-dialog textarea {
  width: 100%;
  height: 150px;
  padding: 12px;
  border: 1px solid #424850;
  background: #15181c;
  color: #e8ebee;
  resize: vertical;
  outline: none;
}
.ai-dialog textarea:focus { border-color: #45b7e9; box-shadow: 0 0 0 2px rgba(69, 183, 233, .08); }
.ai-context-status {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
  color: #7fa8be;
  font-size: 12px;
}
.ai-context-status i { color: #56cbff; }
.dialog-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 18px; }
.dialog-actions button {
  min-width: 82px;
  height: 32px;
  border: 1px solid #434951;
  background: #292e34;
  color: #d7dce1;
  cursor: pointer;
}
.dialog-actions .primary { border-color: #278bc0; background: #1677a8; color: #fff; }
.dialog-actions button:disabled { opacity: .55; cursor: wait; }

.ai-modal-backdrop { background: rgba(0, 5, 8, .82); backdrop-filter: blur(3px); }
.ai-dialog {
  width: min(1120px, calc(100vw - 80px));
  max-height: calc(100vh - 64px);
  overflow: hidden;
  border-color: #277054;
  background: #09110f;
  box-shadow: 0 24px 80px rgba(0, 0, 0, .72), 0 0 44px rgba(48, 225, 122, .12);
}
.ai-dialog > header {
  height: 48px;
  border-bottom-color: #255c47;
  background: #0b1814;
}
.ai-dialog > header strong { color: #dfffe9; }
.ai-dialog > header strong i { margin-right: 7px; color: #54ff87; }
.ai-dialog > header button:disabled { cursor: wait; opacity: .35; }
.ai-config-body {
  display: grid;
  grid-template-columns: 390px minmax(0, 1fr);
  min-height: 600px;
  padding: 0;
}
.ai-command-panel {
  padding: 22px;
  border-right: 1px solid #234d3d;
  background: #0c1514;
}
.ai-mode-switch { margin-bottom: 18px; border-color: #315c4a; background: #07100e; }
.ai-mode-switch button { color: #749383; }
.ai-mode-switch button.active { background: #123d2c; color: #d8ffe5; box-shadow: inset 0 0 0 1px #3ca869; }
.ai-field { display: block; margin-bottom: 16px; }
.ai-field > span,.ai-requirement > span { display: block; margin-bottom: 8px; color: #88ad99; font-size: 11px; }
.ai-field select { width: 100%; height: 38px; border-color: #315b4b; background: #07100e; color: #d7f4df; }
.ai-requirement { display: block; }
.ai-dialog .ai-requirement textarea {
  width: 100%;
  height: 240px;
  min-height: 240px;
  max-height: 240px;
  padding: 12px;
  border: 1px solid #315b4b;
  background: #050c0b;
  color: #d8eee0;
  font-size: 12px;
  line-height: 1.65;
  resize: none;
  outline: none;
}
.ai-dialog .ai-requirement textarea:focus { border-color: #49cf7b; box-shadow: 0 0 0 2px rgba(73, 207, 123, .08); }
.ai-adjustment-examples { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 9px; }.ai-adjustment-examples small { flex: 0 0 100%; color: #4f8b69; font: 8px Consolas, monospace; letter-spacing: .11em; }.ai-adjustment-examples small i { margin-right: 6px; color: #54ff87; }.ai-adjustment-examples button { max-width: 100%; height: 25px; padding: 0 8px; overflow: hidden; border: 1px solid #2b5943; background: #08140f; color: #79ae8d; font-size: 9px; text-overflow: ellipsis; white-space: nowrap; cursor: pointer; }.ai-adjustment-examples button:hover { border-color: #54ff87; color: #caffdc; box-shadow: 0 0 10px rgba(84, 255, 135, .1); }
.ai-context-status { margin-top: 13px; color: #73aa8a; }
.ai-context-status i { color: #54ff87; }
.ai-capabilities { display: grid; grid-template-columns: repeat(3, 1fr); gap: 7px; margin-top: 16px; }
.ai-capabilities span { display: grid; place-items: center; gap: 5px; min-height: 48px; border: 1px solid #284e3e; background: #08120f; color: #75a88b; font-size: 9px; }
.ai-capabilities i { color: #4ee486; font-size: 13px; }
.ai-command-panel .dialog-actions { margin-top: 19px; }
.ai-command-panel .dialog-actions button { height: 36px; }
.ai-command-panel .dialog-actions .primary { min-width: 148px; border-color: #45c978; background: #12673a; color: #effff4; box-shadow: 0 0 16px rgba(69, 201, 120, .16); }
.ai-command-panel .dialog-actions .primary i { margin-right: 7px; }
.scenario-catalog { min-width: 0; min-height: 0; overflow: auto; padding: 22px; background-color: #07100f; background-image: linear-gradient(rgba(76, 181, 119, .055) 1px, transparent 1px), linear-gradient(90deg, rgba(76, 181, 119, .045) 1px, transparent 1px); background-size: 28px 28px; }
.scenario-catalog-divider { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin: 20px 0 4px; padding-top: 16px; border-top: 1px solid #28513f; color: #83bd9b; font-size: 11px; }
.scenario-catalog-divider i { margin-right: 6px; color: #54ff87; }
.scenario-catalog-divider small { color: #4e896b; font: 9px Consolas, monospace; letter-spacing: .08em; }
.catalog-heading { display: flex; align-items: center; justify-content: space-between; height: 54px; margin-bottom: 14px; border-bottom: 1px solid #28513f; }
.catalog-heading div { display: grid; gap: 3px; }.catalog-heading small { color: #488165; font: 9px Consolas, monospace; }.catalog-heading strong { color: #e2f7e9; font-size: 16px; }
.catalog-heading > span { padding: 5px 8px; border: 1px solid #315d49; color: #72b68d; font-size: 10px; }
.scenario-list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.scenario-list > button { position: relative; min-width: 0; min-height: 142px; padding: 18px 16px 14px 20px; overflow: hidden; border: 1px solid #254b3c; background: rgba(8, 20, 16, .94); color: #a8c7b4; text-align: left; cursor: pointer; transition: border-color .2s, background .2s, transform .2s; }
.scenario-list > button:last-child:nth-child(odd) { grid-column: 1 / -1; min-height: 122px; }
.scenario-list > button:hover { border-color: var(--scenario-accent); transform: translateY(-2px); }
.scenario-list > button.active { border-color: var(--scenario-accent); background: color-mix(in srgb, var(--scenario-accent) 8%, #081410); box-shadow: inset 3px 0 0 var(--scenario-accent), 0 0 18px color-mix(in srgb, var(--scenario-accent) 10%, transparent); }
.scenario-signal { position: absolute; left: 0; top: 0; width: 2px; height: 100%; background: var(--scenario-accent); opacity: .32; }.active .scenario-signal { opacity: 1; box-shadow: 0 0 12px var(--scenario-accent); }
.scenario-index { position: absolute; right: 12px; top: 9px; color: #38644f; font: 10px Consolas, monospace; }
.scenario-list strong { display: block; max-width: calc(100% - 35px); overflow: hidden; color: #e6faed; font-size: 14px; text-overflow: ellipsis; white-space: nowrap; }
.scenario-list p { margin: 7px 0 13px; overflow: hidden; color: #6e9b82; font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.scenario-facts { display: flex; flex-wrap: wrap; gap: 5px; padding-right: 20px; }.scenario-facts b { padding: 4px 6px; border: 1px solid #284d3e; color: #81b998; font-size: 9px; font-weight: 400; }
.scenario-arrow { position: absolute; right: 14px; bottom: 16px; color: var(--scenario-accent); opacity: .45; }.active .scenario-arrow { opacity: 1; }

/* AI orchestration center: enterprise workspace skin */
.ai-modal-backdrop { background: radial-gradient(circle at 50% 8%, rgba(50, 108, 210, .18), transparent 34%), rgba(2, 7, 18, .86); backdrop-filter: blur(8px); }
.ai-dialog {
  width: min(1220px, calc(100vw - 56px));
  border: 1px solid rgba(83, 155, 235, .58);
  border-radius: 16px;
  background: #081329;
  box-shadow: 0 30px 100px rgba(0, 0, 0, .72), 0 0 70px rgba(45, 126, 255, .16);
}
.ai-dialog > header.ai-dialog-header {
  position: relative;
  height: 72px;
  padding: 0 22px;
  border-bottom: 1px solid rgba(82, 142, 210, .35);
  background: linear-gradient(105deg, #0d2242 0%, #0c1934 58%, #12183d 100%);
}
.ai-dialog-header::after { position: absolute; right: 68px; bottom: -1px; width: 220px; height: 1px; background: linear-gradient(90deg, transparent, #6b8cff, transparent); content: ''; }
.ai-header-brand { display: flex; align-items: center; gap: 12px; min-width: 0; }
.ai-header-orb { display: grid; place-items: center; width: 36px; height: 36px; border: 1px solid #6aa5ff; border-radius: 11px; background: linear-gradient(145deg, rgba(83, 160, 255, .26), rgba(121, 88, 255, .2)); color: #b7dcff; box-shadow: 0 0 22px rgba(77, 151, 255, .25), inset 0 0 14px rgba(119, 115, 255, .18); }
.ai-header-brand strong { display: block; color: #f2f7ff; font-size: 16px; letter-spacing: .03em; }
.ai-header-brand small { display: block; margin-top: 4px; color: #6d9bd0; font: 9px Consolas, monospace; letter-spacing: .12em; }
.ai-header-meta { display: flex; align-items: center; gap: 14px; margin-left: auto; margin-right: 18px; color: #7197c4; font-size: 10px; }
.ai-header-meta span { display: inline-flex; align-items: center; gap: 5px; color: #62e1b2; font: 9px Consolas, monospace; }
.ai-header-meta span i { font-size: 6px; text-shadow: 0 0 8px #62e1b2; }
.ai-header-meta b { max-width: 160px; overflow: hidden; color: #bbd2ed; font-size: 10px; font-weight: 500; text-overflow: ellipsis; white-space: nowrap; }
.ai-dialog > header.ai-dialog-header > button { color: #84a8d4; }
.ai-dialog > header.ai-dialog-header > button:hover { color: #fff; }
.ai-config-body { grid-template-columns: 410px minmax(0, 1fr); min-height: 660px; }
.ai-command-panel { display: flex; flex-direction: column; min-height: 0; overflow: auto; padding: 24px; border-right: 1px solid rgba(78, 132, 202, .28); background: linear-gradient(180deg, #0b1b36 0%, #09152b 100%); }
.ai-workspace-label { display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; color: #6e9dce; font: 9px Consolas, monospace; letter-spacing: .1em; }
.ai-workspace-label i { color: #6e8cff; font-size: 13px; text-shadow: 0 0 12px rgba(103, 132, 255, .8); }
.ai-mode-switch { margin-bottom: 20px; padding: 4px; border: 1px solid rgba(77, 129, 197, .4); border-radius: 11px; background: rgba(3, 12, 28, .72); }
.ai-mode-switch button { justify-content: flex-start; height: 54px; padding: 0 14px; border-radius: 8px; color: #7293bc; text-align: left; }
.ai-mode-switch button > i { width: 25px; color: #5f85ba; font-size: 16px; }
.ai-mode-switch button > span { display: grid; gap: 3px; }
.ai-mode-switch button > span b { color: inherit; font-size: 12px; font-weight: 600; }
.ai-mode-switch button > span small { color: #55759f; font: 8px Consolas, monospace; letter-spacing: .08em; }
.ai-mode-switch button.active { background: linear-gradient(100deg, rgba(47, 125, 224, .33), rgba(111, 84, 241, .26)); color: #eef6ff; box-shadow: inset 0 0 0 1px rgba(99, 164, 255, .7), 0 0 20px rgba(59, 124, 235, .11); }
.ai-mode-switch button.active > i { color: #77c7ff; text-shadow: 0 0 12px rgba(80, 179, 255, .75); }
.ai-mode-switch button.active > span small { color: #80b9eb; }
.ai-field { display: block; margin-bottom: 18px; }
.ai-field-label, .ai-requirement-label { display: flex !important; align-items: center; gap: 7px; margin-bottom: 9px !important; color: #a5c1e4 !important; font-size: 11px !important; }
.ai-field-label i, .ai-requirement-label i { color: #63b5ff; }
.ai-field-label em, .ai-requirement-label em { margin-left: auto; color: #577aa6; font: 8px Consolas, monospace; font-style: normal; letter-spacing: .08em; }
.ai-field select { height: 42px; padding: 0 12px; border: 1px solid rgba(75, 136, 207, .5); border-radius: 8px; background: #07142b; color: #e7f2ff; box-shadow: inset 0 0 16px rgba(15, 65, 126, .13); }
.ai-field select:focus { border-color: #67b7ff; outline: none; box-shadow: 0 0 0 3px rgba(65, 153, 255, .1); }
.ai-requirement { display: block; }
.ai-dialog .ai-requirement textarea { height: 208px; min-height: 208px; max-height: 208px; padding: 14px; border: 1px solid rgba(75, 136, 207, .5); border-radius: 9px; background: linear-gradient(145deg, #061329, #091a35); color: #e5f1ff; font-size: 12px; line-height: 1.7; box-shadow: inset 0 0 24px rgba(19, 72, 137, .1); }
.ai-dialog .ai-requirement textarea::placeholder { color: #55749a; }
.ai-dialog .ai-requirement textarea:focus { border-color: #6abfff; box-shadow: 0 0 0 3px rgba(74, 167, 255, .1), inset 0 0 24px rgba(19, 72, 137, .14); }
.ai-adjustment-examples { gap: 7px; margin-top: 10px; }
.ai-adjustment-examples small { color: #6289b7; font: 8px Consolas, monospace; letter-spacing: .12em; }
.ai-adjustment-examples small i { color: #8c8cff; }
.ai-adjustment-examples button { height: 28px; border: 1px solid rgba(76, 126, 192, .38); border-radius: 6px; background: rgba(15, 40, 76, .72); color: #88acd7; font-size: 9px; }
.ai-adjustment-examples button:hover { border-color: #74baff; background: rgba(35, 87, 157, .42); color: #e4f3ff; box-shadow: 0 0 14px rgba(74, 164, 255, .12); }
.ai-context-status { margin-top: 16px; padding: 10px 11px; border: 1px solid rgba(69, 138, 202, .25); border-radius: 7px; background: rgba(15, 44, 82, .38); color: #86a9d1; font-size: 10px; line-height: 1.45; }
.ai-context-status i { color: #5de0b1; text-shadow: 0 0 10px rgba(93, 224, 177, .5); }
.ai-capabilities { gap: 7px; margin-top: 12px; }
.ai-capabilities span { min-height: 62px; border: 1px solid rgba(71, 126, 193, .3); border-radius: 8px; background: rgba(6, 22, 46, .72); color: #91afd2; }
.ai-capabilities i { color: #78a7ff; font-size: 14px; }
.ai-capabilities b { color: #c6dbf5; font-size: 9px; font-weight: 500; }
.ai-capabilities small { color: #5e82ad; font-size: 8px; }
.ai-command-panel .dialog-actions { align-items: center; margin-top: auto; padding-top: 22px; }
.ai-command-panel .dialog-actions button { height: 40px; border-radius: 8px; }
.ai-command-panel .dialog-actions .secondary-action { border-color: rgba(80, 123, 173, .35); background: rgba(17, 36, 64, .66); color: #89a6c8; }
.ai-command-panel .dialog-actions .primary { display: inline-flex; align-items: center; gap: 9px; min-width: 174px; border: 1px solid #6b83ff; background: linear-gradient(100deg, #356fe7, #714bea); color: #fff; box-shadow: 0 8px 25px rgba(69, 85, 232, .25); }
.ai-command-panel .dialog-actions .primary i:last-child { margin-left: auto; font-size: 10px; }
.scenario-catalog { padding: 25px 27px 30px; background-color: #071329; background-image: linear-gradient(rgba(76, 134, 218, .055) 1px, transparent 1px), linear-gradient(90deg, rgba(76, 134, 218, .045) 1px, transparent 1px), radial-gradient(circle at 85% 10%, rgba(94, 83, 242, .16), transparent 32%); background-size: 28px 28px, 28px 28px, auto; }
.ai-catalog-hero { display: flex; align-items: center; justify-content: space-between; gap: 18px; margin-bottom: 20px; padding-bottom: 20px; border-bottom: 1px solid rgba(78, 132, 202, .26); }
.ai-catalog-kicker { color: #72a8dc; font: 9px Consolas, monospace; letter-spacing: .12em; }
.ai-catalog-kicker i { margin-right: 6px; color: #8b82ff; }
.ai-catalog-hero h2 { margin: 7px 0 5px; color: #f1f6ff; font-size: 22px; font-weight: 600; letter-spacing: .01em; }
.ai-catalog-hero p { margin: 0; color: #7799c0; font-size: 11px; }
.ai-catalog-radar { position: relative; display: grid; place-items: center; width: 76px; height: 76px; border: 1px solid rgba(101, 138, 255, .38); border-radius: 50%; background: radial-gradient(circle, rgba(97, 118, 255, .22), transparent 58%); color: #aeb8ff; font: 8px/1.35 Consolas, monospace; text-align: center; box-shadow: 0 0 28px rgba(86, 105, 255, .18); }
.ai-catalog-radar::before, .ai-catalog-radar::after { position: absolute; inset: 10px; border: 1px dashed rgba(113, 153, 255, .3); border-radius: inherit; content: ''; }
.ai-catalog-radar::after { inset: 22px; border-style: solid; border-color: rgba(113, 153, 255, .4); }
.ai-catalog-radar i { position: absolute; width: 4px; height: 4px; border-radius: 50%; background: #7bd5ff; box-shadow: 0 0 10px #7bd5ff; }
.ai-catalog-radar i:nth-child(1) { top: 5px; left: 35px; }.ai-catalog-radar i:nth-child(2) { right: 8px; bottom: 24px; background: #a48bff; box-shadow: 0 0 10px #a48bff; }.ai-catalog-radar i:nth-child(3) { bottom: 9px; left: 20px; }
.scenario-catalog-divider { margin-top: 22px; padding-top: 18px; border-top-color: rgba(78, 132, 202, .26); color: #8baee0; }
.scenario-catalog-divider i { color: #9b8cff; }
.scenario-catalog-divider small { color: #6689b6; }
.catalog-heading { border-bottom-color: rgba(78, 132, 202, .22); }.catalog-heading small { color: #5e86b6; }.catalog-heading strong { color: #e5efff; }.catalog-heading > span { border-color: rgba(89, 137, 204, .38); color: #7fa8d8; }
.scenario-list > button { border-color: rgba(69, 124, 191, .32); border-radius: 9px; background: rgba(8, 25, 52, .82); color: #9ab7d8; }
.scenario-list > button:hover { border-color: var(--scenario-accent); background: rgba(21, 50, 91, .9); }
.scenario-list > button.active { background: color-mix(in srgb, var(--scenario-accent) 12%, #0a1a37); box-shadow: inset 3px 0 0 var(--scenario-accent), 0 0 22px color-mix(in srgb, var(--scenario-accent) 13%, transparent); }
.scenario-list strong { color: #edf5ff; }.scenario-list p { color: #7094be; }.scenario-facts b { border-color: rgba(84, 136, 201, .32); color: #86aedb; }

/* Three-zone control-room layout */
.ai-config-body { grid-template-columns: 224px minmax(430px, 1fr) 365px; min-height: 690px; background: #081329; }
.ai-context-panel, .ai-composer-panel, .scenario-catalog { min-width: 0; min-height: 0; }
.ai-context-panel { display: flex; flex-direction: column; gap: 16px; overflow: auto; padding: 23px 17px; border-right: 1px solid rgba(78, 132, 202, .25); background: linear-gradient(180deg, #0d1d3a, #09152b); }
.ai-section-index { display: flex; align-items: center; gap: 9px; min-height: 30px; }
.ai-section-index > span:first-child { display: grid; place-items: center; width: 25px; height: 25px; border: 1px solid rgba(107, 155, 232, .55); border-radius: 7px; background: rgba(62, 117, 206, .16); color: #a5c8ff; font: 10px Consolas, monospace; }
.ai-section-index > div { display: grid; gap: 2px; min-width: 0; }.ai-section-index b { color: #e1edff; font-size: 11px; font-weight: 600; }.ai-section-index small { color: #6188b8; font: 8px Consolas, monospace; letter-spacing: .1em; }.ai-section-index > i { margin-left: auto; color: #7b8dff; }
.ai-mode-rail { display: grid; gap: 7px; }
.ai-mode-rail button { display: grid; grid-template-columns: 22px minmax(0, 1fr) 10px; align-items: center; gap: 8px; min-height: 63px; padding: 0 11px; border: 1px solid rgba(76, 126, 197, .3); border-radius: 10px; background: rgba(8, 25, 55, .72); color: #7d9ec9; text-align: left; cursor: pointer; transition: .2s ease; }
.ai-mode-rail button > i:first-child { color: #6288c3; font-size: 14px; }.ai-mode-rail button > i:last-child { opacity: .35; font-size: 9px; }.ai-mode-rail button span { display: grid; gap: 4px; }.ai-mode-rail button b { color: inherit; font-size: 11px; }.ai-mode-rail button small { color: #5578a6; font: 8px Consolas, monospace; letter-spacing: .06em; }
.ai-mode-rail button:hover { border-color: #669ff3; transform: translateX(2px); }.ai-mode-rail button.active { border-color: #6f8cff; background: linear-gradient(110deg, rgba(49, 111, 220, .42), rgba(93, 73, 197, .28)); color: #f0f6ff; box-shadow: 0 0 18px rgba(69, 117, 235, .16), inset 2px 0 #7fb8ff; }.ai-mode-rail button.active > i:first-child { color: #7fd2ff; }.ai-mode-rail button.active > i:last-child { color: #a7b7ff; opacity: 1; }.ai-mode-rail button.active small { color: #88b5ea; }
.ai-context-panel .ai-field { margin: 1px 0 0; }.ai-context-panel .ai-field select { height: 39px; font-size: 11px; }
.ai-context-card { display: grid; gap: 6px; padding: 12px; border: 1px solid rgba(78, 132, 202, .25); border-radius: 9px; background: rgba(5, 18, 43, .7); }.ai-context-card span { display: flex; align-items: center; gap: 6px; color: #77a4d4; font-size: 9px; }.ai-context-card span i { color: #6ea9ff; }.ai-context-card strong { overflow: hidden; color: #dbeaff; font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }.ai-context-card small { color: #5f83b0; font-size: 9px; line-height: 1.45; }
.ai-context-flow { display: grid; gap: 9px; margin-top: auto; padding-top: 8px; }.ai-context-flow span { display: flex; align-items: center; gap: 7px; color: #7092bd; font-size: 9px; }.ai-context-flow i { color: #5ee1b0; font-size: 9px; text-shadow: 0 0 9px rgba(94, 225, 176, .7); }
.ai-composer-panel { display: flex; flex-direction: column; padding: 24px 26px; background: linear-gradient(155deg, #0c1d3b 0%, #08152f 70%, #10173a 100%); }
.ai-composer-panel > .ai-section-index { padding-bottom: 17px; border-bottom: 1px solid rgba(83, 139, 213, .26); }.ai-composer-live { display: inline-flex !important; align-items: center; gap: 5px; width: auto !important; height: 22px; margin-left: auto; padding: 0 8px; border: 1px solid rgba(87, 219, 179, .35); border-radius: 12px; background: rgba(50, 176, 142, .1); color: #6de1b9 !important; font: 8px Consolas, monospace !important; }.ai-composer-live i { width: 5px; height: 5px; border-radius: 50%; background: #6de1b9; box-shadow: 0 0 8px #6de1b9; }
.ai-composer-intro { margin: 30px 0 20px; }.ai-composer-intro h2 { margin: 8px 0 7px; color: #f0f6ff; font-size: 25px; font-weight: 600; letter-spacing: -.01em; }.ai-composer-intro p { max-width: 420px; margin: 0; color: #7c9bc4; font-size: 11px; line-height: 1.65; }
.ai-composer-card { padding: 15px; border: 1px solid rgba(94, 142, 218, .5); border-radius: 12px; background: linear-gradient(145deg, rgba(7, 25, 56, .94), rgba(8, 20, 46, .96)); box-shadow: 0 18px 40px rgba(1, 8, 24, .22), inset 0 0 24px rgba(36, 91, 173, .08); }.ai-composer-card:focus-within { border-color: #7f9cff; box-shadow: 0 0 0 3px rgba(91, 122, 255, .1), 0 18px 40px rgba(1, 8, 24, .24), inset 0 0 24px rgba(36, 91, 173, .13); }.ai-composer-card .ai-requirement-label { margin-bottom: 12px !important; }.ai-composer-card textarea { display: block; height: 245px !important; min-height: 245px !important; max-height: 245px !important; padding: 0 !important; border: 0 !important; background: transparent !important; box-shadow: none !important; font-size: 13px !important; line-height: 1.8 !important; }.ai-composer-card textarea:focus { outline: none; }.ai-composer-hint { display: flex; align-items: center; gap: 6px; margin-top: 11px; padding-top: 11px; border-top: 1px solid rgba(83, 139, 213, .2); color: #6387b6; font-size: 9px; }.ai-composer-hint i { color: #8c82ff; }
.ai-composer-panel .ai-adjustment-examples { margin-top: 18px; }.ai-composer-panel .ai-context-status { margin-top: 17px; }.ai-composer-panel .ai-capabilities { margin-top: 14px; }.ai-composer-panel .dialog-actions { margin-top: auto; padding-top: 24px; }
.scenario-catalog > .ai-section-index { margin-bottom: 19px; padding-bottom: 17px; border-bottom: 1px solid rgba(83, 139, 213, .26); }.ai-asset-count { width: auto !important; height: auto !important; margin-left: auto; color: #6d91bd !important; font: 9px Consolas, monospace !important; }
.scenario-catalog { padding: 23px 19px 28px; }.scenario-catalog .prompt-library-panel { padding: 0; }.scenario-catalog .prompt-template-list { max-height: 225px; }.scenario-catalog .prompt-library-foot { align-items: flex-start; flex-direction: column; }.scenario-catalog .prompt-save-btn { width: 100%; }
.scenario-catalog .scenario-list { grid-template-columns: 1fr; }.scenario-catalog .scenario-list > button { min-height: 112px; padding: 14px 14px 12px 17px; }.scenario-catalog .scenario-list > button:last-child:nth-child(odd) { grid-column: auto; min-height: 112px; }
@media (max-width: 1120px) {
  .ai-config-body { grid-template-columns: 195px minmax(360px, 1fr) 320px; }
  .ai-composer-panel { padding-inline: 20px; }.ai-composer-intro h2 { font-size: 21px; }
}
@media (max-width: 900px) {
  .ai-dialog { width: min(680px, calc(100vw - 24px)); max-height: calc(100vh - 24px); overflow: auto; }
  .ai-config-body { display: flex; flex-direction: column; min-height: 0; }.ai-context-panel, .ai-composer-panel, .scenario-catalog { border-right: 0; border-bottom: 1px solid rgba(78, 132, 202, .25); }.ai-context-panel { display: grid; grid-template-columns: 1fr 1fr; }.ai-context-panel .ai-section-index, .ai-context-panel .ai-mode-rail, .ai-context-panel .ai-context-flow { grid-column: 1 / -1; }.ai-context-flow { display: flex; flex-wrap: wrap; }.ai-composer-card textarea { height: 180px !important; min-height: 180px !important; max-height: 180px !important; }.ai-composer-panel .dialog-actions { margin-top: 24px; }.scenario-catalog { max-height: 580px; }
}

@media (max-height: 760px) {
  .ai-dialog { max-height: calc(100vh - 24px); }
  .ai-config-body { min-height: 550px; }
  .ai-dialog .ai-requirement textarea { height: 190px; min-height: 190px; max-height: 190px; }
}

/* AI command studio: compact context bar, focused editor and asset rail */
.ai-dialog {
  height: min(760px, calc(100vh - 48px));
  max-height: calc(100vh - 48px);
}
.ai-config-body {
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: 100%;
  background: #081329;
}
.ai-context-strip {
  display: grid;
  grid-template-columns: auto minmax(150px, .95fr) minmax(155px, .95fr) minmax(190px, 1fr) minmax(155px, .8fr) auto;
  align-items: center;
  gap: 13px;
  min-height: 70px;
  padding: 11px 21px;
  border-bottom: 1px solid rgba(86, 143, 216, .28);
  background: linear-gradient(100deg, #0d2040 0%, #0a1731 65%, #11183a 100%);
  box-shadow: 0 8px 22px rgba(1, 8, 23, .2);
}
.ai-strip-mode, .ai-strip-source, .ai-strip-context, .ai-strip-ready { min-width: 0; }
.ai-strip-label { display: block; margin-bottom: 6px; color: #6d91bc; font: 8px Consolas, monospace; letter-spacing: .11em; text-transform: uppercase; }
.ai-mode-pills { display: inline-flex; gap: 3px; padding: 3px; border: 1px solid rgba(86, 137, 205, .38); border-radius: 8px; background: rgba(3, 12, 28, .62); }
.ai-mode-pills button { display: inline-flex; align-items: center; gap: 6px; height: 29px; padding: 0 10px; border: 0; border-radius: 5px; background: transparent; color: #7598c3; font-size: 10px; cursor: pointer; transition: .2s ease; }
.ai-mode-pills button i { color: #6e96c8; font-size: 11px; }
.ai-mode-pills button:hover { color: #dceaff; background: rgba(63, 118, 208, .18); }
.ai-mode-pills button.active { color: #f2f7ff; background: linear-gradient(100deg, #3b78df, #6253d3); box-shadow: 0 4px 12px rgba(63, 90, 213, .25); }
.ai-mode-pills button.active i { color: #9be0ff; }
.ai-strip-select { display: flex; align-items: center; height: 34px; padding: 0 9px; border: 1px solid rgba(79, 135, 207, .45); border-radius: 7px; background: rgba(5, 18, 43, .72); color: #72b8ff; }
.ai-strip-select select { min-width: 0; flex: 1; height: 32px; padding: 0 7px; border: 0; outline: none; background: transparent; color: #e7f2ff; font-size: 10px; cursor: pointer; }
.ai-strip-select select option { background: #10254a; color: #e7f2ff; }.ai-strip-select > i:last-child { color: #6c8db9; font-size: 9px; pointer-events: none; }
.ai-strip-template .ai-strip-select { border-color: rgba(112, 116, 242, .48); background: linear-gradient(110deg, rgba(27, 47, 98, .86), rgba(44, 31, 89, .7)); }
.ai-strip-template .ai-strip-select > i:first-child { color: #a397ff; }
.ai-strip-context { display: grid; grid-template-columns: 16px auto; column-gap: 7px; align-items: center; min-height: 34px; padding: 0 10px; border-left: 1px solid rgba(88, 141, 210, .2); }
.ai-strip-context > i { grid-row: 1 / span 2; color: #7fb4ff; font-size: 12px; }.ai-strip-context span { color: #6589b6; font-size: 8px; }.ai-strip-context b { min-width: 0; overflow: hidden; color: #d5e7ff; font-size: 10px; font-weight: 500; text-overflow: ellipsis; white-space: nowrap; }
.ai-strip-ready { display: inline-flex; align-items: center; gap: 6px; justify-content: flex-end; color: #69ddb5; font: 9px Consolas, monospace; white-space: nowrap; }.ai-strip-ready i { width: 6px; height: 6px; border-radius: 50%; background: #69ddb5; box-shadow: 0 0 10px #69ddb5; }
.ai-workspace-grid { display: grid; grid-template-columns: minmax(0, 1.53fr) minmax(320px, .72fr); flex: 1; min-height: 0; overflow: hidden; }
.ai-workspace-grid > .ai-composer-panel { min-width: 0; min-height: 0; overflow: auto; padding: 27px 30px 24px; border-right: 1px solid rgba(84, 138, 209, .26); background: radial-gradient(circle at 18% 18%, rgba(64, 122, 219, .15), transparent 28%), linear-gradient(145deg, #0d2040 0%, #08152f 68%, #10193b 100%); }
.ai-composer-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; margin-bottom: 21px; }.ai-composer-head h2 { margin: 8px 0 6px; color: #f4f7ff; font-size: 26px; font-weight: 600; letter-spacing: -.02em; }.ai-composer-head p { max-width: 470px; margin: 0; color: #7e9fc8; font-size: 11px; line-height: 1.6; }
.ai-composer-badge { display: grid; grid-template-columns: 20px auto; align-items: center; column-gap: 6px; min-width: 87px; padding: 9px 10px; border: 1px solid rgba(91, 137, 228, .35); border-radius: 9px; background: rgba(22, 52, 108, .38); color: #8fafff; }.ai-composer-badge i { grid-row: 1 / span 2; color: #a493ff; }.ai-composer-badge span { color: #dfe7ff; font: 9px Consolas, monospace; }.ai-composer-badge small { color: #6d91be; font-size: 8px; }
.ai-composer-run { font-family: inherit; text-align: left; cursor: pointer; transition: .2s ease; }.ai-composer-run:hover { border-color: #829cff; background: linear-gradient(110deg, rgba(54, 113, 224, .55), rgba(98, 70, 214, .48)); box-shadow: 0 7px 20px rgba(69, 88, 219, .22); transform: translateY(-1px); }.ai-composer-run:hover i { color: #c2b7ff; }
.ai-template-reference-banner { display: grid; grid-template-columns: 128px minmax(0, 1fr) 26px; align-items: center; gap: 13px; min-height: 78px; margin: -3px 0 16px; padding: 8px; border: 1px solid rgba(65, 142, 239, .42); border-radius: 10px; background: linear-gradient(110deg, rgba(11, 37, 79, .9), rgba(29, 26, 79, .7)); box-shadow: inset 0 0 22px rgba(43, 119, 224, .08); }
.ai-template-reference-banner > img { width: 128px; height: 72px; object-fit: cover; border: 1px solid rgba(52, 206, 255, .45); border-radius: 6px; box-shadow: 0 0 14px rgba(36, 148, 255, .14); }
.ai-template-source-lock { display: grid; width: 128px; height: 72px; place-items: center; align-content: center; gap: 2px; color: #8cc8ff; background: linear-gradient(145deg, #102d52, #101b3d); border: 1px solid rgba(52, 206, 255, .4); border-radius: 6px; }
.ai-template-source-lock > i { margin-bottom: 2px; color: #81bfff; font-size: 15px; }.ai-template-source-lock > b { color: #d9ecff; font: 10px Consolas, monospace; letter-spacing: .12em; }.ai-template-source-lock > small { color: #6389b8; font: 7px Consolas, monospace; letter-spacing: .1em; }
.ai-template-reference-banner > div { display: grid; gap: 4px; min-width: 0; }.ai-template-reference-banner small { color: #788fdd; font: 8px Consolas, monospace; letter-spacing: .09em; }.ai-template-reference-banner strong { overflow: hidden; color: #edf6ff; font-size: 12px; font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }.ai-template-reference-banner span { color: #7899c3; font-size: 9px; }
.ai-template-reference-banner > button { display: grid; place-items: center; width: 25px; height: 25px; border: 1px solid rgba(99, 139, 196, .28); border-radius: 6px; background: rgba(9, 23, 51, .55); color: #7292bb; cursor: pointer; }.ai-template-reference-banner > button:hover { border-color: #799dff; color: #e6efff; }
.ai-composer-card { margin: 0; padding: 16px 17px 13px; border-color: rgba(100, 151, 230, .52); border-radius: 12px; background: linear-gradient(145deg, rgba(7, 25, 57, .92), rgba(8, 20, 47, .96)); box-shadow: 0 17px 35px rgba(2, 8, 23, .2), inset 0 0 28px rgba(46, 101, 190, .07); }.ai-composer-card textarea { height: 235px !important; min-height: 235px !important; max-height: 235px !important; }
.ai-adjustment-block { margin-top: 16px; }.ai-adjustment-heading { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }.ai-adjustment-heading span { color: #b2c9e6; font-size: 10px; font-weight: 600; }.ai-adjustment-heading span i { margin-right: 6px; color: #a494ff; }.ai-adjustment-heading small { color: #6689b5; font-size: 9px; }.ai-adjustment-examples { display: flex; flex-wrap: wrap; gap: 7px; margin: 0; }.ai-adjustment-examples small { display: none; }.ai-adjustment-examples button { max-width: none; height: 29px; padding: 0 10px; border-radius: 6px; background: rgba(16, 42, 84, .7); color: #8eafd6; }
.ai-workspace-grid .ai-context-status { margin-top: 15px; }.ai-workspace-grid .ai-capabilities { margin-top: 12px; }.ai-capabilities span { position: relative; min-height: 57px; }.ai-capabilities span em { position: absolute; top: 7px; right: 8px; color: #5ee0ae; font: 8px Consolas, monospace; font-style: normal; }
.ai-workspace-grid .dialog-actions { position: relative; z-index: 30; display: flex; margin-top: 18px; padding-top: 17px; border-top: 1px solid rgba(84, 138, 209, .2); pointer-events: auto; }.ai-workspace-grid .dialog-actions button { position: relative; z-index: 31; pointer-events: auto; }.ai-workspace-grid .dialog-actions .primary { min-width: 183px; box-shadow: 0 8px 20px rgba(52, 104, 229, .24); }
.ai-idle-notice { display: flex; align-items: flex-start; gap: 8px; margin-top: 15px; padding: 10px 12px; border: 1px solid rgba(255, 177, 84, .55); border-radius: 8px; background: rgba(104, 54, 20, .24); color: #ffd39b; font-size: 10px; line-height: 1.5; }
.ai-idle-notice i { margin-top: 2px; color: #ffb354; }
.ai-workspace-grid > .scenario-catalog { min-width: 0; min-height: 0; overflow-y: auto; overflow-x: hidden; padding: 23px 20px 25px; background: linear-gradient(180deg, #0b1834, #081329); background-image: radial-gradient(circle at 88% 5%, rgba(102, 84, 239, .16), transparent 30%), linear-gradient(rgba(78, 135, 219, .045) 1px, transparent 1px), linear-gradient(90deg, rgba(78, 135, 219, .04) 1px, transparent 1px); background-size: auto, 28px 28px, 28px 28px; }
.template-style-lock { display: flex; min-height: 42px; align-items: center; gap: 10px; padding: 7px 14px; color: #35516f; background: #f6f9fc; border-bottom: 1px solid #d7e0e9; }
.template-style-lock > i { display: grid; width: 26px; height: 26px; place-items: center; flex: 0 0 auto; color: #1f5d96; background: #e4eef8; border-radius: 5px; font-size: 11px; }
.template-style-lock > div { display: grid; min-width: 0; gap: 1px; }
.template-style-lock strong { color: #173a5b; font-size: 11px; font-weight: 650; }
.template-style-lock span { overflow: hidden; color: #6b7f94; font-size: 9px; text-overflow: ellipsis; white-space: nowrap; }
.template-style-lock code { margin-left: auto; color: #58738f; font-size: 8px; }
.ai-assets-head { display: flex; align-items: flex-end; justify-content: space-between; gap: 12px; margin-bottom: 15px; padding-bottom: 15px; border-bottom: 1px solid rgba(84, 138, 209, .25); }.ai-assets-head > div { display: grid; gap: 5px; }.ai-assets-head strong { color: #edf4ff; font-size: 17px; font-weight: 600; }.ai-assets-head .ai-catalog-kicker { color: #7aa6d9; }.ai-assets-head .ai-asset-count { margin: 0; }
.ai-workspace-grid .prompt-library-panel { padding: 0; background: transparent; border: 0; }.ai-workspace-grid :deep(.prompt-library-panel) { padding: 0; background: transparent; border: 0; }.ai-workspace-grid :deep(.prompt-library-header) { margin-bottom: 11px; }.ai-workspace-grid :deep(.prompt-template-list) { max-height: 206px; padding-right: 2px; }.ai-workspace-grid :deep(.prompt-library-foot) { flex-direction: column; align-items: stretch; gap: 8px; }.ai-workspace-grid :deep(.prompt-save-btn) { width: 100%; }
.ai-workspace-grid .scenario-catalog-divider { margin-top: 18px; padding-top: 15px; }.ai-workspace-grid .scenario-list { grid-template-columns: 1fr; gap: 8px; }.ai-workspace-grid .scenario-list > button { min-height: 102px; padding: 13px 13px 11px 16px; border-radius: 8px; }.ai-workspace-grid .scenario-list p { margin: 6px 0 10px; }.ai-workspace-grid .scenario-list > button:last-child:nth-child(odd) { min-height: 102px; }
@media (max-width: 1040px) {
  .ai-context-strip { grid-template-columns: auto minmax(145px, 1fr) minmax(145px, 1fr) minmax(170px, 1fr) auto; }.ai-strip-canvas { display: none; }.ai-workspace-grid { grid-template-columns: minmax(0, 1.35fr) minmax(290px, .75fr); }.ai-workspace-grid > .ai-composer-panel { padding-inline: 23px; }
}
@media (max-width: 790px) {
  .ai-dialog { width: min(680px, calc(100vw - 24px)); height: calc(100vh - 24px); max-height: calc(100vh - 24px); }.ai-context-strip { grid-template-columns: 1fr 1fr; gap: 9px 12px; padding: 12px 16px; }.ai-strip-mode { grid-column: 1 / -1; }.ai-strip-ready { justify-content: flex-start; }.ai-workspace-grid { display: flex; flex-direction: column; overflow: auto; }.ai-workspace-grid > .ai-composer-panel { flex: 0 0 auto; overflow: visible; padding: 22px 18px; border-right: 0; border-bottom: 1px solid rgba(84, 138, 209, .26); }.ai-workspace-grid > .scenario-catalog { flex: 0 0 auto; max-height: 460px; }.ai-composer-head h2 { font-size: 22px; }.ai-composer-card textarea { height: 180px !important; min-height: 180px !important; max-height: 180px !important; }
}
@media (prefers-reduced-motion: reduce) {
  .ai-mode-pills button, .ai-adjustment-examples button { transition: none; }
}

:global(.el-message), :global(.el-overlay-message-box) { z-index: 4200 !important; }
</style>
