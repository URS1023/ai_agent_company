<template>
  <div class="business-page report-workspace">
  <div class="datasource-manage">
    <!-- 页面头部 -->
    <div class="page-header">
      <div class="header-icon">
        <i class="fa-solid fa-chart-pie"></i>
      </div>
      <div class="header-content">
        <h2>报表与数字大屏</h2>
        <span class="item-count">共 {{ reports.length }} 个报表</span>
      </div>
      <div class="header-actions">
        <el-button v-if="can('dashboards.create')" type="primary" @click="createReport" class="create-btn">
          <i class="fa-solid fa-plus"></i> 新增报表
        </el-button>
      </div>
    </div>

    <!-- 内容容器 -->
    <div class="content-container">
      <div class="report-filters">
        <el-input v-model="reportKeyword" clearable maxlength="200" aria-label="搜索报表" placeholder="搜索报表名称、描述或 ID" />
        <select v-model="reportStatus" aria-label="报表状态">
          <option value="all">全部状态</option><option value="draft">草稿</option><option value="published">已发布</option><option value="archived">已归档</option>
        </select>
        <span role="status">显示 {{ visibleReports.length }} / {{ reports.length }} 个</span>
        <el-button :loading="loading" @click="loadReports">刷新列表</el-button>
      </div>
      <el-alert v-if="loadError" :title="loadError" type="error" :closable="false" />
      <div class="datasource-cards" v-loading="loading">
        <TransitionGroup name="card-list">
          <div
            v-for="rp in visibleReports"
            :key="rp.id"
            class="datasource-card"
            :data-report-id="rp.id"
            :data-source-report-id="isLynxImported(rp) ? rp.lineage.sourceReportId : undefined"
          >
            <div class="card-accent" :class="{ active: rp.enabled }"></div>
            <button
              v-if="isLynxImported(rp)"
              type="button"
              class="report-card-preview"
              :aria-label="`预览 ${rp.reportName}`"
              @click="viewReport(rp)"
            >
              <img v-if="rp.thumbnail && !rp.__thumbnailFailed" :src="rp.thumbnail" :alt="`${rp.reportName}预览`" @error="rp.__thumbnailFailed = true" />
              <span v-else class="report-card-mini-canvas" :style="reportCanvasStyle(rp)" aria-hidden="true">
                <i
                  v-for="widget in rp.__previewWidgets"
                  :key="widget.id || widget.i"
                  :class="{ decoration: widget.type === 'decoration' }"
                  :style="reportWidgetStyle(rp, widget)"
                ></i>
              </span>
              <span class="report-source-mark">智慧云脑 Cloud Brain · #{{ rp.lineage.sourceReportId }}</span>
            </button>
            <div class="card-content">
              <!-- 头部：图标 + 标题 + 状态 -->
              <div class="card-top">
                <div v-if="!isLynxImported(rp)" class="card-icon" :class="{ active: rp.enabled }">
                  <i class="fa-solid fa-tv"></i>
                </div>
                <div class="card-title-area">
                  <div class="card-title-row">
                    <span class="name">{{ rp.reportName }}</span>
                    <el-tag type="success" size="small" effect="plain" round class="status-tag">
                      {{ {draft: '草稿', published: '已发布', archived: '已归档'}[rp.status] || rp.status }}
                    </el-tag>
                  </div>
                  <div v-if="!isLynxImported(rp)" class="desc">{{ rp.description || '暂无描述' }}</div>
                </div>
              </div>

              <!-- 信息标签 -->
              <div class="datasource-tags">
                <span class="datasource-tag">
                  <i class="fa-solid fa-ruler-combined"></i>
                  {{ rp.width }}×{{ rp.height }}
                </span>
                <span v-if="isLynxImported(rp)" class="datasource-tag">
                  <i class="fa-solid fa-shapes"></i>
                  {{ rp.lineage.componentCount }} 个组件
                </span>
                <span class="datasource-tag" v-if="rp.updateTime">
                  <i class="fa-solid fa-clock"></i>
                  {{ formatTime(rp.updateTime) }}
                </span>
              </div>

              <!-- 底部：操作按钮 -->
              <div class="card-bottom">
                <div class="card-meta">
                  <span class="meta-item">
                    <i class="fa-solid fa-hashtag"></i>
                    <span v-if="isLynxImported(rp)">接口导入报表</span>
                    <span v-else>ID {{ rp.id }}</span>
                  </span>
                </div>
                <div class="card-actions" @click.stop>
                  <el-tooltip content="预览大屏" placement="top" :show-after="200">
                    <button class="action-btn primary" aria-label="预览大屏" @click="viewReport(rp)">
                      <i class="fa-solid fa-eye"></i>
                    </button>
                  </el-tooltip>
                  <el-tooltip content="设计" placement="top" :show-after="200">
                    <button v-if="can('dashboards.edit')" class="action-btn" aria-label="打开完整设计器" @click="editReport(rp)">
                      <i class="fa-solid fa-pen-ruler"></i>
                    </button>
                  </el-tooltip>
                  <el-tooltip content="保存为模板" placement="top" :show-after="200">
                    <button v-if="can('templates.create')" class="action-btn" aria-label="保存为模板" @click="openTemplateDialog(rp)">
                      <i class="fa-regular fa-clone"></i>
                    </button>
                  </el-tooltip>
                  <el-tooltip content="重命名" placement="top" :show-after="200">
                    <button v-if="can('dashboards.edit')" class="action-btn" aria-label="重命名报表" @click="renameReport(rp)">
                      <i class="fa-solid fa-edit"></i>
                    </button>
                  </el-tooltip>
                  <el-tooltip content="删除" placement="top" :show-after="200">
                    <button v-if="can('dashboards.delete')" class="action-btn danger" aria-label="删除报表" @click="deleteReport(rp)">
                      <i class="fa-solid fa-trash"></i>
                    </button>
                  </el-tooltip>
                </div>
              </div>
            </div>
          </div>
        </TransitionGroup>

        <!-- 空状态 -->
        <div v-if="!loading && !loadError && reports.length === 0" class="empty-state">
          <i class="fa-solid fa-chart-line"></i>
          <p>还没有报表，点击右上角「新增报表」开始创建</p>
        </div>
        <div v-else-if="!loading && !loadError && visibleReports.length === 0" class="empty-state">
          <p>没有符合条件的报表</p>
          <el-button @click="reportKeyword = ''; reportStatus = 'all'">清除筛选</el-button>
        </div>
      </div>
    </div>

    <!-- 新增/重命名对话框 -->
    <el-dialog
      v-model="dialogVisible"
      :title="dialogTitle"
      :width="formData.id ? 'min(480px, 94vw)' : 'min(760px, 94vw)'"
      class="report-create-dialog"
    >
      <el-form :model="formData" label-width="90px">
        <el-form-item v-if="!formData.id" label="创建方式">
          <div class="report-create-mode" role="radiogroup" aria-label="创建方式">
            <button type="button" role="radio" :aria-checked="creationMode === 'none'" :class="{ active: creationMode === 'none' }" @click="selectCreationMode('none')">
              <i class="fa-regular fa-square"></i><span><b>空白报表</b><small>从空画布开始设计</small></span><em>自由</em>
            </button>
            <button v-if="can('templates.view')" type="button" role="radio" :aria-checked="creationMode === 'manual'" :class="{ active: creationMode === 'manual' }" @click="selectCreationMode('manual')">
              <i class="fa-solid fa-layer-group"></i><span><b>参考模板</b><small>选定样式后仅填充数据</small></span><em>{{ templateLoading ? '加载中' : `${enabledTemplateCount} 可用` }}</em>
            </button>
          </div>
        </el-form-item>
        <el-form-item v-if="!formData.id && creationMode === 'manual'" label="参考模板" class="report-template-field">
          <div v-if="templateLoading" class="report-template-state" role="status" aria-live="polite">
            <i class="fa-solid fa-spinner fa-spin"></i>
            <span><strong>正在读取模板目录</strong><small>加载完成后不会自动选择模板</small></span>
          </div>
          <div v-else-if="templateLoadError" class="report-template-state error" role="alert">
            <i class="fa-solid fa-circle-exclamation"></i>
            <span><strong>模板目录读取失败</strong><small>{{ templateLoadError }}</small></span>
            <el-button size="small" @click="loadDashboardTemplates">重试</el-button>
          </div>
          <div v-else-if="enabledTemplateCount === 0" class="report-template-state empty" role="status">
            <i class="fa-regular fa-clone"></i>
            <span>
              <strong>当前工作区还没有可用模板</strong>
              <small>可在任一报表卡点击“保存为模板”；画布、组件、布局和样式会保留，数据与 SQL 会清空。</small>
            </span>
            <div class="report-template-state-actions">
              <el-button size="small" @click="loadDashboardTemplates">刷新目录</el-button>
              <el-button size="small" type="primary" plain @click="openTemplateCenter">打开模板中心</el-button>
            </div>
          </div>
          <template v-else>
            <p v-if="!formData.templateId">请选择一套模板；系统不会自动选择第一套。</p>
            <el-input v-model="templateKeyword" clearable placeholder="搜索模板名称或场景" aria-label="搜索可用报表模板" class="report-template-search" />
            <div class="report-template-gallery">
              <button
                v-for="template in visibleDashboardTemplates"
                :key="template.id"
                type="button"
                class="report-template-card"
                :data-template-id="template.id"
                :class="{ selected: formData.templateId === template.id, locked: template.styleLocked }"
                :aria-label="`${template.name}，${template.styleLocked ? '视觉锁定，可使用' : '可使用'}`"
                @click="selectCreateTemplate(template.id)"
              >
                <span class="report-template-preview">
                  <img v-if="template.preview" :src="templatePreviewUrl(template)" :alt="`${template.name}模板预览`" />
                  <span v-else-if="template.content?.widgets?.length" class="report-template-mini-canvas" :style="templateCanvasStyle(template)" aria-hidden="true">
                    <i v-for="widget in template.content.widgets" :key="widget.id" :class="`widget-${widget.type || 'chart'}`" :style="templateWidgetStyle(template, widget)"></i>
                  </span>
                  <b>{{ template.code }}</b>
                  <em>{{ template.styleLocked ? '视觉锁定' : '可用' }}</em>
                </span>
                <span class="report-template-copy">
                  <small>{{ templateSourceType(template) }}</small>
                  <strong>{{ template.name }}</strong>
                  <p>{{ template.summary }}</p>
                  <span><em v-for="tag in (template.tags || []).slice(0, 2)" :key="tag">{{ tag }}</em><em>{{ template.componentCountLabel || `${template.componentCount} 组件` }}</em><em v-if="template.styleLocked">只填数据</em></span>
                </span>
                <i class="fa-solid fa-circle-check report-template-check"></i>
              </button>
            </div>
            <div class="report-template-tip"><i class="fa-solid fa-circle-info"></i> 锁定模板完整保留画布、组件、布局和样式，创建后只填充授权数据。</div>
          </template>
        </el-form-item>
        <el-form-item label="报表名称" required>
          <el-input v-model="formData.reportName" placeholder="请输入报表名称" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="formData.description" type="textarea" :rows="2" placeholder="选填" />
        </el-form-item>
        <el-form-item v-if="can('datasources.view')" label="主要数据源">
          <el-select v-model="formData.datasourceId" clearable placeholder="可不选；组件可分别绑定多个数据源" style="width: 100%">
            <el-option v-for="ds in datasources" :key="ds.id" :label="ds.name" :value="ds.id" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveReport">确定</el-button>
      </template>
    </el-dialog>
    <ReportToTemplateDialog
      v-model="templateDialogVisible"
      :report="templateSourceReport"
      :saving="templateSaving"
      @submit="saveReportAsTemplate"
    />
  </div>
  </div>
</template>

<script setup>
/**
 * 报表与大屏管理页（路由 /dashboards，权限 dashboards.view）。
 * 职责：报表列表（关键字/状态筛选）、新建与编辑元信息、删除、把已确认报表固化为模板（ReportToTemplateDialog），
 *   以及按模板创建大屏（可选"原样空数据"或"按需求文档仅填充数据绑定"）。
 * 依赖接口：api/report.js（/api/v1/reports 系列）与 /api/v1/report-templates（模板列表与状态）。
 * 关键交互：模板下拉只列"启用且未删除"的模板，停用/源记录已删除的模板给出状态说明；
 *   创建与删除均为破坏性/耗时操作，删除需二次确认，创建中禁止重复提交。
 * 权限：新建/编辑/删除分别受 can('dashboards.create' | 'dashboards.edit' | 'dashboards.delete') 控制。
 * 错误呈现：列表错误进 loadError（页面提示条），操作类错误用 ElMessage。
 */
import { computed, ref, reactive, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import axios from 'axios'
import reportApi from '@/api/report'
import { can, get } from '@/api/request'
import ReportToTemplateDialog from '@/components/reports/ReportToTemplateDialog.vue'
import { formatRecordTime } from '@/utils/dateTime'

const router = useRouter()

const reports = ref([])
const reportKeyword = ref('')
const reportStatus = ref('all')
const visibleReports = computed(() => {
  const keyword = reportKeyword.value.trim().toLocaleLowerCase()
  return reports.value.filter(report => (reportStatus.value === 'all' || report.status === reportStatus.value)
    && (!keyword || [report.reportName, report.description, report.id].some(value => String(value || '').toLocaleLowerCase().includes(keyword))))
})
const datasources = ref([])
const loading = ref(false)
const loadError = ref('')
const saving = ref(false)
const dialogVisible = ref(false)
const dialogTitle = ref('新增报表')
const templateDialogVisible = ref(false)
const templateSourceReport = ref(null)
const templateSaving = ref(false)
const dashboardTemplates = ref([])
const templateKeyword = ref('')
const creationMode = ref('none')
const templateLoading = ref(false)
const templateLoadError = ref('')
const getDashboardTemplate = (id) => dashboardTemplates.value.find(template => template.id === id)
const enabledDashboardTemplates = computed(() => dashboardTemplates.value.filter(template => template.enabled !== false && !template.deleted))
const enabledTemplateCount = computed(() => enabledDashboardTemplates.value.length)
const visibleDashboardTemplates = computed(() => {
  const query = templateKeyword.value.trim().toLowerCase()
  return enabledDashboardTemplates.value.filter(template => !query || [
    template.name,
    template.summary,
    template.description,
    ...(template.tags || []),
  ].join(' ').toLowerCase().includes(query))
})
const templateSourceStatus = (template) => template.sourceDeleted ? '源记录已删除' : template.sourceEmpty ? '源配置为空' : '模板已停用'
const templateSourceType = (template) => template.sourceKind === 'lynx-report-screen'
  ? '智慧云脑模板'
  : '自定义模板'
const templateCreationDescription = (template) => `${template.summary || template.description || ''}${template.styleLocked ? '（源模板样式锁定，仅填充数据）' : '（基于模板创建）'}`
const templateCanvasStyle = (template) => {
  const canvas = template.content?.canvas || {}
  return {
    backgroundColor: canvas.backgroundColor || '#07111f',
    backgroundImage: canvas.backgroundImage ? `url("${String(canvas.backgroundImage).replace(/"/g, '%22')}")` : undefined,
    backgroundSize: 'cover',
    backgroundPosition: 'center',
  }
}
const templateWidgetStyle = (template, widget) => {
  const canvas = template.content?.canvas || {}
  const width = Math.max(1, Number(canvas.width) || 1920)
  const height = Math.max(1, Number(canvas.height) || 1080)
  return {
    left: `${Math.max(0, Number(widget.x) || 0) / width * 100}%`,
    top: `${Math.max(0, Number(widget.y) || 0) / height * 100}%`,
    width: `${Math.max(1, Number(widget.w) || 1) / width * 100}%`,
    height: `${Math.max(1, Number(widget.h) || 1) / height * 100}%`,
  }
}

const formData = reactive({
  id: null,
  reportName: '',
  description: '',
  datasourceId: null,
  templateId: ''
})

const isLynxImported = (report) => report.lineage?.entry === 'lynx_interface_import'
const hydrateReport = (report) => {
  let content = {}
  try {
    content = typeof report.content === 'string' ? JSON.parse(report.content) : (report.content || {})
  } catch {
    content = {}
  }
  return {
    ...report,
    __previewCanvas: content.canvas || {},
    __previewWidgets: (content.widgets || []).slice(0, 64),
    __thumbnailFailed: false,
  }
}
const reportCanvasStyle = (report) => {
  const canvas = report.__previewCanvas || {}
  const theme = canvas.theme || {}
  const image = canvas.backgroundImage || report.backgroundImage
  return {
    backgroundColor: canvas.backgroundColor || theme.background || report.backgroundColor || '#07111f',
    backgroundImage: image ? `url("${String(image).replace(/"/g, '%22')}")` : undefined,
    backgroundSize: 'cover',
    backgroundPosition: 'center',
  }
}
const reportWidgetStyle = (report, widget) => {
  const canvas = report.__previewCanvas || {}
  const width = Math.max(1, Number(canvas.width) || Number(report.width) || 1920)
  const height = Math.max(1, Number(canvas.height) || Number(report.height) || 1080)
  return {
    left: `${Math.max(0, Number(widget.x) || 0) / width * 100}%`,
    top: `${Math.max(0, Number(widget.y) || 0) / height * 100}%`,
    width: `${Math.max(1, Number(widget.w) || 1) / width * 100}%`,
    height: `${Math.max(1, Number(widget.h) || 1) / height * 100}%`,
  }
}

/** 加载报表列表：GET /api/v1/reports/list；失败写入 loadError（页面提示条），不抛出。 */
const loadReports = async () => {
  loading.value = true
  loadError.value = ''
  try {
    const res = await reportApi.list()
    reports.value = (res.data || []).map(hydrateReport)
  } catch (e) {
    loadError.value = e.message || '加载报表列表失败'
  } finally {
    loading.value = false
  }
}

const loadDashboardTemplates = async () => {
  if (!can('templates.view')) {
    dashboardTemplates.value = []
    return
  }
  templateLoading.value = true
  templateLoadError.value = ''
  try {
    const response = await get('/api/v1/report-templates')
    dashboardTemplates.value = Array.isArray(response?.data) ? response.data : []
  } catch (e) {
    dashboardTemplates.value = []
    templateLoadError.value = e.message || '读取模板列表失败'
  } finally {
    templateLoading.value = false
  }
}

const loadDatasources = async () => {
  if (!can('datasources.view')) return
  try {
    const res = await axios.get('/api/data-analysis/datasources')
    datasources.value = (res.data?.data || []).filter(source => source.enabled)
  } catch (e) {
    ElMessage.error(e.message || '读取数据源失败，可稍后在组件中配置')
  }
}

const createReport = () => {
  dialogTitle.value = '新增报表'
  Object.assign(formData, { id: null, reportName: '', description: '', datasourceId: null, templateId: '' })
  formData.version = undefined
  formData.templateMode = 'none'
  creationMode.value = 'none'
  templateKeyword.value = ''
  dialogVisible.value = true
  void loadDashboardTemplates()
}

const openTemplateCenter = () => {
  dialogVisible.value = false
  router.push('/templates')
}

const renameReport = (rp) => {
  dialogTitle.value = '编辑报表'
  Object.assign(formData, {
    id: rp.id,
    reportName: rp.reportName,
    description: rp.description,
    datasourceId: rp.datasourceId,
    templateId: rp.templateId,
    templateMode: rp.templateMode,
    version: rp.version
  })
  dialogVisible.value = true
}

const templatePreviewUrl = (template) => {
  return template.preview
}

const selectCreateTemplate = (templateId) => {
  const previous = getDashboardTemplate(formData.templateId)
  const template = getDashboardTemplate(templateId)
  if (!template || template.enabled === false || template.deleted) return
  formData.templateId = template.id
  if (!formData.reportName || formData.reportName === previous?.name) formData.reportName = template.name
  const previousDescription = previous ? templateCreationDescription(previous) : ''
  if (!formData.description || formData.description === previousDescription) {
    formData.description = templateCreationDescription(template)
  }
}

const selectCreationMode = (mode) => {
  creationMode.value = mode
  formData.templateMode = mode
  if (mode === 'manual') {
    return
  }
  const previous = getDashboardTemplate(formData.templateId)
  if (previous && formData.reportName === previous.name) formData.reportName = ''
  if (previous && formData.description === templateCreationDescription(previous)) formData.description = ''
  formData.templateId = ''
}

/**
 * 保存报表：按现有 id 走 reportApi.update / create，未选模板时用空内容兜底；
 * 失败用 ElMessage 提示且保留弹窗内容（不做本地乐观更新）。
 */
const saveReport = async () => {
  if (!formData.id && creationMode.value === 'manual' && !formData.templateId) {
    ElMessage.warning('请手动选择一套模板')
    return
  }
  if (!formData.reportName?.trim()) {
    ElMessage.warning('请输入报表名称')
    return
  }
  saving.value = true
  try {
    if (formData.id) {
      await reportApi.update(formData.id, formData)
      ElMessage.success('已更新')
    } else {
      const overrides = {
        reportName: formData.reportName.trim(),
        description: formData.description,
        datasourceId: formData.datasourceId || null,
        templateMode: creationMode.value,
        templateId: formData.templateId || null,
      }
      const res = await reportApi.create(overrides)
      const created = res.data || res.result || res
      ElMessage.success('已创建')
      dialogVisible.value = false
      await loadReports()
      // 新建后直接进入设计器
      if (created?.id) {
        router.push(`/report/designer/${created.id}`)
      }
      saving.value = false
      return
    }
    dialogVisible.value = false
    await loadReports()
  } catch (e) {
    ElMessage.error(e.message || '保存失败')
  } finally {
    saving.value = false
  }
}

const editReport = (rp) => {
  router.push(`/report/designer/${rp.id}`)
}

const viewReport = (rp) => {
  const url = router.resolve(`/report/view/${rp.id}`).href
  window.open(url, '_blank', 'noopener')
}

const openTemplateDialog = (report) => {
  templateSourceReport.value = report
  templateDialogVisible.value = true
}

/** 把报表固化为模板：POST /api/v1/report-templates/from-report/{id}（源报表不会被修改），成功后刷新模板列表。 */
const saveReportAsTemplate = async (payload) => {
  if (!templateSourceReport.value) return
  templateSaving.value = true
  try {
    await reportApi.createTemplateFromReport(templateSourceReport.value.id, payload)
    templateDialogVisible.value = false
    await loadDashboardTemplates()
    ElMessage.success('模板已保存：样式已锁定，数据与 SQL 已清空')
  } catch (e) {
    ElMessage.error(e.message || '保存模板失败')
  } finally {
    templateSaving.value = false
  }
}

/** 删除报表：二次确认后 DELETE /api/v1/reports/{id}；用户取消不算错误。 */
const deleteReport = async (rp) => {
  try {
    await ElMessageBox.confirm(`确定删除报表「${rp.reportName}」吗？`, '提示', { type: 'warning' })
    await reportApi.delete(rp.id)
    ElMessage.success('已删除')
    await loadReports()
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') ElMessage.error(e.message || '删除失败')
  }
}

const formatTime = (t) => {
  return formatRecordTime(t)
}

onMounted(() => {
  loadReports()
  loadDatasources()
  loadDashboardTemplates()
})
</script>

<style scoped src="@/styles/datasource-manage.css"></style>
<style scoped>
.report-filters { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin-bottom: 18px; }
.report-filters > .el-input { flex: 1; min-width: 180px; max-width: 420px; }
.report-filters select { height: 34px; padding: 0 10px; border: 1px solid #dcdfe6; border-radius: 4px; background: white; color: #303133; }
.report-filters > span { color: #606266; font-size: 13px; }
.report-card-preview {
  position: relative;
  display: block;
  width: 100%;
  aspect-ratio: 16 / 9;
  overflow: hidden;
  padding: 0;
  border: 0;
  border-bottom: 1px solid #dce4ef;
  background: #07111f;
  cursor: pointer;
}

.report-card-preview > img,
.report-card-mini-canvas {
  display: block;
  width: 100%;
  height: 100%;
}

.report-card-preview > img { object-fit: cover; }
.report-card-mini-canvas { position: relative; overflow: hidden; }
.report-card-mini-canvas::after {
  position: absolute;
  inset: 0;
  background: linear-gradient(180deg, transparent 58%, rgba(3, 14, 30, .28));
  content: '';
}
.report-card-mini-canvas > i {
  position: absolute;
  min-width: 2px;
  min-height: 2px;
  border: 1px solid rgba(74, 220, 255, .55);
  background: rgba(12, 49, 76, .72);
  box-shadow: inset 0 0 8px rgba(57, 192, 240, .12);
}
.report-card-mini-canvas > i.decoration {
  border-color: transparent;
  background: rgba(74, 220, 255, .14);
  box-shadow: none;
}
.report-source-mark {
  position: absolute;
  right: 9px;
  bottom: 9px;
  z-index: 2;
  padding: 4px 7px;
  border: 1px solid rgba(117, 220, 255, .32);
  border-radius: 4px;
  background: rgba(3, 17, 35, .78);
  color: #d8f6ff;
  font: 10px/1.2 Consolas, 'Microsoft YaHei UI', sans-serif;
  letter-spacing: .04em;
}
.report-card-preview:hover > img { transform: scale(1.015); }
.report-card-preview > img { transition: transform .18s ease; }
.report-card-preview:focus-visible { outline: 3px solid #2c71da; outline-offset: -3px; }

.report-create-mode {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  width: 100%;
}

.report-create-mode button {
  display: grid;
  grid-template-columns: 32px minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px;
  min-height: 58px;
  padding: 8px 12px;
  border: 1px solid #dce4f0;
  border-radius: 9px;
  background: #f8fafd;
  color: #738198;
  text-align: left;
  cursor: pointer;
  transition: .18s ease;
}

.report-create-mode button > i {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  border-radius: 7px;
  background: #edf2f9;
  color: #7590b5;
}

.report-create-mode button span { display: grid; gap: 2px; }
.report-create-mode button b { color: #26354b; font-size: 13px; }
.report-create-mode button small { color: #8a97aa; font-size: 10px; }
.report-create-mode button em { color: #8290a4; font-size: 10px; font-style: normal; }
.report-create-mode button:hover { border-color: #8eb7f8; background: #fff; }
.report-create-mode button.active { border-color: #3d80f5; background: #f2f7ff; box-shadow: 0 0 0 3px rgba(61, 128, 245, .1); }
.report-create-mode button.active > i { background: #3d80f5; color: #fff; box-shadow: 0 5px 13px rgba(61, 128, 245, .22); }
.report-create-mode button.active em { color: #3478ed; }

.report-template-field :deep(.el-form-item__content) { display: block; }
.report-template-search { margin: 6px 0 10px; }
.report-template-state {
  display: grid;
  grid-template-columns: 40px minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
  min-height: 92px;
  padding: 16px;
  border: 1px solid #d9e4f2;
  border-radius: 10px;
  background: linear-gradient(135deg, #f8fbff, #f1f6fd);
}
.report-template-state > i {
  display: grid;
  place-items: center;
  width: 40px;
  height: 40px;
  border-radius: 9px;
  background: #e7f0ff;
  color: #3478ed;
  font-size: 17px;
}
.report-template-state > span { display: grid; gap: 4px; min-width: 0; }
.report-template-state strong { color: #26364d; font-size: 13px; }
.report-template-state small { color: #718198; font-size: 11px; line-height: 1.55; }
.report-template-state.error { border-color: #f0c8c8; background: #fff8f8; }
.report-template-state.error > i { background: #ffebeb; color: #db4b4b; }
.report-template-state-actions { display: flex; gap: 6px; }
.report-template-gallery {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  width: 100%;
  max-height: min(56vh, 620px);
  overflow-y: auto;
  padding: 2px 6px 2px 2px;
  overscroll-behavior: contain;
}

.report-template-card {
  position: relative;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  gap: 8px;
  width: 100%;
  min-height: 268px;
  padding: 10px;
  overflow: hidden;
  border: 1px solid #d9e3f0;
  border-radius: 11px;
  background: #fff;
  text-align: left;
  cursor: pointer;
  transition: .2s ease;
}

.report-template-card:hover,
.report-template-card.selected { border-color: #3c83f6; box-shadow: 0 10px 26px rgba(45, 94, 176, .14); }
.report-template-card.unavailable { opacity: .58; cursor: not-allowed; filter: grayscale(.32); }
.report-template-card.unavailable:hover { border-color: #d9e3f0; box-shadow: none; }
.report-template-card.locked { border-color: #b9d8ed; }
.report-template-preview { position: relative; display: block; width: 100%; aspect-ratio: 16 / 9; overflow: hidden; border-radius: 7px; background: #020b1f; }
.report-template-preview img { width: 100%; height: 100%; object-fit: cover; }
.report-template-mini-canvas { position: absolute; inset: 0; background: linear-gradient(145deg, #f5f8fc, #dbe8f4); }
.report-template-mini-canvas i { position: absolute; min-width: 2px; min-height: 2px; border: 1px solid rgba(43, 149, 189, .5); border-radius: 2px; background: rgba(255, 255, 255, .7); box-shadow: 0 1px 4px rgba(26, 79, 118, .12); }
.report-template-mini-canvas i.widget-decoration { border-color: transparent; background: rgba(41, 107, 157, .2); box-shadow: none; }
.report-template-mini-canvas i.widget-sheet { border-color: rgba(78, 113, 153, .45); background: repeating-linear-gradient(0deg, #fff 0 8px, #e1eaf3 8px 9px); }
.report-template-preview::after { position: absolute; inset: 0; border: 1px solid rgba(33, 211, 255, .4); border-radius: inherit; box-shadow: inset 0 0 20px rgba(20, 126, 255, .16); content: ''; }
.report-template-preview b { position: absolute; bottom: 7px; left: 7px; padding: 3px 6px; border: 1px solid rgba(61, 218, 255, .45); border-radius: 3px; background: rgba(2, 17, 42, .82); color: #78eaff; font: 8px Consolas, monospace; }
.report-template-preview > em { position: absolute; top: 7px; right: 7px; z-index: 2; padding: 3px 6px; border-radius: 999px; background: rgba(4, 31, 57, .82); color: #d8f6ff; font-size: 8px; font-style: normal; }
.report-template-copy { display: flex; min-width: 0; flex-direction: column; padding: 0 18px 3px 2px; }
.report-template-copy > small { color: #4d84d6; font: 9px Consolas, monospace; letter-spacing: .09em; }
.report-template-copy strong { margin-top: 7px; color: #1f2e44; font-size: 15px; line-height: 1.4; }
.report-template-copy p { display: -webkit-box; margin: 8px 0 10px; overflow: hidden; color: #718096; font-size: 11px; line-height: 1.6; -webkit-box-orient: vertical; -webkit-line-clamp: 3; }
.report-template-copy > span { display: flex; flex-wrap: wrap; gap: 5px; margin-top: auto; }
.report-template-copy em { padding: 3px 6px; border-radius: 4px; background: #edf4ff; color: #4c75b3; font-size: 9px; font-style: normal; }
.report-template-check { position: absolute; top: 10px; right: 10px; color: #3b80f4; font-size: 17px; opacity: 0; transition: .18s ease; }
.report-template-card.selected .report-template-check { opacity: 1; }
.report-template-tip { display: flex; align-items: center; gap: 6px; margin-top: 8px; color: #7c8ca2; font-size: 10px; }
.report-template-tip i { color: #4b89f5; }

@media (max-width: 720px) {
  :deep(.report-create-dialog) {
    --el-dialog-margin-top: 3vh;
    display: flex;
    max-height: 94vh;
    flex-direction: column;
  }
  :deep(.report-create-dialog .el-dialog__body) { overflow-y: auto; }
  :deep(.report-create-dialog .el-form-item) { display: block; }
  :deep(.report-create-dialog .el-form-item__label) {
    display: flex;
    width: auto !important;
    height: auto;
    margin-bottom: 7px;
    line-height: 1.4;
  }
  :deep(.report-create-dialog .el-form-item__content) {
    width: 100%;
    margin-left: 0 !important;
  }
  .report-create-mode { grid-template-columns: minmax(0, 1fr); }
  .report-template-state { grid-template-columns: 40px minmax(0, 1fr); }
  .report-template-state-actions {
    display: grid;
    grid-column: 1 / -1;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    width: 100%;
  }
  .report-template-state-actions :deep(.el-button) { min-width: 0; margin: 0; }
  .report-template-gallery { grid-template-columns: minmax(0, 1fr); }
  .report-card-preview { aspect-ratio: 16 / 8; }
}

@media (prefers-reduced-motion: reduce) {
  .report-card-preview > img { transition: none; }
}

.empty-state {
  width: 100%;
  text-align: center;
  padding: 80px 20px;
  color: #909399;
}
.empty-state i {
  font-size: 48px;
  margin-bottom: 16px;
  opacity: 0.5;
}
</style>
