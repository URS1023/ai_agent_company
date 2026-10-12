/**
 * SQL 工作区的组合式函数：自然语言/SQL 查询、结果与历史、模板套用、SQL 草稿记忆。
 * 加载/取消：自行持有查询请求并作废过期响应（见文件内英文说明），切换数据源后旧结果不再回写。
 * 关键约束：查询必须指定数据源；SQL 草稿按工作区持久化（readSqlDraft/rememberSqlDraft）；
 *   导出与删除均需权限（can 判定）并由 ElMessageBox 二次确认。
 * 错误暴露：查询失败写入 error 并 ElMessage 提示，结果区保留上一次结果不受影响。
 */
import { computed, onMounted, onScopeDispose, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRoute } from 'vue-router'
import { can, get, post } from '../api/request'
import { useSources } from './useSources'
import { parseQueryParameters, readSqlDraft, rememberSqlDraft } from '../utils/queryContext'
import type { Envelope, QueryResult, QueryTemplate } from '../types'

/** Own SQL requests and invalidate responses when the user changes data source. */
export function useSqlQuery() {
  const route = useRoute()
  const sourceState = useSources({ rememberSelection: true })
  const { sources, sourceId, error, loadSources, loadTables } = sourceState
  const sql = ref('')
  const parameterJson = ref('{}')
  const maxRows = ref(1000)
  const executing = ref(false)
  const saving = ref(false)
  const templateLoading = ref(false)
  const result = ref<QueryResult | null>(null)
  const templates = ref<QueryTemplate[]>([])
  const selectedTemplate = ref('')
  const source = computed(() => sources.value.find(item => item.id === sourceId.value))
  const sqlTemplates = computed(() => templates.value.filter(item => item.mode === 'sql'))
  let controller: AbortController | null = null
  let sequence = 0
  let templateSequence = 0

  function invalidate() {
    ++sequence
    controller?.abort()
    controller = null
    executing.value = false
    result.value = null
  }
  watch(sourceId, () => {
    invalidate()
    maxRows.value = Math.min(maxRows.value || 1000, source.value?.maxRows || 1000)
    void loadTables()
  }, { flush: 'sync' })

  function sampleTable(table: string) {
    ++templateSequence
    templateLoading.value = false
    const quote = source.value?.type === 'mysql' ? String.fromCharCode(96) : '"'
    sql.value = 'SELECT * FROM ' + quote + table.replaceAll(quote, quote + quote) + quote + ' LIMIT 100'
    parameterJson.value = '{}'
    selectedTemplate.value = ''
  }
  async function run() {
    if (executing.value || !can('analytics.execute')) return
    if (!sourceId.value || !sql.value.trim()) { ElMessage.warning('请选择数据源并输入只读 SQL'); return }
    const current = ++sequence
    controller = new AbortController()
    executing.value = true
    error.value = ''
    result.value = null
    try {
      const parameters = parseQueryParameters(parameterJson.value)
      const response = await post<Envelope<QueryResult>>('/api/v1/analytics/query', {
        datasourceId: sourceId.value, query: sql.value, mode: 'sql', maxRows: maxRows.value, parameters,
      }, { signal: controller.signal })
      if (current !== sequence) return
      result.value = response.data
      ElMessage.success('查询完成，返回 ' + response.data.totalCount + ' 行')
    } catch (caught) {
      if (current !== sequence) return
      const failure = caught as Error & { code?: string }
      if (failure.code === 'ERR_CANCELED' || failure.name === 'CanceledError') {
        ElMessage.info('已停止等待。可到查询历史查看服务端最终状态。')
      } else error.value = failure.message || '查询失败，请检查 SQL 或数据源'
    } finally {
      if (current === sequence) { executing.value = false; controller = null }
    }
  }
  async function loadTemplates() {
    templates.value = (await get<Envelope<QueryTemplate[]>>('/api/v1/analytics/query-templates')).data
  }
  async function saveTemplate() {
    if (saving.value || !sql.value.trim()) return
    try {
      const parameters = parseQueryParameters(parameterJson.value)
      const content = sql.value
      const { value } = await ElMessageBox.prompt('为此 SQL 配置一个可复用的名称', '保存查询模板', {
        confirmButtonText: '保存', cancelButtonText: '取消',
        inputValidator: value => Boolean(String(value || '').trim()) || '请输入名称',
      })
      saving.value = true
      await post('/api/v1/analytics/query-templates', { name: value.trim(), category: 'SQL 查询', content, mode: 'sql', parameters })
      await loadTemplates()
      ElMessage.success('查询模板已保存')
    } catch (caught) {
      if (caught !== 'cancel' && caught !== 'close') ElMessage.error((caught as Error).message)
    } finally { saving.value = false }
  }
  async function useTemplate() {
    const current = ++templateSequence
    if (!selectedTemplate.value) { templateLoading.value = false; return }
    const originalSql = sql.value
    const originalParameters = parameterJson.value
    templateLoading.value = true
    try {
      const item = (await post<Envelope<QueryTemplate>>('/api/v1/analytics/query-templates/' + selectedTemplate.value + '/use')).data
      if (current !== templateSequence || sql.value !== originalSql || parameterJson.value !== originalParameters) return
      sql.value = item.content
      parameterJson.value = JSON.stringify(item.parameters || {}, null, 2)
      ElMessage.success('已填入模板与参数，可修改后执行')
    } catch (caught) { if (current === templateSequence) ElMessage.error((caught as Error).message) }
    finally { if (current === templateSequence) templateLoading.value = false }
  }
  onMounted(async () => {
    await loadSources(String(route.query.datasourceId || ''))
    if (!route.query.sql && !route.query.tableName && !route.query.historyId) {
      const draft = readSqlDraft(sourceId.value)
      if (draft) {
        sql.value = draft.sql
        parameterJson.value = draft.parameterJson
        maxRows.value = Math.max(1, Math.min(draft.maxRows, source.value?.maxRows || 1000))
      }
    }
    if (route.query.sql) sql.value = String(route.query.sql)
    if (sourceId.value && route.query.tableName) sampleTable(String(route.query.tableName))
    if (route.query.historyId && sourceId.value) {
      const initialSource = sourceId.value
      const current = sequence
      const originalSql = sql.value
      const originalParameters = parameterJson.value
      try {
        const saved = (await get<Envelope<QueryResult>>('/api/v1/analytics/history/' + encodeURIComponent(String(route.query.historyId)))).data
        if (current === sequence && saved.datasourceId === initialSource && sql.value === originalSql && parameterJson.value === originalParameters) {
          sql.value = saved.generatedSql
          parameterJson.value = JSON.stringify(saved.parameters || {}, null, 2)
        }
      } catch (caught) { if (current === sequence) error.value = '历史查询读取失败：' + (caught as Error).message }
    }
    try { await loadTemplates() }
    catch (caught) { ElMessage.error('查询模板加载失败：' + (caught as Error).message) }
  })
  onScopeDispose(() => {
    rememberSqlDraft(sourceId.value, { sql: sql.value, parameterJson: parameterJson.value, maxRows: maxRows.value })
    invalidate(); ++templateSequence
  })
  return { ...sourceState, source, sql, parameterJson, maxRows, executing, saving, result,
    sqlTemplates, selectedTemplate, templateLoading, sampleTable, run, saveTemplate, useTemplate,
    stopWaiting: () => controller?.abort() }
}
