/**
 * 报表/大屏组件钻取的组合式函数。
 * 职责：打开与关闭钻取面板、按组件身份请求明细数据、导出 CSV。
 * 安全约束：只提交"已保存的报表/组件标识"，不接受客户端拼写的 SQL（见文件内英文说明）。
 * 权限：导出与查看依赖 can('dashboards.view') / can(...) 判断，无权限时给出中文提示而不发请求。
 * 加载/取消：用递增 sequence 作请求代际，关闭面板或重新查询会作废在途响应。
 * 错误暴露：drilldownError 字符串；失败不发 ElMessage，由宿主面板展示。
 */
import { onBeforeUnmount, ref, unref, type Ref } from 'vue'
import { can } from '../api/request'
import reportApi from '../api/report'
import { createDrilldownRequest, normalizeDrilldownRows, rowsToCsv } from '../utils/dashboardDrilldown'

/** Viewers submit saved report/widget identities, never client-authored SQL. */
export function useReportDrilldown(reportId: Ref<string>, beforeQuery?: () => Promise<boolean>) {
  const drilldownState = ref<Record<string, any> | null>(null)
  let sequence = 0
  function closeDrilldown() { sequence += 1; drilldownState.value = null }
  async function openDrilldown({ widget, point = {} }: { widget?: Record<string, any>; point?: Record<string, any> } = {}) {
    if (!widget || widget.type === 'decoration' || /JDragBorder|JDragDecoration/i.test(widget.component || '')) return
    const current = ++sequence
    const request = createDrilldownRequest(widget, point)
    const local = normalizeDrilldownRows(request.sql ? [] : request.fallbackRows, { columns: request.columns })
    drilldownState.value = {
      widget: JSON.parse(JSON.stringify(widget)), title: request.title, point: request.point,
      rows: local.rows, headers: local.headers, loading: Boolean(request.sql), error: '',
      sql: '', sourceSql: request.sourceSql, executionTime: 0, executed: false, truncated: false,
    }
    if (!request.sql) {
      if (request.incomplete) drilldownState.value.error = '请点击具有有效筛选值的数据点'
      return
    }
    try {
      if (beforeQuery && !(await beforeQuery())) throw new Error('请先解决报表保存问题，再查询已保存的钻取配置')
      if (!unref(reportId)) throw new Error('请先保存报表和钻取配置')
      if (current !== sequence) return
      const response = await reportApi.drilldown({
        reportId: unref(reportId), widgetId: String(widget.id),
        point: request.point, parameters: request.parameters,
      })
      const result = response.data
      if (!result?.success || !Array.isArray(result.data)) throw new Error('明细接口未返回有效结果')
      if (current !== sequence) return
      const normalized = normalizeDrilldownRows(result.data, { columns: result.columns || request.columns })
      Object.assign(drilldownState.value!, {
        rows: normalized.rows, headers: normalized.headers, sql: result.executedSql,
        executionTime: Number(result.executionTime || 0), executed: true, truncated: Boolean(result.truncated),
        statistics: result.statistics || {},
      })
    } catch (cause) {
      if (current === sequence) Object.assign(drilldownState.value!, {
        error: (cause as Error).message, rows: [], executed: false,
      })
    } finally {
      if (current === sequence && drilldownState.value) drilldownState.value.loading = false
    }
  }
  function exportDrilldown() {
    const state = drilldownState.value
    if (!can('dashboards.export') || !state || state.error || state.loading) return
    const href = URL.createObjectURL(new Blob([rowsToCsv(state.headers, state.rows)], { type: 'text/csv;charset=utf-8' }))
    const anchor = document.createElement('a')
    anchor.href = href
    anchor.download = (state.title || '数据明细') + '.csv'
    anchor.click()
    setTimeout(() => URL.revokeObjectURL(href), 1000)
  }
  onBeforeUnmount(closeDrilldown)
  return { drilldownState, openDrilldown, closeDrilldown, exportDrilldown }
}
