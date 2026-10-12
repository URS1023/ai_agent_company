<script setup lang="ts">
/**
 * 大屏模板中心页面（路由 /templates，权限 templates.view）。
 * 职责：模板列表与关键字过滤、在线预览、新建/编辑/复制/启停、历史版本与使用记录查看、回收站（已删除模板）恢复，以及"用模板生成大屏"。
 * 关键交互：使用模板走 DashboardTemplateUseDialog（选择数据源 → 生成），生成中禁止重复提交；
 *   编辑/删除/恢复均为破坏性操作，需 ElMessageBox 二次确认；不再使用的模板优先停用而非删除。
 * 权限：新增/编辑/删除/使用分别受 can('templates.create' | 'templates.edit' | 'templates.delete' | 'dashboards.create') 控制（模板见模板内的按钮 v-if）。
 * 错误呈现：列表错误进 error（页面提示条），操作类错误用 ElMessage 直接提示。
 * 约束：模板内容（content）为只读渲染数据，前端仅做缩略预览换算（miniCanvasStyle/widgetStyle 把绝对坐标换算成百分比）。
 */
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRouter } from 'vue-router'
import { can, del, get, post, put } from '../api/request'
import DashboardTemplateUseDialog, {
  type DashboardTemplate,
  type DataSourceSummary,
  type TemplateUseRequest,
} from '../components/reports/DashboardTemplateUseDialog.vue'
import { createClientId } from '../utils/randomId'
import ReferenceTemplatePreview from '../components/reports/ReferenceTemplatePreview.vue'

const router = useRouter()
const items = ref<Record<string, any>[]>([])
const keyword = ref('')
const loading = ref(false)
const error = ref('')
const preview = ref<Record<string, any> | null>(null)
const dialog = ref(false)
const editing = ref<Record<string, any> | null>(null)
const form = ref({ name: '', description: '', category: '自定义', tags: '', preview: '', content: '', version: 1, enabled: true })
const lifecycleDialog = ref(false)
const lifecycleTab = ref('revisions')
const lifecycleItem = ref<Record<string, any> | null>(null)
const revisionRows = ref<Record<string, any>[]>([])
const usageRows = ref<Record<string, any>[]>([])
const lifecycleLoading = ref(false)
const recycleDialog = ref(false)
const recycleLoading = ref(false)
const deletedItems = ref<Record<string, any>[]>([])
const useDialog = ref(false)
const useTemplate = ref<DashboardTemplate | null>(null)
const useDatasources = ref<DataSourceSummary[]>([])
const useSourcesLoading = ref(false)
const useSaving = ref(false)
// 生成请求断线后保留同一个防重复键，用户重试时由服务端回放同一草稿而不是重复创建。
const useRequestKey = ref('')
const visible = computed(() => items.value.filter(item => [item.name, item.description, ...(item.tags || [])].join(' ').toLowerCase().includes(keyword.value.toLowerCase())))
const enabledCount = computed(() => items.value.filter(item => item.enabled).length)
const sourceStatus = (item: Record<string, any>) => item.deleted ? '已删除' : item.sourceDeleted ? '源记录已删除' : item.sourceEmpty ? '源配置为空' : '模板已停用'
const sourceType = (item: Record<string, any>) => item.sourceKind === 'hifi-reference'
  ? '参考高保真模板'
  : item.sourceKind === 'lynx-report-screen'
    ? '智慧云脑模板'
    : '自定义模板'
const miniCanvasStyle = (item: Record<string, any>) => {
  const canvas = item.content?.canvas || {}
  return {
    backgroundColor: canvas.backgroundColor || '#07111f',
    backgroundImage: canvas.backgroundImage ? `url("${String(canvas.backgroundImage).replace(/"/g, '%22')}")` : undefined,
    backgroundSize: 'cover',
    backgroundPosition: 'center',
  }
}
const widgetStyle = (item: Record<string, any>, widget: Record<string, any>) => {
  const canvas = item.content?.canvas || {}
  const width = Math.max(1, Number(canvas.width) || 1920)
  const height = Math.max(1, Number(canvas.height) || 1080)
  return {
    left: `${Math.max(0, Number(widget.x) || 0) / width * 100}%`,
    top: `${Math.max(0, Number(widget.y) || 0) / height * 100}%`,
    width: `${Math.max(1, Number(widget.w) || 1) / width * 100}%`,
    height: `${Math.max(1, Number(widget.h) || 1) / height * 100}%`,
  }
}

async function load() {
  loading.value = true; error.value = ''
  try { items.value = (await get('/api/v1/report-templates')).data }
  catch (caught) { error.value = (caught as Error).message }
  finally { loading.value = false }
}
function edit(item?: Record<string, any>, copy = false) {
  editing.value = item && !copy ? item : null
  form.value = { name: item ? item.name + (copy ? ' · 副本' : '') : '', description: item?.description || item?.summary || '',
    category: item?.category || '自定义', tags: (item?.tags || []).join(','), preview: item?.preview || '',
    content: JSON.stringify(item?.content || { canvas: { width: 1920, height: 1080 }, widgets: [] }, null, 2),
    version: item?.version || 1, enabled: item?.enabled !== false }
  dialog.value = true
}
/**
 * 保存模板：无 editing 时 POST /api/v1/report-templates 新建，否则 PUT 更新（带 version 乐观锁）。
 * 关键字段：name/description/category/tags（逗号分隔转数组）、content（JSON 文本解析后提交）。
 * 副作用：成功关闭弹窗并刷新列表；后端会清理模板里的私有数据与绑定（提示文案已说明）。
 * 失败表现：JSON 解析错误与接口错误都用 ElMessage.error 提示，弹窗保持打开以便修正。
 */
async function save() {
  try {
    const payload = { ...form.value, tags: form.value.tags.split(',').map(value => value.trim()).filter(Boolean), content: JSON.parse(form.value.content) }
    if (editing.value) await put('/api/v1/report-templates/' + editing.value.id, payload)
    else await post('/api/v1/report-templates', payload)
    dialog.value = false; await load(); ElMessage.success('模板已保存，私有数据与绑定已清理')
  } catch (caught) { ElMessage.error((caught as Error).message) }
}
/**
 * 打开"使用模板"流程：停用/源记录已删除的模板直接拦截并提示原因（不给用户误导性的入口）。
 * 副作用：按权限加载可用数据源（无 datasources.view 时给空列表，不请求）；随后由弹窗收集使用参数。
 */
async function use(item: Record<string, any>) {
  if (!item.enabled) { ElMessage.warning(sourceStatus(item)); return }
  useTemplate.value = item as DashboardTemplate
  useRequestKey.value = ''
  useDialog.value = true
  useSourcesLoading.value = true
  try {
    useDatasources.value = can('datasources.view')
      ? ((await get('/api/v1/analytics/datasources')).data || []).filter((source: Record<string, any>) => source.enabled)
      : []
  } catch (caught) {
    useDatasources.value = []
    ElMessage.error((caught as Error).message || '读取数据源失败')
  } finally {
    useSourcesLoading.value = false
  }
}
/**
 * 用模板生成大屏：两种模式 —— 'empty' 原样建空数据报表（POST /api/v1/reports），
 *   否则按需求文档 + 数据源由 AI 仅填充数据绑定后保存草稿（POST /api/v1/ai/business-actions/dashboards/drafts，等待服务端终态）。
 * 关键约束：必须拿到 reportId 才能跳转，缺失即视为失败（提示"服务端未返回报表标识"）；useSaving 期间禁止重复提交。
 * 失败表现：ElMessage.error 展示后端 message，弹窗保留用户已填的需求与数据源选择。
 */
async function submitUse(request: TemplateUseRequest) {
  const template = useTemplate.value
  if (!template) return
  useSaving.value = true
  if (request.mode !== 'empty' && !useRequestKey.value) useRequestKey.value = createClientId()
  try {
    let reportId = ''
    if (request.mode === 'empty') {
      const created = (await post('/api/v1/reports', {
        reportName: request.reportName,
        templateMode: 'manual',
        templateId: template.id,
      })).data
      reportId = created.id
      ElMessage.success('已按模板原样创建空数据报表')
    } else {
      const canvas = template.content?.canvas || {}
      const result = (await post('/api/v1/ai/business-actions/dashboards/drafts', {
        requestKey: useRequestKey.value,
        requirementDocument: request.requirementDocument,
        title: request.reportName,
        datasourceIds: request.datasourceIds,
        templateMode: 'manual',
        templateId: template.id,
        width: Number(canvas.width) || 1920,
        height: Number(canvas.height) || 1080,
      })).data
      reportId = result.reportId
      ElMessage.success('已仅填充数据绑定并保存草稿')
    }
    if (!reportId) throw new Error('服务端未返回报表标识')
    useDialog.value = false
    useRequestKey.value = ''
    await load()
    await router.push('/report/designer/' + reportId)
  } catch (caught) {
    // 参数、权限或冲突属于确定失败，必须换键；网络中断保留键供用户安全重试。
    const status = (caught as any)?.response?.status
    if ([400, 401, 403, 404, 409, 422].includes(status)) useRequestKey.value = ''
    ElMessage.error((caught as Error).message || '使用模板失败')
  } finally {
    useSaving.value = false
  }
}
/**
 * 删除模板：DELETE /api/v1/report-templates/{id}；属软删除（进入当前工作区回收站），已有报表不受影响。
 * 交互：先 ElMessageBox 二次确认；用户点"取消"不视为错误（因此过滤 cancel/close）。
 */
async function remove(item: Record<string, any>) {
  try {
    await ElMessageBox.confirm('删除后模板会进入当前工作区回收站，已有报表不受影响。', '删除模板', { type: 'warning', confirmButtonText: '删除' })
    await del('/api/v1/report-templates/' + item.id); await load(); ElMessage.success('模板已移入回收站')
  } catch (caught) { if (caught !== 'cancel' && caught !== 'close') ElMessage.error((caught as Error).message) }
}
/** 打开回收站：GET /api/v1/report-templates?includeDeleted=true 后在前端筛出 deleted 记录（接口无独立回收站端点）。 */
async function openRecycleBin() {
  recycleDialog.value = true
  recycleLoading.value = true
  try {
    const rows = (await get('/api/v1/report-templates?includeDeleted=true')).data
    deletedItems.value = rows.filter((item: Record<string, any>) => item.deleted)
  } catch (caught) { ElMessage.error((caught as Error).message) }
  finally { recycleLoading.value = false }
}
/** 从回收站恢复模板：POST /api/v1/report-templates/{id}/recover；成功后本地移除该行并刷新列表。 */
async function recover(item: Record<string, any>) {
  try {
    await post('/api/v1/report-templates/' + item.id + '/recover', {})
    deletedItems.value = deletedItems.value.filter(row => row.id !== item.id)
    await load()
    ElMessage.success('模板已恢复')
  } catch (caught) { ElMessage.error((caught as Error).message) }
}
/** 重新发布停用的模板：POST /api/v1/report-templates/{id}/publish（提交 version 做乐观锁）；发布后立即出现在模板选择列表。 */
async function publish(item: Record<string, any>) {
  try {
    await ElMessageBox.confirm('重新发布后，该版本会立即出现在模板选择列表中。', '发布模板', { type: 'warning' })
    await post('/api/v1/report-templates/' + item.id + '/publish', { version: item.version })
    await load(); ElMessage.success('模板已重新发布')
  } catch (caught) { if (caught !== 'cancel' && caught !== 'close') ElMessage.error((caught as Error).message) }
}
/** 导出模板为 JSON 包：GET /api/v1/report-templates/{id}/export，前端序列化后触发浏览器下载（objectURL 1s 后释放）。 */
async function exportTemplate(item: Record<string, any>) {
  try {
    const data = (await get('/api/v1/report-templates/' + item.id + '/export')).data
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }))
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `dashboard-template-${item.id}.json`
    anchor.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (caught) { ElMessage.error((caught as Error).message) }
}
async function importPackage(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  input.value = ''
  if (file.size > 8 * 1024 * 1024) { ElMessage.error('模板包不能超过 8 MB'); return }
  try {
    const value = JSON.parse(await file.text())
    if (value?.schemaVersion !== 'report-template-package-v1') throw new Error('请选择由模板中心导出的模板包')
    await post('/api/v1/report-templates/import', value)
    await load(); ElMessage.success('模板包已校验并导入')
  } catch (caught) { ElMessage.error((caught as Error).message || '模板包不是合法 JSON') }
}
async function openLifecycle(item: Record<string, any>) {
  lifecycleItem.value = item
  lifecycleDialog.value = true
  lifecycleTab.value = 'revisions'
  lifecycleLoading.value = true
  try {
    const [revisions, usage] = await Promise.all([
      get('/api/v1/report-templates/' + item.id + '/revisions'),
      get('/api/v1/report-templates/' + item.id + '/usage'),
    ])
    revisionRows.value = revisions.data
    usageRows.value = usage.data
  } catch (caught) { ElMessage.error((caught as Error).message) }
  finally { lifecycleLoading.value = false }
}
async function restore(revision: Record<string, any>) {
  const item = lifecycleItem.value
  if (!item) return
  try {
    await ElMessageBox.confirm(`恢复 v${revision.revision}？当前内容不会覆盖历史，而会生成一个新修订。`, '恢复模板', { type: 'warning' })
    await post('/api/v1/report-templates/' + item.id + '/restore', { version: item.version, revision: revision.revision })
    await load()
    const refreshed = items.value.find(row => row.id === item.id)
    if (refreshed) await openLifecycle(refreshed)
    ElMessage.success('历史版本已恢复为新修订')
  } catch (caught) { if (caught !== 'cancel' && caught !== 'close') ElMessage.error((caught as Error).message) }
}
async function manage(item: Record<string, any>, command: string) {
  document.querySelectorAll<HTMLDetailsElement>('details.template-manage[open]').forEach(node => node.removeAttribute('open'))
  if (command === 'history') await openLifecycle(item)
  else if (command === 'export') await exportTemplate(item)
  else if (command === 'copy') edit(item, true)
  else if (command === 'edit') edit(item)
  else if (command === 'delete') await remove(item)
  else if (command === 'publish') await publish(item)
}
async function importJson(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file) return
  if (file.size > 8 * 1024 * 1024) { ElMessage.error('配置文件不能超过 8 MB'); return }
  try { const value = JSON.parse(await file.text()); form.value.content = JSON.stringify(value.content || value, null, 2) }
  catch { ElMessage.error('请选择有效的画布 JSON 文件') }
}
onMounted(load)
</script>
<template>
  <main class="business-page" aria-label="大屏模板中心">
    <div class="business-heading"><div><p class="eyebrow">DASHBOARD TEMPLATES</p><h1>大屏模板中心</h1><p>20 套参考大屏采用原图装饰与独立数据组件。创建参考示例副本后，可修改静态数据或绑定授权数据源；布局与外框保持固定。</p></div><div class="template-heading-actions"><el-button v-if="can('templates.edit')" @click="openRecycleBin">回收站</el-button><label v-if="can('templates.create')" class="template-package-import">导入模板包<input type="file" accept="application/json,.json" aria-label="导入大屏模板包" @change="importPackage" /></label><el-button v-if="can('templates.create')" type="primary" @click="edit()">新增模板</el-button></div></div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <div class="list-toolbar"><el-input v-model="keyword" placeholder="搜索模板名称、场景或标签" clearable aria-label="搜索大屏模板" /><span>{{ visible.length }} 条记录 · {{ enabledCount }} 条可用</span></div>
    <div class="template-grid" v-loading="loading">
      <article v-for="item in visible" :key="item.id" class="template-item" :data-template-id="item.id" :class="{ disabled: !item.enabled, locked: item.styleLocked }">
        <button class="template-image" @click="preview = item" :aria-label="'查看 ' + item.name + ' 预览'">
          <img v-if="item.preview" :src="item.preview" :alt="item.name + ' 模板预览'" loading="lazy" />
          <span v-else-if="item.content?.widgets?.length" class="source-template-canvas" :style="miniCanvasStyle(item)" aria-hidden="true"><i v-for="widget in item.content.widgets" :key="widget.id" :class="`widget-${widget.type || 'chart'}`" :style="widgetStyle(item, widget)"></i></span>
          <span v-else class="source-template-empty">源配置为空</span>
          <b class="source-template-status">{{ item.enabled ? (item.styleLocked ? '视觉锁定' : '可用') : sourceStatus(item) }}</b>
        </button>
        <div class="template-copy"><div class="prompt-item-heading"><h2>{{ item.name }}</h2><el-tag size="small" :type="item.sourceKind === 'lynx-report-screen' ? 'primary' : item.sourceKind ? 'warning' : item.builtin ? 'info' : 'success'">{{ sourceType(item) }}</el-tag></div><p>{{ item.description || item.summary }}</p><div class="template-tags"><span v-for="tag in (item.tags || []).slice(0, 3)" :key="tag">{{ tag }}</span><span>v{{ item.version }}</span><span>{{ item.usageCount || 0 }} 次使用</span><span v-if="item.styleLocked">只填数据</span><span v-if="item.sourceTable">{{ item.sourceTable }} · {{ item.sourceRecordId }}</span></div><div class="template-actions">
          <details class="template-manage"><summary :aria-label="'管理 ' + item.name">管理</summary><div class="template-manage-menu" role="menu"><button role="menuitem" type="button" @click="manage(item, 'history')">版本与使用</button><button role="menuitem" type="button" @click="manage(item, 'export')">导出模板包</button><button v-if="can('templates.create') && !item.styleLocked" role="menuitem" type="button" @click="manage(item, 'copy')">复制为自定义模板</button><button v-if="!item.builtin && can('templates.edit')" role="menuitem" type="button" @click="manage(item, 'edit')">编辑模板</button></div></details><el-button v-if="can('templates.delete')" plain type="danger" size="small" @click="manage(item, 'delete')">删除</el-button>
          <el-button v-if="can('dashboards.create')" type="primary" size="small" :disabled="!item.enabled" @click="use(item)">{{ item.enabled ? '使用此模板' : '已停用' }}</el-button>
        </div></div>
      </article>
    </div>
    <el-empty v-if="!visible.length && !loading" :description="keyword ? '没有匹配的模板，请修改或清空搜索条件' : '当前没有大屏模板'" />
    <el-dialog :model-value="Boolean(preview)" :title="preview?.name || '模板预览'" width="min(1280px, 96vw)" @close="preview = null">
      <ReferenceTemplatePreview v-if="preview?.sourceKind === 'hifi-reference'" :content="preview.content" />
      <img v-else-if="preview?.preview" :src="preview.preview" :alt="preview.name + ' 大屏预览'" class="large-template-preview" />
      <div v-else-if="preview?.content?.widgets?.length" class="large-source-template-canvas"><span class="source-template-canvas" :style="miniCanvasStyle(preview)"><i v-for="widget in preview.content.widgets" :key="widget.id" :class="`widget-${widget.type || 'chart'}`" :style="widgetStyle(preview, widget)"></i></span></div>
      <p class="muted-note">{{ preview?.sourceKind === 'hifi-reference' ? '当前为参考 JPG 示例。装饰和布局取自原图；修改或绑定数据后，数据区域使用当前结果重绘。原图序列无法从截图完整恢复，编辑框中的示例序列仅用于填写格式参考。' : preview?.styleLocked ? '源模板视觉属性已锁定，使用时仅写入授权数据绑定与结果。' : '这是模板视觉参考，使用后可继续编辑。' }}</p>
    </el-dialog>
    <el-dialog v-model="recycleDialog" title="模板回收站" width="min(760px, 96vw)">
      <div v-loading="recycleLoading" class="template-recycle-list">
        <div v-for="item in deletedItems" :key="item.id"><span><strong>{{ item.name }}</strong><small>{{ sourceType(item) }}</small></span><el-button v-if="can('templates.edit')" size="small" @click="recover(item)">恢复</el-button></div>
        <el-empty v-if="!recycleLoading && !deletedItems.length" description="回收站为空" />
      </div>
    </el-dialog>
    <el-dialog v-model="lifecycleDialog" :title="lifecycleItem?.name || '模板版本'" width="min(920px, 96vw)">
      <div class="template-lifecycle-summary"><span>当前 v{{ lifecycleItem?.version }}</span><span>{{ lifecycleItem?.enabled ? '可用' : '已停用' }}</span><span>{{ lifecycleItem?.usageCount || 0 }} 次实际使用</span></div>
      <el-tabs v-model="lifecycleTab" v-loading="lifecycleLoading">
        <el-tab-pane :label="`版本（${revisionRows.length}）`" name="revisions"><div class="lifecycle-table-wrap" role="region" aria-label="模板版本历史" tabindex="0"><table class="lifecycle-table"><thead><tr><th>版本</th><th>动作</th><th>状态</th><th>时间</th><th>操作</th></tr></thead><tbody><tr v-for="row in revisionRows" :key="row.revision"><td>v{{ row.revision }}</td><td>{{ row.reason }}</td><td>{{ row.snapshot?.enabled ? '可用' : '停用' }}</td><td>{{ row.createTime ? new Date(row.createTime).toLocaleString() : '系统来源' }}</td><td><el-button v-if="row.restorable && can('templates.edit') && row.revision !== lifecycleItem?.version" link size="small" @click="restore(row)">恢复为新版本</el-button><span v-else>—</span></td></tr></tbody></table></div></el-tab-pane>
        <el-tab-pane :label="`使用记录（${usageRows.length}）`" name="usage"><div class="lifecycle-table-wrap" role="region" aria-label="模板使用记录" tabindex="0"><table class="lifecycle-table"><thead><tr><th>使用时间</th><th>模板版本</th><th>创建报表</th><th>操作人</th></tr></thead><tbody><tr v-for="row in usageRows" :key="row.id"><td>{{ new Date(row.createTime).toLocaleString() }}</td><td>v{{ row.templateVersion }}</td><td><el-button v-if="row.reportId" link size="small" @click="lifecycleDialog = false; router.push('/report/view/' + row.reportId)">{{ row.detail?.templateName || row.reportId }}</el-button><span v-else>—</span></td><td>{{ row.createdBy }}</td></tr><tr v-if="!usageRows.length"><td colspan="4">尚未通过此模板创建报表。</td></tr></tbody></table></div></el-tab-pane>
      </el-tabs>
    </el-dialog>
    <el-dialog v-model="dialog" :title="editing ? '编辑自定义模板' : '新增自定义模板'" width="min(900px, 96vw)" :close-on-click-modal="false">
      <el-form label-position="top"><div class="two-column-form"><el-form-item label="模板名称" required><el-input v-model="form.name" /></el-form-item><el-form-item label="分类"><el-input v-model="form.category" /></el-form-item><el-form-item label="标签"><el-input v-model="form.tags" /></el-form-item><el-form-item label="预览图片地址"><el-input v-model="form.preview" /></el-form-item></div><el-form-item label="说明"><el-input v-model="form.description" /></el-form-item><el-form-item label="完整画布配置 JSON"><input type="file" accept=".json" aria-label="导入模板 JSON 文件" @change="importJson" /><el-input v-model="form.content" type="textarea" :rows="12" class="code-input" /></el-form-item><el-checkbox v-if="!editing" v-model="form.enabled">创建后允许选择此模板</el-checkbox><p v-else class="muted-note">状态通过卡片“管理”菜单发布或停用；保存只更新当前版本内容。</p></el-form>
      <template #footer><el-button @click="dialog = false">取消</el-button><el-button type="primary" @click="save">保存模板</el-button></template>
    </el-dialog>
    <DashboardTemplateUseDialog
      v-model="useDialog"
      :template="useTemplate"
      :datasources="useDatasources"
      :sources-loading="useSourcesLoading"
      :saving="useSaving"
      @submit="submitUse"
    />
  </main>
</template>

<style scoped>
.template-heading-actions { display: flex; align-items: center; gap: 8px; }
.template-package-import { display: inline-flex; height: 32px; align-items: center; padding: 0 14px; border: 1px solid #cbd8e8; border-radius: 5px; color: #304762; background: #fff; cursor: pointer; font-size: 13px; }
.template-package-import:hover { border-color: #7da8e8; color: #2364c6; }
.template-package-import input { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); clip-path: inset(50%); }
.template-manage { position: relative; }
.template-manage summary { display: inline-flex; min-width: 56px; height: 30px; align-items: center; justify-content: center; border: 1px solid #d6dfeb; border-radius: 4px; color: #445b76; background: #fff; cursor: pointer; font-size: 12px; list-style: none; }
.template-manage summary::-webkit-details-marker { display: none; }
.template-manage[open] summary { border-color: #83aee9; color: #2364c6; }
.template-manage-menu { position: absolute; right: 0; bottom: 36px; z-index: 8; display: grid; min-width: 158px; padding: 5px; border: 1px solid #d6e0ec; border-radius: 6px; background: #fff; box-shadow: 0 10px 28px rgba(32, 57, 88, .16); }
.template-manage-menu button { padding: 8px 10px; border: 0; border-radius: 4px; color: #304762; background: transparent; cursor: pointer; text-align: left; font-size: 12px; }
.template-manage-menu button:hover, .template-manage-menu button:focus-visible { color: #174f9d; background: #edf4ff; outline: none; }
.template-manage-menu button.danger { color: #a12e36; }
.template-item.locked { border-color: #b9d8ed; }
.template-item.disabled { opacity: 1; border-color: #c7d3e0; background: #f7f9fc; }
.template-image { position: relative; overflow: hidden; }
.source-template-canvas { position: absolute; inset: 0; display: block; background: linear-gradient(145deg, #f5f8fc, #dbe8f4); }
.source-template-canvas i { position: absolute; min-width: 2px; min-height: 2px; border: 1px solid rgba(43, 149, 189, .5); border-radius: 2px; background: rgba(255, 255, 255, .78); box-shadow: 0 1px 5px rgba(26, 79, 118, .14); }
.source-template-canvas i.widget-decoration { border-color: transparent; background: rgba(41, 107, 157, .2); box-shadow: none; }
.source-template-canvas i.widget-sheet { border-color: rgba(78, 113, 153, .45); background: repeating-linear-gradient(0deg, #fff 0 8px, #e1eaf3 8px 9px); }
.source-template-empty { display: grid; min-height: 100%; place-items: center; color: #40546c; background: #e6ecf3; font-size: 12px; }
.source-template-status { position: absolute; top: 10px; right: 10px; z-index: 2; padding: 4px 8px; border-radius: 999px; color: #fff; background: #304f68; font-size: 10px; }
.template-item.disabled .template-copy > p { color: #4c6078; }
.template-item.disabled .template-tags span { color: #40546c; background: #e0e8f1; }
.template-item.disabled :deep(.el-tag--warning) { --el-tag-text-color: #694600; --el-tag-bg-color: #fff4d6; --el-tag-border-color: #d9ae4e; }
.large-source-template-canvas { position: relative; width: 100%; aspect-ratio: 16 / 9; overflow: hidden; border: 1px solid #d7e3ef; border-radius: 8px; }
.template-recycle-list { min-height: 120px; }
.template-recycle-list > div { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 12px 4px; border-bottom: 1px solid #e6ebf2; }
.template-recycle-list > div > span { display: grid; gap: 4px; }
.template-recycle-list small { color: #718096; }
.template-lifecycle-summary { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 6px; }
.template-lifecycle-summary span { padding: 4px 9px; border-radius: 4px; color: #2d4664; background: #edf3fa; font-size: 12px; }
.lifecycle-table-wrap { max-height: 440px; overflow: auto; border: 1px solid #dce5ef; border-radius: 6px; }
.lifecycle-table { width: 100%; border-collapse: collapse; color: #30445e; font-size: 12px; }
.lifecycle-table th { position: sticky; top: 0; z-index: 1; color: #30465f; background: #f5f8fb; text-align: left; font-weight: 600; }
.lifecycle-table th, .lifecycle-table td { padding: 10px 12px; border-bottom: 1px solid #e8edf3; white-space: nowrap; }
.lifecycle-table tbody tr:last-child td { border-bottom: 0; }
@media (max-width: 680px) {
  .template-heading-actions { width: 100%; justify-content: flex-end; }
  .lifecycle-table-wrap { max-height: 56vh; }
}
</style>
