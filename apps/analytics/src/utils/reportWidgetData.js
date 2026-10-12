/**
 * reportWidgetData —— 从生成的大屏组件中解析数据绑定（数据源 + SQL + dataType）。
 *
 * 位置：jimuReportAdapter.toJimuWidget 归一化组件时调用。
 * 关键约束：
 * - 兼容三种历史存放位置：config.datasourceId/sql、widget.datasourceId、widget.query.sql、widget.generatedSql，
 *   读取顺序不可随意调整，否则老报表会丢绑定；
 * - dataType 的默认值由"是否同时存在数据源与 SQL"推导（3 = 已绑定查询，1 = 静态数据），
 *   显式配置优先，不得用默认值覆盖已保存的显式取值。
 */
export function resolveGeneratedDataBinding(widget = {}) {
  const config = widget.config || {}
  const datasourceId = config.datasourceId || widget.datasourceId
  const sql = config.sql || widget.query?.sql || widget.generatedSql || ''

  return {
    datasourceId,
    sql,
    dataType: config.dataType ?? (datasourceId && sql ? 3 : 1)
  }
}
