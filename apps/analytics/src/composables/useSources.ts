/**
 * 数据源与表元数据的组合式函数（智能查询 / SQL 工作区共用）。
 * 职责：加载可用数据源列表、按所选数据源加载表清单、记住上次选择的来源并做回退。
 * 加载/取消：watch 数据源变化后重新拉表；作用域销毁（onScopeDispose）时终止后续写入。
 * 错误暴露：内部捕获后用 ElMessage 提示，状态保持为空（页面展示空态）。
 */
import { computed, onScopeDispose, ref, watch } from 'vue'
import { get } from '../api/request'
import type { Envelope, Source, Table } from '../types'
import { preferredQuerySource, rememberQuerySource } from '../utils/queryContext'

/**
 * 数据源与表元数据状态。
 * 参数：options.rememberSelection 为 true 时记住用户选择的数据源（跨页面复用），
 *   否则只在显式传入 requested 时用 preferredQuerySource 解析一次。
 * 返回：sources（仅启用中的数据源）、tables、sourceId（双向绑定）、loading、error、loadSources、loadTables。
 * 失败表现：requested 指定的数据源不可用时给出"已停用/已删除/无授权"提示；
 *   表元数据请求用 metadataSequence 作代际，切换数据源时丢弃在途结果。
 */
export function useSources(options: { rememberSelection?: boolean } = {}) {
  const sources = ref<Source[]>([])
  const tables = ref<Table[]>([])
  const sourceId = ref('')
  const sourcesLoading = ref(false)
  const tablesLoading = ref(false)
  const loading = computed(() => sourcesLoading.value || tablesLoading.value)
  const error = ref('')
  let metadataSequence = 0
  async function loadSources(requested = '') {
    sourcesLoading.value = true
    error.value = ''
    try {
      sources.value = (await get<Envelope<Source[]>>('/api/v1/analytics/datasources')).data.filter(item => item.enabled)
      if (options.rememberSelection || requested) sourceId.value = preferredQuerySource(sources.value, requested)
      if (requested && !sourceId.value) error.value = '此数据源已停用、被删除或不在你的授权范围内，请重新选择'
    }
    catch (caught) { error.value = (caught as Error).message }
    finally { sourcesLoading.value = false }
  }
  async function loadTables() {
    const sequence = ++metadataSequence
    tables.value = []
    if (!sourceId.value) { tablesLoading.value = false; return }
    tablesLoading.value = true
    error.value = ''
    try {
      const result = await get<Envelope<Table[]>>('/api/v1/analytics/datasources/' + sourceId.value + '/tables/info')
      if (sequence === metadataSequence) tables.value = result.data
    } catch (caught) { if (sequence === metadataSequence) error.value = (caught as Error).message }
    finally { if (sequence === metadataSequence) tablesLoading.value = false }
  }
  watch(sourceId, id => { if (options.rememberSelection) rememberQuerySource(id) })
  onScopeDispose(() => { ++metadataSequence })
  return { sources, tables, sourceId, loading, error, loadSources, loadTables }
}
