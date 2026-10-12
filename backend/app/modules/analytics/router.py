"""Analysis, source, history, quality and export API surface."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile

from app.common.authorization import require_permission
from app.common.contracts import Envelope, MutationResult
from app.common.deps import AuthContext, get_current_context
from app.modules.analytics import (
    exports, governance, imports, inventory, metadata, queries, query_templates, source_transfer, sources,
)
from app.modules.analytics.schemas import (
    DataSourceConfigurationImport, DataSourceHealthView, DataSourcePolicyInput,
    DataSourcePolicyView,
    ExportInput, HistoryView, InventoryQueryInput, QualityInput, QueryInput, QueryResult, QueryTemplateInput,
    QueryTemplateView, SourceInput, SourceView, TableView,
)
from app.modules.system.service_helpers import commit_change

router = APIRouter(prefix="/api/v1/analytics", tags=["AI 数据分析"])
Context = Annotated[AuthContext, Depends(get_current_context)]


@router.get("/datasources", response_model=Envelope[list[SourceView]], operation_id="analytics_sources_list", summary="列出已授权分析数据源")
def list_sources(ctx: Context, enabled_only: bool = Query(False, description="只返回启用中的数据源")):
    """复用智慧云脑 Cloud Brain 数据源和数据范围；永不返回密码、密钥或连接串。"""
    return Envelope(data=sources.list_sources(ctx, enabled_only=enabled_only))


@router.post("/datasources", response_model=Envelope[SourceView], status_code=201, operation_id="analytics_sources_create", summary="创建数据库或搜索引擎数据源")
def create_source(payload: SourceInput, ctx: Context):
    """需要 datasources.create；保存加密凭据，不执行用户数据库写入。"""
    return Envelope(data=sources.create_source(ctx, payload))


@router.get("/datasources/page", response_model=Envelope[dict], operation_id="analytics_sources_page", summary="分页搜索已授权分析数据源")
def source_page(
    ctx: Context, page: int = Query(1, ge=1), page_size: int = Query(12, ge=1, le=100),
    keyword: str = Query("", max_length=200), enabled_only: bool = Query(False),
):
    """分页搜索数据源（GET /api/v1/analytics/datasources/page），需要 datasources.view。

    路由层限制 page_size <= 100，避免一次拉取过多记录；响应体不包含密码或连接串。
    """
    return Envelope(data=sources.list_sources_page(
        ctx, page=page, page_size=page_size, keyword=keyword, enabled_only=enabled_only,
    ))


@router.post("/datasources/test", response_model=Envelope[dict], operation_id="analytics_sources_test_config", summary="测试新建或修改中的连接配置")
def test_source_config(payload: SourceInput, ctx: Context):
    """需要连接执行权限；测试未保存配置还需要创建或编辑权限，不回传驱动错误中的凭据。"""
    return Envelope(data=sources.test_source(ctx, payload), message="healthy")


@router.post("/datasources/import", response_model=Envelope[SourceView], status_code=201, operation_id="analytics_sources_import", summary="上传 CSV 或 XLSX 建立独立分析数据源")
def import_source(
    ctx: Context, file: UploadFile = File(description="用户主动选择的 CSV/XLSX，最大 20 MB"),
    name: str = Form("", description="数据源业务名称"),
    table_name: str = Form("imported_data", description="目标表名，不是服务器路径"),
    encoding: Literal["utf-8-sig", "gb18030"] = Form("utf-8-sig", description="CSV 编码"),
):
    """需要 datasources.create；创建新的受管 SQLite 文件，不覆盖任何旧数据库。"""
    require_permission(ctx, "datasources.create")
    data = file.file.read(imports.MAX_UPLOAD + 1)
    return Envelope(data=imports.import_tabular(ctx, filename=file.filename or "", data=data, name=name, table_name=table_name, encoding=encoding))


@router.get("/datasources/config-export", operation_id="analytics_sources_config_export", summary="导出已授权数据源配置包")
def export_source_configurations(ctx: Context):
    """仅导出连接参数、说明和范围；不包含密码、密钥、平台 ID、查询结果或业务数据。"""
    bundle = source_transfer.export_configurations(ctx)
    content = json.dumps(bundle, ensure_ascii=False, indent=2).encode("utf-8")
    return Response(
        content=content,
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="datasource-config-{datetime.utcnow():%Y%m%d}.json"',
            "Cache-Control": "private, no-store",
        },
    )


@router.post("/datasources/config-import", response_model=Envelope[dict], operation_id="analytics_sources_config_import", summary="批量预检或导入数据源配置包")
def import_source_configurations(payload: DataSourceConfigurationImport, ctx: Context):
    """整包先校验后原子变更；凭据从不随包迁移，需要凭据的新连接自动保持停用。"""
    return Envelope(data=source_transfer.import_configurations(ctx, payload))


@router.put("/datasources/{source_id}", response_model=Envelope[SourceView], operation_id="analytics_sources_update", summary="按版本编辑数据源配置")
def update_source(source_id: str, payload: SourceInput, ctx: Context):
    """需要 datasources.edit 及该资源的数据范围；旧版本返回 409，留空密码保留原加密值。"""
    return Envelope(data=sources.update_source(ctx, source_id, payload))


@router.delete("/datasources/{source_id}", response_model=Envelope[MutationResult], operation_id="analytics_sources_delete", summary="移除数据源并保留查询追溯")
def delete_source(source_id: str, ctx: Context):
    """需要 datasources.delete；软删除连接，保留历史和受管数据文件，不删除用户外部数据库。"""
    sources.remove_source(ctx, source_id)
    return Envelope(data=MutationResult(id=source_id, deleted=True))


@router.get("/datasources/{source_id}/health", response_model=Envelope[dict], operation_id="analytics_sources_health", summary="执行真实只读连接健康检查")
def source_health(source_id: str, ctx: Context):
    """兼容旧入口；检查结果写入脱敏历史，连接失败返回非 2xx 并仍保留失败证据。"""
    result = governance.run_health_check(ctx, source_id)
    return Envelope(data=result.model_dump(), message=result.status)


@router.post("/datasources/{source_id}/health-checks", response_model=Envelope[DataSourceHealthView], operation_id="analytics_sources_health_run", summary="执行并记录数据源健康检查")
def run_source_health(source_id: str, ctx: Context):
    """需要执行权限；实际打开只读连接，成功和脱敏失败都会形成不可变检查记录。"""
    result = governance.run_health_check(ctx, source_id)
    return Envelope(data=result, message=result.status)


@router.get("/datasources/{source_id}/health-checks", response_model=Envelope[list[DataSourceHealthView]], operation_id="analytics_sources_health_history", summary="查看数据源健康检查历史")
def source_health_history(
    source_id: str, ctx: Context,
    limit: int = Query(50, ge=1, le=200, description="最多返回的检查记录数"),
):
    """沿用数据源查看范围；不返回连接串、驱动堆栈或密码。"""
    return Envelope(data=governance.list_health_checks(ctx, source_id, limit))


@router.get("/datasources/{source_id}/policy", response_model=Envelope[DataSourcePolicyView], operation_id="analytics_sources_policy_get", summary="查看数据源资源与健康策略")
def source_policy(source_id: str, ctx: Context):
    """显示真实活跃查询租约、当日尝试数和统一调度状态。"""
    return Envelope(data=governance.get_policy(ctx, source_id))


@router.put("/datasources/{source_id}/policy", response_model=Envelope[DataSourcePolicyView], operation_id="analytics_sources_policy_update", summary="按版本保存数据源治理策略")
def update_source_policy(source_id: str, payload: DataSourcePolicyInput, ctx: Context):
    """并发/日限额需要编辑权限；启用自动健康检查还需要执行与系统运维管理权限。"""
    return Envelope(data=governance.update_policy(ctx, source_id, payload))


@router.get("/datasources/{source_id}/pool-status", response_model=Envelope[dict], operation_id="analytics_sources_pool", summary="查看分析连接的资源策略")
def source_pool(source_id: str, ctx: Context):
    """短连接仍为 NullPool；活跃数来自跨请求查询租约而不是伪造的常驻连接。"""
    return Envelope(data=governance.resource_status(ctx, source_id))


@router.get("/datasources/{source_id}/tables", response_model=Envelope[list[str]], operation_id="analytics_sources_tables", summary="读取数据源的可访问表名")
def source_tables(source_id: str, ctx: Context):
    """返回真实元数据，按表/字段配置过滤；新配置自动使旧缓存失效。"""
    record = metadata.source_metadata(ctx, sources.get_source(ctx, source_id, active=True))
    ctx.db.commit()
    return Envelope(data=[table["tableName"] for table in record.tables_json])


@router.get("/datasources/{source_id}/tables/info", response_model=Envelope[list[TableView]], operation_id="analytics_sources_metadata", summary="读取表、字段、注释和外键")
def source_tables_info(source_id: str, ctx: Context):
    """未统计行数时返回 null，不将未知数量当成 0；关联仅来自真实外键。"""
    record = metadata.source_metadata(ctx, sources.get_source(ctx, source_id, active=True))
    ctx.db.commit()
    return Envelope(data=record.tables_json)


@router.get("/datasources/{source_id}/tables/{table_name}/schema", response_model=Envelope[TableView], operation_id="analytics_sources_table_schema", summary="读取选定表的字段结构")
def source_table_schema(source_id: str, table_name: str, ctx: Context):
    """只能读取该数据源授权表与字段，不存在或无权访问时返回 404。"""
    result = metadata.table_metadata(ctx, sources.get_source(ctx, source_id, active=True), table_name)
    ctx.db.commit()
    return Envelope(data=result)


@router.post("/datasources/{source_id}/sync", response_model=Envelope[dict], operation_id="analytics_sources_sync", summary="同步真实数据源元数据")
def sync_source(source_id: str, ctx: Context):
    """需要 datasources.execute；完成后返回真实表数和配置版本，不仅返回已启动。"""
    row = sources.get_source(ctx, source_id, "datasources.execute", active=True)
    result = metadata.source_metadata(ctx, row, refresh=True)
    commit_change(ctx, "datasource.metadata.sync", "datasources", source_id)
    ctx.db.refresh(row)
    summary = row.connection_config or {}
    return Envelope(data={
        "tables": len(result.tables_json), "sourceVersion": result.source_version,
        "status": result.status,
        "businessTables": int(summary.get("businessTableCount") or 0),
        "fields": int(summary.get("fieldCount") or 0),
        "commentedTables": int(summary.get("commentedTableCount") or 0),
        "commentedFields": int(summary.get("commentedFieldCount") or 0),
        "semanticIndexStatus": summary.get("semanticIndexStatus") or "not_indexed",
        "semanticIndexedTables": int(summary.get("semanticIndexedTableCount") or 0),
        "autoDescription": summary.get("autoDescription") or "",
        "businessDomains": summary.get("businessDomains") or [],
    })


@router.post("/query", response_model=Envelope[QueryResult], operation_id="analytics_query", summary="执行自然语言或手工 SQL 分析")
def query(payload: QueryInput, ctx: Context):
    """需要 analytics.execute 和数据源访问权限；共用 AST、表字段、参数、只读事务、超时及返回行限制。"""
    return Envelope(data=queries.execute_query(ctx, payload))


@router.post("/inventory/query", response_model=Envelope[QueryResult], operation_id="analytics_inventory_query", summary="直接读取库存、仓库或物料数据")
def inventory_query(payload: InventoryQueryInput, ctx: Context):
    """按已授权元数据执行确定性查询，保留原始空值、分页范围与持久结果来源。"""
    return Envelope(data=inventory.query_inventory(ctx, payload))


@router.post("/execute-sql", response_model=Envelope[QueryResult], operation_id="analytics_execute_sql", summary="运行已绑定组件的只读 SQL")
def execute_sql(ctx: Context, datasource_id: str = Query(alias="datasourceId", min_length=1, max_length=26, description="组件绑定的数据源"),
                sql: str = Query(min_length=1, max_length=30000, pattern=r"\S", description="只读 SQL"), max_rows: int = Query(1000, alias="maxRows", ge=1, le=10000, description="返回行上限")):
    """报表组件与人工查询遵守同一校验，不接受任意写 SQL。"""
    return Envelope(data=queries.execute_query(ctx, QueryInput(datasourceId=datasource_id, query=sql, mode="sql", maxRows=max_rows)))


@router.get("/history/recent", response_model=Envelope[list[HistoryView]], operation_id="analytics_history_recent", summary="查看授权范围内的查询历史")
def recent_history(ctx: Context, limit: int = Query(100, ge=1, le=500, description="本页条数"),
                   offset: int = Query(0, ge=0, description="分页起始偏移")):
    """重新检查分析结果与源数据权限，不通过历史绕过已撤销的数据访问。"""
    return Envelope(data=queries.list_history(ctx, limit=limit, offset=offset))


@router.get("/history/datasource/{source_id}", response_model=Envelope[list[HistoryView]], operation_id="analytics_history_source", summary="查看某数据源的查询历史")
def datasource_history(source_id: str, ctx: Context, limit: int = Query(100, ge=1, le=500, description="最多返回条数")):
    """历史记录包含实际 SQL、状态、耗时和错误，不依赖浏览器本地聊天记录。"""
    return Envelope(data=queries.list_history(ctx, source_id=source_id, limit=limit))


@router.get("/history/{query_id}", response_model=Envelope[QueryResult], operation_id="analytics_history_result", summary="恢复已保存的分析结果")
def history_result(query_id: str, ctx: Context):
    """结果可独立打开和刷新；再次检查 analytics.view 与源数据范围。"""
    return Envelope(data=queries.historical_result(ctx, query_id))


@router.post("/quality-check", response_model=Envelope[dict], operation_id="analytics_quality_check", summary="对选定表字段执行数据质量检查")
def quality_check(payload: QualityInput, ctx: Context):
    """返回真实样本的缺失、重复与 IQR 离群统计，明确样本和截断情况，不自动修改源数据。"""
    return Envelope(data=queries.check_quality(ctx, payload))


@router.get("/query-templates", response_model=Envelope[list[QueryTemplateView]], operation_id="analytics_query_templates_list", summary="列出可复用查询模板")
def query_template_list(ctx: Context, keyword: str = Query("", max_length=160, description="模板名称关键词")):
    """模板按分析模块的数据权限过滤，不预设用户数据库中的表名。"""
    return Envelope(data=query_templates.list_templates(ctx, keyword))


@router.post("/query-templates", response_model=Envelope[QueryTemplateView], status_code=201, operation_id="analytics_query_templates_create", summary="保存自然语言或参数化 SQL 查询模板")
def create_query_template(payload: QueryTemplateInput, ctx: Context):
    """需要 analytics.create；保存模板不代表执行 SQL。"""
    return Envelope(data=query_templates.save_template(ctx, payload))


@router.put("/query-templates/{template_id}", response_model=Envelope[QueryTemplateView], operation_id="analytics_query_templates_update", summary="按版本编辑查询模板")
def update_query_template(template_id: str, payload: QueryTemplateInput, ctx: Context):
    """需要 analytics.edit；并发版本冲突返回 409。"""
    return Envelope(data=query_templates.save_template(ctx, payload, template_id))


@router.post("/query-templates/{template_id}/use", response_model=Envelope[QueryTemplateView], operation_id="analytics_query_templates_use", summary="复用查询模板并记录使用次数")
def use_query_template(template_id: str, ctx: Context):
    """只读取并记录复用，不自动执行查询；执行时单独检查 analytics.execute。"""
    return Envelope(data=query_templates.use_template(ctx, template_id))


@router.delete("/query-templates/{template_id}", response_model=Envelope[MutationResult], operation_id="analytics_query_templates_delete", summary="删除查询模板")
def delete_query_template(template_id: str, ctx: Context):
    """需要 analytics.delete 及对应数据范围，不删除已生成的查询结果。"""
    query_templates.delete_template(ctx, template_id)
    return Envelope(data=MutationResult(id=template_id, deleted=True))


@router.post("/export", operation_id="analytics_export", summary="导出已授权的真实查询结果",
             responses={200: {"description": "CSV/XLSX/JSON/HTML/Markdown 文件", "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}})
def export(payload: ExportInput, ctx: Context):
    """需要 analytics.export 和当前数据源权限；CSV/XLSX 防公式注入，HTML 转义，不接受服务器输出路径。"""
    data, mime, filename = exports.export_query(ctx, payload.query_id, payload.format)
    return Response(data, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{filename}"',
                                                   "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})
