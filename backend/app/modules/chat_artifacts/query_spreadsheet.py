"""将本任务的查询回执直接导出为 Excel，避免模型重抄表格数值。"""
from __future__ import annotations


def spreadsheet_filename(value: str | None, default_name: str) -> str:
    """在生成阶段确定 Excel 文件名，避免用工作区文件工具给工件改名。"""
    from pathlib import Path
    from ...errors import ValidationError
    from ..files.service import normalize_filename

    name = normalize_filename(value if value is not None else default_name)
    extension = Path(name).suffix.lower()
    if extension and extension != ".xlsx":
        raise ValidationError("Excel 导出文件名必须使用 .xlsx 扩展名")
    return normalize_filename(name if extension else name + ".xlsx")


def export_query_spreadsheet(ctx, tool_context: dict, args: dict):
    """先验证回执范围，再复用带公式保护和数据来源页的正式导出服务。"""
    from .chart_renderer import load_authorized_query
    from ..analytics.exports import export_query
    from ..artifact.service import create_artifact, artifact_reference
    from ...common.artifact_fonts import normalize_office_fonts
    from ...models import Run
    from ...providers.tool.base import ToolResult

    query = load_authorized_query(ctx, tool_context, str(args.get("query_id") or ""))
    # 工件归属尽量使用经 load_authorized_query 验证过的运行记录（评审 R26 防御性改进）；
    # 生产路径 ctx.db 一定存在；单元/夹具路径（db=None）则回退调用方上下文标识，
    # 此时真实路径的 load_authorized_query 已独立完成本机归属校验。
    artifact_run_id = tool_context.get("run_id")
    if ctx.db is not None:
        run = ctx.db.get(Run, artifact_run_id)
        if run is None or run.workspace_id != ctx.workspace_id or run.started_by_user_id != ctx.user_id:
            from ...errors import PermissionErrorEx
            raise PermissionErrorEx("Excel 导出缺少属于当前用户的运行记录")
        artifact_run_id = run.id
    filename = spreadsheet_filename(args.get("name"), f"analysis-{query.id}.xlsx")
    data, _, _ = export_query(ctx, query.id, "xlsx")
    data = normalize_office_fonts(data, ".xlsx")
    row = create_artifact(ctx.db, workspace_id=ctx.workspace_id, run_id=artifact_run_id,
        owner_user_id=ctx.user_id, type="spreadsheet", name=filename, data=data,
        metadata={"tool": "export_query_spreadsheet", "delivery_evidence": {
            "kind": "xlsx", "producer": "server_renderer.v1", "source_query_id": query.id,
            "truncated": bool((query.result_json or {}).get("truncated"))}})
    return ToolResult(status="success", data={"artifact_id": row.id, "name": filename, "query_id": query.id,
        "truncated": bool((query.result_json or {}).get("truncated")),
        "description": "保存的查询结果原样导出，宋体，包含数据来源页及真实截断标识。"},
        artifacts=[artifact_reference(row)])
