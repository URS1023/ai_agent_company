/**
 * 报表/大屏组件取数的组合式函数。
 * 职责：按组件标识取渲染数据、按 refreshSeconds 定时刷新、把同一组件的并发请求串行排队。
 * 关键约束：只读语义，禁止在此处拼 SQL 或改写报表配置；只读视图（readOnly）下不自动刷新；
 *   权限不足（can('dashboards.view') 等）时直接返回空数据并给出中文提示，不发起请求。
 * 加载/取消：请求队列 QueuedRequest 保证同一组件上一次未返回时不重复发起；卸载时停止定时器与在途写入。
 * 错误暴露：每个组件各自维护 loading/error，组件内展示失败占位，不阻塞其它组件。
 */
import { computed, inject, onBeforeUnmount, ref, unref, watch, type Ref } from 'vue'
import { can, post } from '../api/request'
import reportApi from '../api/report'

export interface ReportDataContext {
  id: Ref<string>
  readOnly: boolean
  refreshSeconds?: Ref<number>
  refreshVersion?: Ref<number>
}

interface QueuedRequest<T> {
  run: () => Promise<T>
  resolve: (value: T) => void
  reject: (cause: unknown) => void
}

interface SourceRequestQueue {
  running: boolean
  requests: QueuedRequest<unknown>[]
}

// A report can contain many widgets bound to one governed data source.  Letting
// every mounted widget fetch at once turns an ordinary page open into a burst
// that exceeds a deliberately small source concurrency policy.  Serialize only
// requests for the same source; different sources continue loading in parallel.
const sourceRequestQueues = new Map<string, SourceRequestQueue>()

function pumpSourceQueue(key: string) {
  const queue = sourceRequestQueues.get(key)
  if (!queue || queue.running) return
  const request = queue.requests.shift()
  if (!request) { sourceRequestQueues.delete(key); return }
  queue.running = true
  void request.run().then(request.resolve, request.reject).finally(() => {
    queue.running = false
    pumpSourceQueue(key)
  })
}

function queueSourceRequest<T>(key: string, run: () => Promise<T>): Promise<T> {
  if (!key) return run()
  let queue = sourceRequestQueues.get(key)
  if (!queue) {
    queue = { running: false, requests: [] }
    sourceRequestQueues.set(key, queue)
  }
  const result = new Promise<T>((resolve, reject) => {
    queue!.requests.push({ run, resolve, reject } as QueuedRequest<unknown>)
  })
  pumpSourceQueue(key)
  return result
}

/** Remote failures clear stale snapshots; readonly viewers execute saved SQL only. */
export function useWidgetData(widget: Ref<Record<string, any>>) {
  const context = inject<ReportDataContext | null>('report-data-context', null)
  const remote = ref<unknown[] | null>(null)
  const error = ref('')
  const loading = ref(false)
  const refreshedAt = ref('')
  const isRemote = computed(() => [2, 3].includes(Number(widget.value.config?.dataType)))
  let sequence = 0
  let timer: ReturnType<typeof setTimeout> | undefined
  let stopped = false
  function reschedule() {
    if (timer) clearTimeout(timer)
    const seconds = Number(unref(context?.refreshSeconds) || 0)
    if (!stopped && isRemote.value && seconds > 0) timer = setTimeout(() => { void refresh() }, Math.max(5, seconds) * 1000)
  }
  async function refresh() {
    const current = ++sequence
    remote.value = null
    error.value = ''
    refreshedAt.value = ''
    if (!isRemote.value) { loading.value = false; return }
    loading.value = true
    const config = widget.value.config || {}
    try {
      const isHttp = Number(config.dataType) === 2
      if (!config.datasourceId || (!isHttp && !config.sql)) throw new Error(isHttp ? '请选择已授权的 HTTP 数据源' : '请选择数据源并配置组件 SQL')
      let response
      response = await queueSourceRequest(String(config.datasourceId), async () => {
        if (current !== sequence || stopped) throw new Error('组件数据请求已取消')
        if (context?.readOnly && unref(context.id)) {
          return reportApi.widgetData(unref(context.id), String(widget.value.id))
        }
        if (!can('dashboards.execute')) throw new Error('没有试运行组件查询的权限，请保存后通过已授权的报表预览查看')
        return isHttp ? post('/api/v1/reports/http-query', { datasourceId: String(config.datasourceId) }) : post('/api/v1/reports/query', {
          datasourceId: String(config.datasourceId), query: config.sql, mode: 'sql',
          parameters: config.parameters || {}, maxRows: 1000,
        })
      })
      const rows = response.data?.data
      if (!Array.isArray(rows)) throw new Error('组件接口未返回有效数据数组')
      if (current === sequence && !stopped) { remote.value = rows; refreshedAt.value = new Date().toLocaleTimeString('zh-CN') }
    } catch (cause) {
      if (current === sequence && !stopped) { remote.value = []; error.value = (cause as Error).message }
    } finally {
      if (current === sequence && !stopped) { loading.value = false; reschedule() }
    }
  }
  watch(() => [widget.value.id, JSON.stringify({
    dataType: widget.value.config?.dataType, datasourceId: widget.value.config?.datasourceId,
    sql: widget.value.config?.sql, parameters: widget.value.config?.parameters, url: widget.value.config?.url,
  }), unref(context?.id), unref(context?.refreshVersion)], () => { void refresh() }, { immediate: true })
  watch(() => unref(context?.refreshSeconds), reschedule)
  onBeforeUnmount(() => { stopped = true; sequence += 1; if (timer) clearTimeout(timer) })
  return { remote, isRemote, error, loading, refreshedAt, refresh }
}
