/**
 * 工作流"读表/写表"节点的表结构元数据加载（列名 + 注释 + 类型）。
 * 用途：节点表单里选择表与字段映射时展示列信息，属于只读元数据。
 * 加载/取消：watch 的 cleanup 中 abort 上一次请求，数据源切换时先清空表清单再加载。
 * 错误暴露：error 字符串；组件在表单内展示提示，不弹全局消息。
 */
import { shallowRef, watch, type Ref } from 'vue'
import { get } from '../api/request'

export interface ReadColumn { columnName: string; comment: string; dataType: string; primaryKey?: boolean }
export interface ReadTable { tableName: string; tableComment: string; columns: ReadColumn[] }

export function useWorkflowTableMetadata(sourceId: Ref<string>) {
  const tables = shallowRef<ReadTable[]>([]), loading = shallowRef(false), error = shallowRef(''), revision = shallowRef(0)
  watch([sourceId, revision], async ([id], _old, cleanup) => {
    const controller = new AbortController()
    cleanup(() => controller.abort())
    tables.value = []; error.value = ''; loading.value = false
    if (!id) return
    loading.value = true
    try {
      const result = await get<{ data: ReadTable[] }>(`/api/v1/analytics/datasources/${encodeURIComponent(id)}/tables/info`, {}, { signal: controller.signal })
      if (!controller.signal.aborted) tables.value = result.data
    } catch (cause) { if (!controller.signal.aborted) error.value = (cause as Error).message }
    finally { if (!controller.signal.aborted) loading.value = false }
  }, { immediate: true })
  return { tables, loading, error, reload: () => revision.value++ }
}
