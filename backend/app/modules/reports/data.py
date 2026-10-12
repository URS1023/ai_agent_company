"""Saved bindings, refresh and drilldown use server-owned SQL and current access."""
from __future__ import annotations

from app.errors import NotFoundError, ValidationError
from app.modules.analytics.queries import execute_query
from app.modules.analytics.schemas import QueryInput
from app.modules.reports.schemas import DrilldownInput
from app.modules.reports.service import get_report


def get_widget(ctx, report_id: str, widget_id: str) -> tuple:
    """定位报表内的组件，返回 ``(report, widget)``；找不到抛 ``NotFoundError``。

    复用 ``get_report``，因此读取组件数据同样受报表权限与其数据源权限双重约束。
    """

    report = get_report(ctx, report_id)
    widget = next((item for item in report.content.get("widgets", []) if str(item.get("id")) == widget_id), None)
    if widget is None:
        raise NotFoundError("报表中不存在该组件")
    return report, widget


def refresh_widget(ctx, report_id: str, widget_id: str) -> dict:
    """刷新已保存组件的数据，返回查询结果字典。

    只执行**服务端已保存**的绑定：``dataType=2`` 走固定 HTTP 采集，``dataType=3`` 用报表里
    存的 SQL + 参数执行，行数上限固定 1000；用 ``dashboards.view`` 权限执行，因此只读查看者
    刷新不会获得任意 SQL 执行能力。未绑定数据库查询的组件抛 ``ValidationError``。
    """

    report, widget = get_widget(ctx, report_id, widget_id)
    config = widget.get("config") or {}
    if str(config.get("dataType")) == "2":
        return http_widget_data(ctx, str(config.get("datasourceId") or ""), permission="dashboards.view")
    if str(config.get("dataType")) != "3" or not config.get("datasourceId") or not config.get("sql"):
        raise ValidationError("该组件未绑定数据库查询")
    result = execute_query(ctx, QueryInput(datasourceId=str(config["datasourceId"]), query=config["sql"], mode="sql",
                                          parameters=config.get("parameters") or {}, maxRows=1000),
                           permission="dashboards.view")
    return result.model_dump(mode="json")


def http_widget_data(ctx, source_id: str, *, permission: str = "dashboards.execute") -> dict:
    """Bind an approved fixed HTTP source, never a browser-supplied request URL.

    只按 ``source_id`` 触发服务端已批准的固定采集（``collect``，不允许前端传 URL/请求体）；
    需要 ``permission``（默认 ``dashboards.execute``，视图刷新传 ``dashboards.view``）。
    采集状态不是 success 时抛 ``ToolExecutionError`` 并带 ``query_id``/``run_id``
    ——部分成功的数据不得当作完整结果展示。
    """
    from app.errors import ToolExecutionError
    from app.modules.collection.execution import collect
    if not source_id:
        raise ValidationError("请选择已授权的 HTTP 数据源")
    result = collect(ctx, source_id, automatic=True, permission=permission)
    if result.status != "success":
        raise ToolExecutionError("HTTP 组件采集不完整，请从数据源采集历史查看逐条结果", query_id=result.queryId, run_id=result.runId)
    return result.model_dump(mode="json")


def drilldown(ctx, payload: DrilldownInput) -> dict:
    """按组件已保存的钻取配置查询明细，返回含 SQL、统计与行的完整结果。

    SQL 与数据源一律取自服务端报表配置，``point`` 只用于取触发字段的值，``parameters`` 必须
    是该钻取配置声明过的唯一参数（多余参数直接拒绝，防止绕过配置传自定义过滤）。
    需要 ``dashboards.view``；行数上限由 ``payload.max_rows`` 控制。
    """

    report, widget = get_widget(ctx, payload.report_id, payload.widget_id)
    config = widget.get("config") or {}
    drill = config.get("drilldown") or widget.get("drilldown") or {}
    sql = drill.get("sql")
    source_id = drill.get("datasourceId") or config.get("datasourceId")
    if not sql or not source_id:
        raise ValidationError("此组件尚未配置钻取查询，请在设计器中保存配置")
    parameter = drill.get("parameter") or "point"
    values = dict(payload.parameters)
    if not values:
        point_data = payload.point.get("data") or {}
        value = point_data.get(drill.get("triggerField")) if isinstance(point_data, dict) else None
        if value is None:
            value = payload.point.get("name")
        if value is not None:
            values[parameter] = value
    if set(values) - {parameter}:
        raise ValidationError("包含未配置的钻取参数")
    result = execute_query(ctx, QueryInput(datasourceId=str(source_id), query=sql, mode="sql",
                                          parameters=values, maxRows=payload.max_rows), permission="dashboards.view")
    return {"success": True, "queryId": result.queryId, "runId": result.runId,
            "executedSql": result.generatedSql, "executionTime": result.executionTime,
            "title": drill.get("title") or widget.get("componentName") or "数据明细",
            "statistics": result.statistics,
            "data": result.data, "rows": result.data, "columns": [column.model_dump() for column in result.columns],
            "totalCount": result.totalCount, "truncated": result.truncated}
