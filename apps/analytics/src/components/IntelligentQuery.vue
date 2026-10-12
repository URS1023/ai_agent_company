<script setup lang="ts">
/**
 * 智能问数主页面（路由 /analytics，数据分析布局的默认子路由）。
 * 职责：选择数据源 →（可选）浏览表结构 → 自然语言或 SQL 提问 → 执行查询 → 展示结果、历史、快捷查询与 AI 大屏生成入口。
 * 依赖接口：api/request.ts 直连 /api/v1/analytics/*（表结构、历史、查询、生成）与 /api/v1/report-templates（大屏模板）。
 * 关键交互：切换数据源会重置表结构、历史、结果与错误（sync 触发，避免短暂展示上一数据源的数据）；
 *   提问会并发带上会话 id，模板套用（templateMode）决定生成时是否使用大屏模板；生成失败且可继续时提示"继续等待"。
 * 权限：查询/导出要求 analytics.execute / analytics.export；大屏模板列表需 templates.view（无权限则不请求）。
 * 加载/取消：metadataSequence/querySequence 作代际，abortController 与 dashboardController 分别取消查询与生成请求。
 */
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Search, FolderOpened, Close, RefreshLeft } from '@element-plus/icons-vue'
import { can, get, post, scopedKey } from '../api/request'
import type { Envelope, History, QueryResult, Source, Table } from '../types'
import { createClientId } from '../utils/randomId'
import { parseQueryParameters, preferredQuerySource, rememberQuerySource, suggestedQueryTables } from '../utils/queryContext'
import QueryTemplates from './QueryTemplates.vue'
import QueryResultPanel from './business/QueryResultPanel.vue'
import DashboardGenerationStatus from './business/DashboardGenerationStatus.vue'

interface DashboardTemplate {
  id: string
  name: string
  description?: string
  content?: {
    canvas?: {
      width?: number
      height?: number
    }
  }
  preview?: string
  styleLocked?: boolean
  enabled?: boolean
}

const route = useRoute()
const router = useRouter()
const datasources = ref<Source[]>([])
const selectedDatasource = ref('')
const datasourceLoading = ref(false)
const allTables = ref<Table[]>([])
const tableLoading = ref(false)
const tableError = ref('')
const tableSearchKeyword = ref('')
const tablePage = ref(1)
const tablePageSize = ref(10)
const queryText = ref('')
const queryMode = ref<'natural_language' | 'sql'>('natural_language')
const parameterJson = ref('{}')
const needAnalysis = ref(true)
const querying = ref(false)
const queryError = ref('')
const queryResult = ref<QueryResult | null>(null)
const queryHistory = ref<History[]>([])
const historyError = ref('')
const templatesRef = ref<InstanceType<typeof QueryTemplates> | null>(null)
const sessionId = ref(createClientId())
const templateMode = ref<'auto' | 'manual' | 'none'>('none')
const selectedDashboardTemplateId = ref('')
const dashboardTemplates = ref<DashboardTemplate[]>([])
const templateLoading = ref(false)
const dashboardDrafting = ref(false)
const dashboardError = ref('')
const dashboardMayContinue = ref(false)
const draftingSourceName = ref('')
let dashboardController: AbortController | null = null
let dashboardSequence = 0
let abortController: AbortController | null = null
let metadataSequence = 0
let querySequence = 0

const source = computed(() => datasources.value.find(item => item.id === selectedDatasource.value))
const filteredTables = computed(() => allTables.value.filter(table =>
  [table.tableName, table.tableComment].some(value => String(value || '').toLowerCase().includes(tableSearchKeyword.value.toLowerCase())),
))
const tableData = computed(() => filteredTables.value.slice((tablePage.value - 1) * tablePageSize.value, tablePage.value * tablePageSize.value))
const connectionStatus = computed(() => tableLoading.value ? 'testing' : tableError.value ? 'failed' : 'connected')
const connectionStatusText = computed(() => tableLoading.value ? '正在读取表结构' : tableError.value ? '表结构读取失败' : '表结构已加载')
const queryPlaceholder = computed(() => queryMode.value === 'sql'
  ? '输入只读 SELECT 或 WITH 查询。\n可展开数据表列表，或点击下方快捷查询生成只读 SQL。'
  : '描述你要了解的业务问题，例如：按部门统计本月费用，或汇总最近一周的设备预警。\n请使用所选数据源中实际存在的表与字段。')
const queryProgress = computed(() => queryMode.value === 'sql'
  ? '正在校验并执行只读查询…' : '正在生成并校验查询，等待数据结果…')
const hasEnabledDashboardTemplates = computed(() => dashboardTemplates.value.some(item => item.enabled !== false))
const selectedDashboardTemplate = computed(() => dashboardTemplates.value.find(item => item.id === selectedDashboardTemplateId.value))

// Only suggest tables from the current authorized source. Never accumulate examples from a previous source.
// 快捷查询只依据"当前已授权数据源"的表生成；切换数据源必须清空，绝不能沿用上一个数据源的示例（会误导用户）。
const queryExamples = computed(() => suggestedQueryTables(allTables.value).flatMap(table => {
  const label = table.tableComment || table.tableName
  const quote = source.value?.type === 'mysql' ? String.fromCharCode(96) : '"'
  const identifier = quote + table.tableName.replaceAll(quote, quote + quote) + quote
  return [
    { label: '查看 ' + label + ' 数据', query: queryMode.value === 'sql' ? 'SELECT * FROM ' + identifier + ' LIMIT 20' : '查看 ' + label + '（' + table.tableName + '）的前20条数据' },
    { label: '统计 ' + label + ' 总量', query: queryMode.value === 'sql' ? 'SELECT COUNT(*) AS total FROM ' + identifier : '统计 ' + label + '（' + table.tableName + '）的记录总数' },
  ]
}))
const dashboardTemplateOptionLabel = (template: DashboardTemplate) => `${template.name}（${template.enabled === false ? '已停用' : '可用'} · ${template.styleLocked ? '锁定布局' : '可编辑布局'}）`

watch([tableSearchKeyword, tablePageSize], () => { tablePage.value = 1 })
watch(selectedDatasource, async (id) => {
  rememberQuerySource(id)
  const sequence = ++metadataSequence
  ++querySequence
  abortController?.abort()
  abortController = null
  querying.value = false
  queryResult.value = null
  queryError.value = ''
  dashboardError.value = ''
  dashboardMayContinue.value = false
  allTables.value = []
  tablePage.value = 1
  tableSearchKeyword.value = ''
  tableError.value = ''
  queryHistory.value = []
  historyError.value = ''
  tableLoading.value = Boolean(id)
  if (!id) return
  await Promise.all([
    loadTableData(id, sequence),
    loadQueryHistory(id, sequence),
  ])
}, { flush: 'sync' })
watch(() => templateMode.value, (mode) => {
  if (mode !== 'manual') {
    selectedDashboardTemplateId.value = ''
  }
})

async function loadDashboardTemplates() {
  templateLoading.value = true
  try {
    const response = can('templates.view')
      ? await get<Envelope<DashboardTemplate[]>>('/api/v1/report-templates')
      : null
    dashboardTemplates.value = Array.isArray(response?.data) ? response.data : []
  } catch (caught) {
    dashboardTemplates.value = []
    ElMessage.error('读取大屏模板失败：' + (caught as Error).message)
  } finally {
    templateLoading.value = false
  }
}

async function loadTableData(id: string, sequence: number) {
  try {
    const response = await get<Envelope<Table[]>>('/api/v1/analytics/datasources/' + id + '/tables/info')
    if (!response.success) throw new Error(response.message || '表结构读取失败')
    if (sequence === metadataSequence) allTables.value = response.data
  } catch (caught) {
    if (sequence === metadataSequence) tableError.value = (caught as Error).message
  } finally {
    if (sequence === metadataSequence) tableLoading.value = false
  }
}
async function loadQueryHistory(id: string, sequence: number) {
  try {
    const response = await get<Envelope<History[]>>('/api/v1/analytics/history/datasource/' + id, { limit: 5 })
    if (sequence === metadataSequence) {
      queryHistory.value = response.data.filter(item => ['completed', 'success'].includes(item.status))
      historyError.value = ''
    }
  } catch (caught) {
    if (sequence === metadataSequence) historyError.value = '最近查询暂不可用：' + (caught as Error).message
  }
}
function useHistoryQuery(history: History) {
  if (dashboardDrafting.value) return
  queryText.value = history.queryText
  queryMode.value = history.queryMode === 'sql' ? 'sql' : 'natural_language'
  parameterJson.value = JSON.stringify(history.parameters || {}, null, 2)
  ElMessage.success('已填入最近查询，可修改后执行')
}
function fieldSummary(table: Table) {
  return table.columns.map(column => column.comment || column.columnName).join(' · ')
}
function handleUseTemplate(text: string, mode?: 'natural_language' | 'sql', parameters: Record<string, unknown> = {}) {
  if (dashboardDrafting.value) return
  queryText.value = text
  queryMode.value = mode || (/^\s*(SELECT|WITH)\b/i.test(text) ? 'sql' : 'natural_language')
  parameterJson.value = JSON.stringify(parameters, null, 2)
}
/**
 * 创建 AI 大屏（对话式）：按 templateMode 选择是否套用模板，POST 大屏生成接口后保存草稿并跳转查看页。
 * 约束：同一时刻只允许一个生成请求（dashboardSequence 作代际，旧响应丢弃）；
 *   生成过程中禁止重复提交；失败时 dashboardMayContinue 表示任务可能仍在服务端继续，提示用户到报表列表查看。
 */
async function createDashboardFromConversation() {
  if (dashboardDrafting.value || querying.value || !can('dashboards.create')) return
  if (!selectedDatasource.value) {
    ElMessage.warning('请先选择用于生成大屏的数据源')
    return
  }
  if (!queryText.value.trim()) {
    ElMessage.warning('请先输入需求文本或 SQL')
    return
  }
  if (templateMode.value === 'manual' && !selectedDashboardTemplateId.value) {
    ElMessage.warning('手动模式请先选择一套模板')
    return
  }
  dashboardDrafting.value = true
  dashboardError.value = ''
  dashboardMayContinue.value = false
  draftingSourceName.value = source.value?.name || selectedDatasource.value
  const sequence = ++dashboardSequence
  dashboardController = new AbortController()
  try {
    const response = await post('/api/v1/ai/business-actions/dashboards/drafts', {
      requestKey: createClientId(),
      requirement: queryText.value.trim(),
      title: queryText.value.trim().slice(0, 80),
      datasourceIds: [selectedDatasource.value],
      templateMode: templateMode.value,
      templateId: templateMode.value === 'manual' ? selectedDashboardTemplateId.value : null,
      width: 1920,
      height: 1080,
    // 大屏生成按服务端终态等待；用户离开页面或主动开始新会话时仍可通过 AbortController 中止等待。
    }, { signal: dashboardController.signal })
    if (sequence !== dashboardSequence) return
    const result = (response as { data?: { reportId?: string } }).data || response as { reportId?: string }
    const reportId = result.reportId
    if (!reportId) throw new Error('服务端未返回大屏标识')
    ElMessage.success('AI 数字大屏草稿已生成，正在进入设计页')
    await router.push('/report/designer/' + reportId)
  } catch (caught) {
    if (sequence !== dashboardSequence) return
    const error = caught as Error & { code?: string; response?: unknown }
    dashboardMayContinue.value = ['ECONNABORTED', 'ETIMEDOUT', 'ERR_NETWORK'].includes(error.code || '')
    dashboardError.value = dashboardMayContinue.value
      ? '等待超时或连接中断，服务端可能仍在生成。请先查看大屏列表，避免重复创建。'
      : error.message || '生成数字大屏失败'
  } finally {
    if (sequence === dashboardSequence) {
      dashboardDrafting.value = false
      dashboardController = null
    }
  }
}
function startNewSession() {
  if (dashboardDrafting.value) return
  dashboardError.value = ''
  dashboardMayContinue.value = false
  ++querySequence
  abortController?.abort()
  abortController = null
  sessionId.value = createClientId()
  queryText.value = ''
  parameterJson.value = '{}'
  queryResult.value = null
  queryError.value = ''
  querying.value = false
  ElMessage.success('已开始新的分析会话')
}
/**
 * 执行查询：自然语言或只读 SQL（POST /api/v1/analytics/query）。
 * 关键字段：datasourceId、queryText、queryMode、parameters、needAnalysis（是否要统计与洞察）。
 * 约束：必须已选数据源；请求用 AbortController 支持取消，querySequence 保证只有最后一次请求写入结果。
 */
async function executeQuery() {
  if (querying.value || dashboardDrafting.value || !can('analytics.execute')) return
  if (!selectedDatasource.value || !queryText.value.trim()) { ElMessage.warning('请选择数据源并输入查询内容'); return }
  const sequence = ++querySequence
  const sourceId = selectedDatasource.value
  const sourceSequence = metadataSequence
  abortController = new AbortController()
  querying.value = true
  queryError.value = ''
  queryResult.value = null
  try {
    const parameters = queryMode.value === 'sql' ? parseQueryParameters(parameterJson.value) : {}
    const response = await post<Envelope<QueryResult>>('/api/v1/analytics/query', {
      datasourceId: sourceId, query: queryText.value, mode: queryMode.value,
      needAnalysis: needAnalysis.value, needChart: true, sessionId: sessionId.value, parameters,
    }, { signal: abortController.signal })
    if (sequence !== querySequence) return
    if (!response.success) throw new Error(response.message || '查询失败')
    queryResult.value = response.data
    ElMessage.success('查询完成，返回 ' + response.data.totalCount + ' 行')
    await loadQueryHistory(sourceId, sourceSequence)
  } catch (caught) {
    if (sequence !== querySequence) return
    const error = caught as Error & { code?: string }
    if (error.name === 'CanceledError' || error.code === 'ERR_CANCELED') {
      ElMessage.info('已停止等待。服务端可能仍在处理，可在查询历史查看最终状态。')
    } else {
      queryError.value = error.message || '查询失败，请检查数据源或查询内容'
    }
  } finally {
    if (sequence === querySequence) { querying.value = false; abortController = null }
  }
}
onMounted(async () => {
  const promptKey = scopedKey('analysis-prompt')
  const prompt = sessionStorage.getItem(promptKey)
  if (prompt) { queryText.value = prompt; sessionStorage.removeItem(promptKey) }
  datasourceLoading.value = true
  try {
    datasources.value = (await get<Envelope<Source[]>>('/api/v1/analytics/datasources')).data.filter(item => item.enabled)
    await loadDashboardTemplates()
    const id = String(route.query.datasourceId || '')
    selectedDatasource.value = preferredQuerySource(datasources.value, id)
    if (id && !selectedDatasource.value) queryError.value = '此数据源已停用、被删除或不在你的授权范围内，请重新选择'
    if (selectedDatasource.value) {
      if (route.query.tableName) queryText.value = String(route.query.queryHint || ('查看 ' + (route.query.tableComment || route.query.tableName) + ' 的数据'))
    }
    if (route.query.query) queryText.value = String(route.query.query)
  } catch (caught) { queryError.value = (caught as Error).message }
  finally { datasourceLoading.value = false }
})
onUnmounted(() => {
  ++metadataSequence; ++querySequence; ++dashboardSequence
  abortController?.abort()
  dashboardController?.abort()
})
</script>

<template>
  <div class="intelligent-query">
    <section class="query-source-panel" aria-label="分析数据源">
      <div class="datasource-selector">
        <label for="query-source">数据源</label>
        <el-select id="query-source" v-model="selectedDatasource" placeholder="选择已授权数据源" :loading="datasourceLoading" :disabled="querying || dashboardDrafting" filterable clearable popper-class="query-source-popper">
          <el-option v-for="item in datasources" :key="item.id" :label="item.name + ' (' + item.type + ')'" :value="item.id" class="query-source-option">
            <div><strong>{{ item.name }}</strong><span>{{ item.type }}</span></div>
            <small>{{ item.description || item.autoDescription || item.environmentSummary || item.databaseName }}</small>
          </el-option>
        </el-select>
        <span v-if="selectedDatasource" :class="['connection-status', connectionStatus]" role="status">
          <i aria-hidden="true"></i>{{ connectionStatusText }}
        </span>
        <span v-else class="query-help">支持已接入的数据库和导入表格</span>
      </div>
      <div v-if="source" class="source-context" aria-label="当前数据源说明">
        <p>{{ source.description || source.autoDescription || '尚未填写业务说明' }}</p>
        <el-tag v-for="domain in source.businessDomains || []" :key="domain" size="small" effect="plain">{{ domain }}</el-tag>
        <span>{{ source.businessTableCount || 0 }} 张业务表 · {{ source.fieldCount || 0 }} 个字段 · {{ source.semanticIndexStatus === 'ready' ? '向量与关键词索引' : '关键词语义索引' }}</span>
      </div>
      <details v-if="selectedDatasource" class="query-metadata">
        <summary>数据表与字段说明 <span>{{ allTables.length }} 个授权数据表</span></summary>
        <el-alert v-if="tableError" :title="tableError" type="error" :closable="false" show-icon />
        <div class="metadata-toolbar">
          <label for="query-table-search">查找数据表</label>
          <el-input id="query-table-search" v-model="tableSearchKeyword" placeholder="搜索表名或注释" clearable />
        </div>
        <el-table v-loading="tableLoading" :data="tableData" stripe border max-height="260" :empty-text="tableError ? '表结构读取失败，请检查数据源配置' : '没有匹配的授权数据表'">
          <el-table-column prop="tableName" label="表名" min-width="170" />
          <el-table-column prop="tableComment" label="表注释" min-width="190" />
          <el-table-column label="字段" min-width="210"><template #default="{ row }">
            <span class="table-field-summary">{{ fieldSummary(row) }}</span>
          </template></el-table-column>
          <el-table-column label="记录数" width="110"><template #default="{ row }">{{ row.rowCount == null ? '未统计' : row.rowCount.toLocaleString() }}</template></el-table-column>
        </el-table>
        <div class="metadata-pagination">
          <label>每页 <select v-model.number="tablePageSize"><option v-for="size in [10, 20, 50, 100]" :key="size" :value="size">{{ size }}</option></select> 条</label>
          <el-pagination v-model:current-page="tablePage" :page-size="tablePageSize" :total="filteredTables.length" layout="total, prev, pager, next" :pager-count="5" />
        </div>
      </details>
    </section>

    <section class="query-card" aria-label="查询条件">
      <div class="query-mode-row">
        <label for="query-mode">查询方式</label>
        <el-select id="query-mode" v-model="queryMode" :disabled="querying || dashboardDrafting">
          <el-option label="自然语言查询" value="natural_language" />
          <el-option label="手工 SQL 查询" value="sql" />
        </el-select>
        <span class="query-help">{{ queryMode === 'sql' ? '只读执行，不修改源数据' : '需要已配置的语言模型，根据真实表结构生成查询' }}</span>
      </div>
      <el-input v-model="queryText" type="textarea" :rows="5" :placeholder="queryPlaceholder"
        :readonly="dashboardDrafting"
        aria-label="查询问题或只读 SQL" :class="{ 'code-input': queryMode === 'sql' }"
        @keydown.ctrl.enter.prevent="executeQuery" @keydown.meta.enter.prevent="executeQuery" />
      <details v-if="queryMode === 'sql'" class="query-parameters" :open="parameterJson !== '{}'">
        <summary>命名参数 JSON</summary>
        <el-input v-model="parameterJson" type="textarea" :rows="3" :readonly="dashboardDrafting" aria-label="SQL 命名参数 JSON" class="code-input" />
      </details>
      <div class="query-template-row">
        <label for="dashboard-template-mode">模板策略</label>
        <el-select id="dashboard-template-mode" v-model="templateMode" :disabled="dashboardDrafting" style="width: 180px">
          <el-option label="无模板" value="none" />
          <el-option label="自动匹配模板" value="auto" />
          <el-option label="手动选择模板" value="manual" :disabled="!hasEnabledDashboardTemplates" />
        </el-select>
        <el-select
          v-if="templateMode === 'manual'"
          v-model="selectedDashboardTemplateId"
          :loading="templateLoading"
          :disabled="dashboardDrafting"
          placeholder="请选择模板（不自动修改模板）"
          style="width: min(420px, 100%)"
        >
          <el-option
            v-for="template in dashboardTemplates"
            :key="template.id"
            :label="dashboardTemplateOptionLabel(template)"
            :value="template.id"
            :disabled="template.enabled === false"
          >
            <span>{{ template.name }}</span>
            <small>{{ template.description || (template.styleLocked ? '模板锁定（仅改数据绑定）' : '可按模板风格生成') }}</small>
          </el-option>
        </el-select>
        <span class="query-help" v-if="templateMode === 'manual' && !hasEnabledDashboardTemplates">
          模板中心未返回可用模板，可先去“模板中心”创建/启用再选择
        </span>
      </div>
      <div v-if="queryExamples.length" class="quick-queries" aria-label="基于当前数据源的快捷查询">
        <span class="query-help">快捷查询</span>
        <el-button v-for="example in queryExamples" :key="example.label" size="small" :title="example.query" :disabled="dashboardDrafting" @click="queryText = example.query">{{ example.label }}</el-button>
      </div>
      <details v-if="queryHistory.length || historyError" class="query-history">
        <summary>最近查询 <span v-if="queryHistory.length">{{ queryHistory.length }} 条</span></summary>
        <p v-if="historyError" class="query-help" role="status">{{ historyError }}</p>
        <button v-for="history in queryHistory" :key="history.id" type="button" :disabled="dashboardDrafting" @click="useHistoryQuery(history)">
          <span class="history-mode">{{ history.queryMode === 'sql' ? 'SQL' : '问数' }}</span>
          <span class="history-query">{{ history.queryText }}</span>
          <span class="history-count">{{ history.resultCount }} 行</span>
        </button>
      </details>
      <div class="query-actions">
        <el-checkbox v-model="needAnalysis" :disabled="dashboardDrafting">生成数据统计与洞察</el-checkbox>
        <div class="action-buttons">
          <el-button :icon="RefreshLeft" :disabled="dashboardDrafting" @click="startNewSession">新会话</el-button>
          <el-button :disabled="dashboardDrafting" @click="templatesRef?.open()"><el-icon><FolderOpened /></el-icon>查询模板</el-button>
          <el-button type="primary" :loading="dashboardDrafting" :disabled="querying || !can('dashboards.create') || !selectedDatasource || !queryText.trim() || (templateMode === 'manual' && !selectedDashboardTemplateId)" @click="createDashboardFromConversation">生成数字大屏</el-button>
          <el-button type="primary" :loading="querying" :disabled="dashboardDrafting || !can('analytics.execute') || !selectedDatasource || !queryText.trim()" @click="executeQuery">
            <el-icon><Search /></el-icon>执行查询
          </el-button>
        </div>
      </div>
      <p class="query-help query-shortcut">Ctrl / ⌘ + Enter 执行查询；可以直接“生成数字大屏”形成 AI 草稿。</p>
      <DashboardGenerationStatus v-if="dashboardDrafting" :source-name="draftingSourceName" />
      <el-alert v-if="dashboardError" class="dashboard-error" :title="dashboardMayContinue ? '尚未收到生成结果' : '大屏未生成，请检查以下信息后重试'" type="error"
        :description="dashboardError" show-icon :closable="true" @close="dashboardError = ''" />
      <router-link v-if="dashboardError && dashboardMayContinue" to="/dashboards">查看大屏列表</router-link>
      <div v-if="querying" class="query-progress" role="status">
        <el-progress :percentage="100" indeterminate :show-text="false" :stroke-width="4"
          aria-label="查询执行进度" :aria-valuenow="null" :aria-valuetext="queryProgress" />
        <div><span>{{ queryProgress }}</span><el-button text @click="abortController?.abort()"><el-icon><Close /></el-icon>停止等待</el-button></div>
      </div>
    </section>
    <el-alert v-if="queryError" :title="queryError" type="error" :closable="false" show-icon />
    <QueryResultPanel v-if="queryResult" :key="queryResult.queryId" :result="queryResult" />
    <div v-else-if="!querying && !queryError && !dashboardDrafting" class="query-empty-state">
      <el-icon aria-hidden="true"><Search /></el-icon>
      <div><strong>尚无查询结果</strong><p>{{ selectedDatasource ? '输入问题或只读 SQL 后执行，结果将在这里展示。' : '先选择数据源，再输入你要了解的业务问题。' }}</p></div>
    </div>
    <QueryTemplates ref="templatesRef" @use-template="handleUseTemplate" />
  </div>
</template>

<style scoped>
.intelligent-query { min-width: 0; color: #17263c; }
.query-source-panel, .query-card { border: 1px solid #dce4ef; background: #fff; border-radius: 7px; padding: 20px; margin-bottom: 18px; }
.datasource-selector, .query-mode-row, .query-template-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.datasource-selector > label, .query-mode-row > label, .query-template-row > label, .metadata-toolbar > label { font-size: 13px; font-weight: 600; min-width: 58px; }
.datasource-selector .el-select { width: min(340px, 100%); }
.source-context { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; margin-top: 11px; padding-left: 70px; color: #5c6d84; font-size: 12px; }
.source-context p { margin: 0; max-width: 620px; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.source-context span:last-child { color: #53657d; }
.query-mode-row { margin-bottom: 16px; }
.query-mode-row .el-select { width: 180px; }
.query-template-row { margin: 10px 0 6px; }
.query-template-row .el-select { min-width: 180px; }
.query-help { margin: 0; color: #5c6d84; font-size: 12px; line-height: 1.7; }
.dashboard-error :deep(.el-alert__description) { white-space: pre-line; overflow-wrap: anywhere; }
.connection-status { display: inline-flex; align-items: center; gap: 7px; color: #247342; font-size: 12px; }
.connection-status i { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.connection-status.testing { color: #2666e8; }
.connection-status.failed { color: #bb293a; }
.query-metadata { border-top: 1px solid #e3e8ef; margin-top: 16px; padding-top: 8px; }
.query-metadata summary, .query-history summary { color: #40546c; font-size: 13px; }
.query-metadata summary span, .query-history summary span { margin-left: 12px; font-size: 12px; color: #5c6d84; }
.metadata-toolbar { display: flex; gap: 12px; align-items: center; margin: 12px 0; }
.metadata-toolbar .el-input { max-width: 320px; }
.table-field-summary { font-size: 12px; overflow-wrap: anywhere; }
.metadata-pagination { margin-top: 12px; display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 10px; }
.metadata-pagination label { display: flex; gap: 6px; align-items: center; font-size: 12px; color: #40546c; }
.metadata-pagination select { border: 1px solid #aebcd0; border-radius: 4px; padding: 4px 6px; background: #fff; color: #40546c; }
.query-card :deep(.el-textarea__inner) { line-height: 1.8; padding: 12px 14px; font-size: 14px; }
.quick-queries { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin-top: 14px; }
.quick-queries .query-help { margin-right: 4px; }
.quick-queries :deep(.el-button) { margin-left: 0; max-width: 100%; height: auto; min-height: 28px; padding: 7px 9px; white-space: normal; }
.query-history { margin-top: 16px; border-top: 1px solid #e3e8ef; padding-top: 8px; }
.query-history button { display: flex; align-items: center; gap: 10px; width: 100%; border: 0; border-radius: 4px; background: #fff; text-align: left; padding: 10px 8px; cursor: pointer; color: #40546c; }
.query-history button:hover { background: #f1f5fb; }
.history-mode { color: #2666e8; font: 12px Consolas, monospace; flex-shrink: 0; }
.history-query { font-size: 13px; text-overflow: ellipsis; overflow: hidden; white-space: nowrap; }
.history-count { margin-left: auto; flex-shrink: 0; font-size: 12px; color: #5c6d84; }
.query-actions { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin-top: 20px; padding-top: 16px; border-top: 1px solid #e3e8ef; }
.action-buttons { display: flex; gap: 10px; flex-wrap: wrap; }
.action-buttons :deep(.el-button) { margin-left: 0; }
.query-shortcut { margin-top: 8px; }
.query-progress { margin-top: 16px; }
.query-progress > div:last-child { display: flex; justify-content: space-between; align-items: center; gap: 10px; color: #40546c; font-size: 13px; }
.query-empty-state { display: flex; align-items: center; justify-content: center; gap: 16px; padding: 30px 20px; color: #5c6d84; border: 1px dashed #c9d5e5; border-radius: 7px; }
.query-empty-state > .el-icon { font-size: 24px; color: #637b9c; }
.query-empty-state strong { font-size: 14px; color: #40546c; font-weight: 500; }
.query-empty-state p { font-size: 12px; line-height: 1.7; margin: 6px 0 0; }
@media (max-width: 720px) {
  .query-source-panel, .query-card { padding: 15px; }
  .datasource-selector > label, .query-mode-row > label { min-width: 52px; }
  .datasource-selector .el-select { flex: 1; min-width: 170px; }
  .datasource-selector .connection-status, .datasource-selector .query-help { flex-basis: 100%; }
  .source-context { padding-left: 0; }
  .source-context p { flex-basis: 100%; }
  .query-mode-row .query-help { width: 100%; }
  .query-template-row { flex-direction: column; align-items: stretch; }
  .query-template-row > .el-select { width: 100%; }
  .metadata-pagination .el-pagination { max-width: 100%; overflow-x: auto; }
  .query-actions { align-items: stretch; gap: 8px; }
  .action-buttons { margin-left: auto; }
  .query-actions :deep(.el-button), .metadata-pagination select { min-height: 40px; }
  .query-empty-state { justify-content: flex-start; padding: 24px 15px; }
}
:global(.query-source-popper .el-select-dropdown__item.query-source-option) { height: auto; min-height: 54px; line-height: 1.35; padding-top: 8px; padding-bottom: 8px; }
:global(.query-source-option > span) { width: 100%; }
:global(.query-source-option div) { display: flex; justify-content: space-between; gap: 14px; color: #17263c; }
:global(.query-source-option div span) { color: #7a899d; font-size: 11px; text-transform: uppercase; }
:global(.query-source-option small) { display: block; margin-top: 4px; color: #5c6d84; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
</style>
