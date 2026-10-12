/**
 * 数据回写目标的组合式函数（工作流"输出表"节点 / 回写管理页共用）。
 * 职责：分页加载写入目标、打开编辑器（只列出支持写入的 sqlite/mysql/postgres 数据源）、
 *   按所选数据源加载可写表清单、保存与移除授权目标。
 * 加载/取消：列表请求持有单个 AbortController；表清单用 watch 的 cleanup abort 上一次请求。
 * 忙碌锁与错误：busy 期间禁止重复保存；error（列表/表单）与 metadataError（表清单）分开暴露，互不覆盖。
 * 约束：保存目标只登记授权，不会写入任何业务记录（成功提示已明确说明）；移除同样不影响已写入的数据与历史凭据。
 */
import { onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { get } from '../api/request'
import { emptyTarget, writeback, type WriteTarget } from '../api/writeback'

/**
 * 写入目标状态。
 * 返回：rows/sources/tables（列表、可选数据源、可写表）、form/editor/busy/loading、分页（offset/total/hasMore）、
 *   error/metadataError，以及 load/open/save/remove。
 * 副作用：save/remove 会写库并刷新列表；表格元数据是只读请求。
 */
export function useWriteTargets() {
  const rows = shallowRef<WriteTarget[]>([]), sources = shallowRef<any[]>([]), tables = shallowRef<any[]>([])
  const form = ref(emptyTarget()), editor = shallowRef(false), busy = shallowRef(false), loading = shallowRef(false)
  const total = shallowRef(0), hasMore = shallowRef(false)
  const offset = shallowRef(0), error = shallowRef(''), metadataError = shallowRef('')
  let request: AbortController | null = null, stopped = false
  async function load(next = offset.value) {
    request?.abort(); const active = new AbortController(); request = active; loading.value = true; error.value = ''
    try { const response = await writeback.list(next, active.signal); if (!stopped && request === active) { rows.value = response.data; offset.value = response.offset; total.value = response.total; hasMore.value = response.hasMore } }
    catch (cause) { if (!active.signal.aborted && !stopped) error.value = (cause as Error).message }
    finally { if (!stopped && active === request) loading.value = false }
  }
  async function open(row?: WriteTarget) {
    error.value = ''; busy.value = true
    try { const response = await get('/api/v1/analytics/datasources'); if (stopped) return; sources.value = response.data.filter((item: any) => ['sqlite', 'mysql', 'postgres'].includes(item.type)); form.value = row ? structuredClone(row) : emptyTarget(); editor.value = true }
    catch (cause) { error.value = (cause as Error).message }
    finally { busy.value = false }
  }
  async function save() {
    if (busy.value) return; busy.value = true; error.value = ''
    try { await writeback.save(form.value); editor.value = false; ElMessage.success('授权目标已保存，尚未写入任何业务记录'); await load() }
    catch (cause) { error.value = (cause as Error).message }
    finally { busy.value = false }
  }
  async function remove(row: WriteTarget) {
    try { await ElMessageBox.confirm('移除该写入授权？不会删除目标表、业务记录或历史凭据。', '移除写入目标', { confirmButtonText: '确认移除', cancelButtonText: '取消' }); busy.value = true; await writeback.remove(row); await load() }
    catch (cause) { if (cause instanceof Error) error.value = cause.message }
    finally { busy.value = false }
  }
  watch(() => form.value.datasourceId, async (id, _, cleanup) => {
    tables.value = []; metadataError.value = ''; if (!id) return
    const abort = new AbortController(); cleanup(() => abort.abort())
    try { const result = await get('/api/v1/analytics/datasources/' + id + '/tables/info', {}, { signal: abort.signal }); if (!abort.signal.aborted && !stopped) tables.value = result.data.filter((table: any) => table.tableType === 'TABLE') }
    catch (cause) { if (!abort.signal.aborted && !stopped) metadataError.value = (cause as Error).message }
  })
  onBeforeUnmount(() => { stopped = true; request?.abort() })
  void load()
  return { rows, sources, tables, form, editor, busy, loading, offset, total, hasMore, error, metadataError, load, open, save, remove }
}
