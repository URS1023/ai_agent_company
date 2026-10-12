"""One implementation for AI chat tools and documented external business actions."""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from hashlib import sha256
import json
from urllib.parse import urlencode, urlsplit

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.common.authorization import access_policy, require_permission
from app.common.deps import AuthContext
from app.errors import AppError, ConflictError, ValidationError
from app.modules.alerts import reports as alert_reports
from app.modules.analytics.models import SourceMetadata
from app.modules.analytics.sources import get_source, list_sources as list_analytics_sources, source_view
from app.modules.analytics.semantic import (
    build_semantic_index, is_business_table, recall_tables, source_match_score,
)
from app.modules.collection.sources import list_sources as list_http_sources
from app.modules.business_ai.models import BusinessActionReceipt
from app.modules.business_ai.schemas import (
    AlertOverviewInput, AlertOverviewView, BusinessActionCapability, BusinessActionLink,
    BusinessDataSourceSummary, DashboardDraftInput, DashboardDraftView,
    DataSourceDiscoveryInput, DataSourceDiscoveryView,
)
from app.modules.reports import generation as report_generation
from app.modules.reports import service as report_service
from app.modules.reports.generation_quality import extract_requirement_title
from app.modules.reports.schemas import GenerationInput, ReportInput


# AI 业务动作目录：这是“能力清单”的唯一事实来源，接口与聊天工具都从这里派生。
# 每项必须写清 capabilityId/operationId/权限/读写性质/幂等策略/结果组件，因为：
#   1) AI 只能调用这里登记的动作（未登记的能力不会出现在提示与目录里）；
#   2) 目录同时是对外契约，前端据 resultComponent 决定渲染哪种结果卡片；
#   3) permissions 会在工具入口与业务服务里**各校验一次**，目录里写错权限会导致
#      “看着可用但调用即失败”，因此这里的列表必须与真实路由权限保持一致。
_ACTION_DEFINITIONS = (
    {
        "capabilityId": "business.datasources.discover",
        "operationId": "ai_business_datasources_discover",
        "name": "发现可用业务数据",
        "description": "列出当前账号可访问、已启用的数据分析源及已同步元数据；不连接未授权数据，也不自动选择第一项。",
        "utterances": ["有哪些可用数据", "查找财务和设备数据源", "我能分析哪些数据库"],
        "schema": DataSourceDiscoveryInput,
        "permissions": ["datasources.view"],
        "readWrite": "read",
        "idempotencyPolicy": "只读，无持久化副作用",
        "resultComponent": "resource_resolution",
        "modulePath": "/datasources",
    },
    {
        "capabilityId": "alerts.overview",
        "operationId": "ai_business_alerts_overview",
        "name": "查看设备预警",
        "description": "读取持久化预警报告和问题处置状态；不会重新请求设备、重跑工作流或把无数据解释为正常。",
        "utterances": ["查看设备预警", "有哪些设备问题", "打开最近预警报告"],
        "schema": AlertOverviewInput,
        "permissions": ["alerts.view"],
        "readWrite": "read",
        "idempotencyPolicy": "只读，无持久化副作用",
        "resultComponent": "alert_problem_list",
        "modulePath": "/alerts/reports",
    },
    {
        "capabilityId": "dashboard.generate",
        "operationId": "ai_business_dashboard_draft_create",
        "name": "生成业务数字大屏草稿",
        "description": "从授权数据库元数据和逐项验证的只读查询，或已授权固定 HTTP 采集结果，生成可编辑大屏草稿；数据库组件只使用逐项验证 SQL，HTTP 组件只使用固定采集结果，不公开发布、不写生产表，也不使用演示数据兜底。",
        "utterances": ["生成财务和设备数字大屏", "做一个经营和设备看板", "把这些真实数据做成大屏"],
        "schema": DashboardDraftInput,
        # The workbench scene starts in auto-template mode.  Keep the catalog
        # conservative so an account never sees an executable-looking entry
        # that is guaranteed to fail when template matching begins.
        "permissions": [
            "dashboards.view", "dashboards.create", "dashboards.execute",
            "datasources.view", "templates.view",
        ],
        "readWrite": "create",
        "idempotencyPolicy": "requestKey 或对话 Run 派生键；同键同参数恢复既有草稿，同键不同参数返回 409",
        "resultComponent": "dashboard",
        "modulePath": "/dashboards",
    },
)


def _workspace_report_path(kind: str, report_id: str, workspace_id: str) -> str:
    """Build a stable report route that cannot inherit another workspace selection."""

    # 显式把 workspace_id 写进链接：报表路由会读取它来定位资源，省略时浏览器里残留的
    # 其它工作区选择会被继承，导致打开到别人的/错误的大屏。
    return f"/report/{kind}/{report_id}?{urlencode({'workspace_id': workspace_id})}"


def _scope_dashboard_result(result: dict, workspace_id: str) -> dict:
    """Upgrade replayed legacy receipts to stable workspace-scoped report links."""

    # 幂等回放路径：早期版本写入凭据的链接没有 workspace_id，这里在返回前统一归一化，
    # 并由调用方**写回**凭据，使旧结果逐步升级；因此同一 requestKey 的重复调用
    # 始终返回一致结果。
    normalized = dict(result or {})
    report_id = str(normalized.get("reportId") or "").strip()
    if not report_id:
        return normalized
    normalized["previewPath"] = _workspace_report_path("view", report_id, workspace_id)
    actions = []
    for action in normalized.get("actions") or []:
        scoped_action = dict(action)
        href = str(scoped_action.get("href") or "")
        if href.startswith(f"/report/view/{report_id}"):
            scoped_action["href"] = _workspace_report_path("view", report_id, workspace_id)
        elif href.startswith(f"/report/designer/{report_id}"):
            scoped_action["href"] = _workspace_report_path("designer", report_id, workspace_id)
        actions.append(scoped_action)
    normalized["actions"] = actions
    return normalized


def action_catalog(ctx: AuthContext) -> list[BusinessActionCapability]:
    """Describe only real registered actions and current-user availability."""

    # 用途：GET /api/v1/ai/business-actions，返回当前用户可用的业务动作目录。
    # 权限：不做单一权限拦截，而是逐项用当前账号的权限集合计算出 available 与
    # unavailableReasons（只说明**缺少的权限名**，不泄露其他用户或资源信息）。
    # 能力清单来自 _ACTION_DEFINITIONS；未实现的能力不会登记为可执行，
    # 因此“不可用”只表示权限不足，不表示功能不存在。
    granted = access_policy(ctx).permissions
    result = []
    for definition in _ACTION_DEFINITIONS:
        missing = [permission for permission in definition["permissions"] if permission not in granted]
        result.append(BusinessActionCapability(
            capabilityId=definition["capabilityId"], operationId=definition["operationId"],
            name=definition["name"], description=definition["description"],
            utterances=definition["utterances"],
            inputSchema=definition["schema"].model_json_schema(by_alias=True),
            requiredPermissions=definition["permissions"], readWrite=definition["readWrite"],
            confirmationPolicy="当前动作无需额外确认；发布、生产写回、删除和权限调整不在此动作内",
            idempotencyPolicy=definition["idempotencyPolicy"],
            resultComponent=definition["resultComponent"], available=not missing,
            unavailableReasons=["缺少权限：" + permission for permission in missing],
            modulePath=definition["modulePath"], version=1,
        ))
    return result


def _discovery_table_summary(table: dict, all_tables: list[dict]) -> dict:
    """摘要优先保留主键、声明关联键及名称字段，并显式标记字段裁剪。"""
    name = str(table.get("tableName") or "")
    columns = list(table.get("columns") or [])
    required = {str(column.get("columnName")) for column in columns
                if column.get("primaryKey") or column.get("businessKey") or column.get("nameField")}
    for candidate in all_tables:
        candidate_name = str(candidate.get("tableName") or "")
        for relation in candidate.get("foreignKeys") or []:
            if candidate_name == name:
                required.update(str(value) for value in relation.get("columns") or [])
            if relation.get("referredTable") == name:
                required.update(str(value) for value in relation.get("referredColumns") or [])
        for relation in candidate.get("declaredRelations") or []:
            if candidate_name == name:
                required.add(str(relation.get("sourceField") or ""))
                required.update(str(value) for value in relation.get("columns") or [])
            if relation.get("targetTable") == name:
                required.update(str(relation.get(key) or "") for key in ("targetField", "targetNameField"))
                required.update(str(value) for value in relation.get("targetColumns") or [])
    key_columns = [column for column in columns if str(column.get("columnName")) in required]
    others = [column for column in columns if str(column.get("columnName")) not in required]
    selected = key_columns + others[:max(0, 20 - len(key_columns))]
    return {
        "name": table.get("tableName"), "description": table.get("tableComment") or "",
        "rowCount": table.get("rowCount"), "rowCountEstimated": bool(table.get("rowCountEstimated")),
        "retrievalEvidence": table.get("retrievalEvidence") or {},
        "fieldCount": len(columns), "returnedFieldCount": len(selected),
        "fieldsTruncated": len(selected) < len(columns),
        "foreignKeys": table.get("foreignKeys") or [],
        "declaredRelations": table.get("declaredRelations") or [],
        "fields": [{"name": column.get("columnName"), "type": column.get("dataType"),
                    "description": column.get("comment") or "",
                    "primaryKey": bool(column.get("primaryKey")), "nullable": column.get("nullable"),
                    **{key: column[key] for key in ("businessKey", "nameField", "semanticType", "enumSource", "enumComplete")
                       if key in column},
                    **({"enumValues": list(column["enumValues"])[:30],
                        "enumComplete": bool(column.get("enumComplete")) and len(column["enumValues"]) <= 30,
                        "enumTruncated": len(column["enumValues"]) > 30} if isinstance(column.get("enumValues"), list) else {})}
                   for column in selected],
    }


def discover_data_sources(ctx: AuthContext, payload: DataSourceDiscoveryInput) -> DataSourceDiscoveryView:
    """通过平台缓存发现数据库与 HTTP 来源，不连接业务库、外部模型或业务 HTTP。"""

    require_permission(ctx, "datasources.view")
    keyword = payload.keyword.strip()
    database_rows = list_analytics_sources(ctx, enabled_only=True)
    http_rows = [row for row in list_http_sources(ctx, limit=500) if row.enabled]
    all_metadata = {
        row.datasource_id: row for row in ctx.db.scalars(select(SourceMetadata).where(
            SourceMetadata.workspace_id == ctx.workspace_id,
            SourceMetadata.datasource_id.in_([item.id for item in database_rows] or [""]),
        )).all()
    }

    ranked_rows: list[tuple[float, int, str, str, object, object, list[dict], list[str]]] = []
    for row in database_rows:
        metadata = all_metadata.get(row.id)
        current = metadata is not None and metadata.status == "ready" and metadata.source_version == row.version
        # 连接或字段范围变更后，旧快照不能再被发现结果当作可查询结构返回。
        tables = list((metadata.tables_json if current else []) or [])
        score, matched = source_match_score(row, tables, keyword) if keyword else (0.0, [])
        if keyword and score <= 0:
            continue
        ranked_rows.append((
            score, int(row.businessTableCount or 0), row.name, "database",
            row, metadata, tables, matched,
        ))
    for row in http_rows:
        fields = [{
            "columnName": field.name,
            "dataType": field.type,
            "comment": field.comment,
        } for field in row.fields]
        configured_description = row.description.strip()
        derived_description = (
            f"已配置的 {row.method} HTTP 数据接口，按 {row.recordPath} 提取"
            + (f" {len(fields)} 个业务字段" if fields else "响应记录")
        )
        discovery_description = configured_description or derived_description
        matching_table = [{
            "tableName": row.name,
            "tableComment": discovery_description,
            "columns": fields,
        }]
        score, matched = source_match_score(row, matching_table, keyword) if keyword else (0.0, [])
        if keyword and score <= 0:
            continue
        ranked_rows.append((
            score, len(fields), row.name, "http", row, None, matching_table, matched,
        ))
    ranked_rows.sort(
        key=lambda item: (item[0], item[1], item[2]), reverse=True,
    )
    rows = ranked_rows
    selected = rows[:payload.limit]
    summaries = []
    for score, _, _, resource_type, row, metadata, metadata_tables, matched in selected:
        if resource_type == "http":
            configured_description = row.description.strip()
            field_count = len(row.fields)
            field_comments = sum(bool(field.comment.strip()) for field in row.fields)
            description = configured_description or (
                f"已配置的 {row.method} HTTP 数据接口，按 {row.recordPath} 提取"
                + (f" {field_count} 个业务字段" if field_count else "响应记录")
            )
            parsed = urlsplit(row.url)
            target = (parsed.netloc + (parsed.path or "/"))[:500]
            summaries.append(BusinessDataSourceSummary(
                id=row.id, name=row.name, provider="http", resourceType="http",
                description=description,
                descriptionSource="configured" if configured_description else "metadata",
                environmentSummary=f"HTTP {row.method} · {target}", version=row.version,
                metadataStatus="ready", tableCount=1, businessTableCount=1,
                fieldCount=field_count,
                commentCoverage={
                    "tables": {"commented": int(bool(description)), "total": 1},
                    "fields": {"commented": field_comments, "total": field_count},
                },
                semanticIndexStatus="keyword_only", semanticIndexedTableCount=0,
                businessDomains=["HTTP/API"], automaticReady=bool(row.allowAutomatic),
                matchEvidence={"score": round(score, 4), "matchedTerms": matched},
                returnedTableCount=1, tables=[{
                    "name": row.name,
                    "description": description,
                    "rowCount": None,
                    "rowCountEstimated": True,
                    "retrievalEvidence": {"recordPath": row.recordPath, "method": row.method},
                    "fields": [{
                        "name": field.name, "type": field.type, "description": field.comment,
                    } for field in row.fields],
                }],
            ))
            continue
        status = "not_synced"
        tables = []
        if metadata is not None:
            status = "ready" if metadata.status == "ready" and metadata.source_version == row.version else "stale"
            source_record = None
            business_table_count = sum(
                is_business_table(str(table.get("tableName") or ""))
                for table in metadata_tables
            )
            if status == "ready" and (
                row.semanticIndexStatus not in {"ready", "keyword_only"}
                or row.semanticIndexedTableCount != business_table_count
                or row.businessTableCount != business_table_count
                or not row.autoDescription
            ):
                source_record = get_source(ctx, row.id, active=True)
                build_semantic_index(ctx, source_record, metadata_tables, cached_only=True)
                # This endpoint is read-only from the user's perspective, but
                # the semantic catalog is a durable derived cache.  Explicitly
                # commit the one-time upgrade because request teardown does not
                # commit flushed ORM rows.
                ctx.db.commit()
                ctx.db.refresh(source_record)
                row = source_view(source_record)
            selected_tables = [
                table for table in metadata_tables
                if is_business_table(str(table.get("tableName") or ""))
            ][:20]
            evidence = []
            if keyword and status == "ready":
                source_record = source_record or get_source(ctx, row.id, active=True)
                selected_tables, evidence = recall_tables(
                    ctx, source_record, metadata_tables, keyword,
                    seed_k=8, max_extra=4, include_samples=False, cached_only=True,
                )
                # recall_tables lazily upgrades sources created before the
                # semantic catalog existed; expose that upgraded profile in
                # this same response rather than one request later.
                ctx.db.commit()
                ctx.db.refresh(source_record)
                row = source_view(source_record)
            summary_scope = metadata_tables + selected_tables
            tables = [_discovery_table_summary(table, summary_scope) for table in selected_tables[:20]]
        summaries.append(BusinessDataSourceSummary(
            id=row.id, name=row.name, provider=row.type, resourceType="database",
            description=row.description,
            descriptionSource=row.descriptionSource, environmentSummary=row.environmentSummary,
            version=row.version, metadataStatus=status,
            tableCount=row.tableCount, businessTableCount=row.businessTableCount,
            fieldCount=row.fieldCount,
            commentCoverage={
                "tables": {"commented": row.commentedTableCount, "total": row.businessTableCount},
                "fields": {"commented": row.commentedFieldCount, "total": row.fieldCount},
            },
            semanticIndexStatus=row.semanticIndexStatus,
            semanticIndexedTableCount=row.semanticIndexedTableCount,
            businessDomains=row.businessDomains, automaticReady=status == "ready",
            matchEvidence={"score": round(score, 4), "matchedTerms": matched},
            returnedTableCount=len(tables), tablesTruncated=row.businessTableCount > len(tables),
            tables=tables,
        ))
    return DataSourceDiscoveryView(
        sources=summaries, returnedCount=len(summaries), truncated=len(rows) > len(selected),
        selectionPolicy=(
            "组合关键词分别匹配；按用户权限和启用状态过滤；按数据源说明、表/字段注释与语义索引排序，"
            "数据库使用已声明关系扩展相关表，HTTP/API 使用已保存的提取字段；"
            "多个候选由用户明确勾选，不默认取第一项，发现过程不会连接数据库或发送 HTTP 请求"
        ),
    )


def alert_overview(ctx: AuthContext, payload: AlertOverviewInput) -> AlertOverviewView:
    """独立读取报告、问题与处置日志，只统计本次授权页，不触发设备或工作流。"""

    require_permission(ctx, "alerts.view")
    from .alert_evidence import OUTCOME_LABELS, EXECUTION_LABELS, HANDLING_LABELS, label, alert_evidence_message
    history = None
    reports_offset = payload.offset if payload.reports_offset is None else payload.reports_offset
    problems_offset = payload.offset if payload.problems_offset is None else payload.problems_offset
    if payload.problem_id:
        problem = alert_reports.get_problem(ctx, payload.problem_id)
        if payload.report_id and problem.report_id != payload.report_id:
            raise ValidationError("该问题不属于指定报告")
        report_rows = [alert_reports.report_view(ctx, alert_reports.get_report(ctx, problem.report_id))]
        problem_rows = [alert_reports.problem_view(problem)]
        events = alert_reports.problem_events(ctx, problem.id, limit=payload.limit + 1, offset=payload.offset)
        history = {'problemId': problem.id, 'currentStatus': problem.status, 'currentNote': problem.note,
                   'events': events[:payload.limit], 'offset': payload.offset,
                   'hasMore': len(events) > payload.limit,
                   'nextOffset': payload.offset + payload.limit if len(events) > payload.limit else None}
    elif payload.report_id:
        report_rows = [alert_reports.report_view(ctx, alert_reports.get_report(ctx, payload.report_id))]
        problem_rows = alert_reports.list_problems(ctx, limit=payload.limit + 1, offset=problems_offset,
                                                   report_id=payload.report_id)
    else:
        report_rows = alert_reports.list_reports(ctx, limit=payload.limit + 1, offset=reports_offset)
        problem_rows = alert_reports.list_problems(ctx, limit=payload.limit + 1, offset=problems_offset)
    reports_have_more = not payload.problem_id and not payload.report_id and len(report_rows) > payload.limit
    problems_have_more = not payload.problem_id and len(problem_rows) > payload.limit
    report_rows = report_rows[:payload.limit]
    problem_rows = problem_rows[:payload.limit]
    if payload.problem_id or payload.report_id:
        reports_offset = 0
    if payload.problem_id:
        problems_offset = 0

    def page_scope(offset: int, count: int, has_more: bool) -> dict:
        """首个完整页才知道总量；末页起点可能超过真实总数，不能直接相加猜总量。"""
        total_known = offset == 0 and not has_more
        return {"offset": offset, "limit": payload.limit, "returnedCount": count,
                "hasMore": has_more, "nextOffset": offset + count if has_more else None,
                "totalKnown": total_known, "total": count if total_known else None}

    reports = [{
        "id": row["id"], "title": row["title"], "deviceName": row["deviceName"],
        "deviceId": row.get("deviceId"), "executionId": row.get("executionId"),
        "workflowId": row.get("workflowId"),
        "outcome": row["outcome"], "executionStatus": row["executionStatus"],
        "outcomeLabel": label(OUTCOME_LABELS, row["outcome"]),
        "executionStatusLabel": label(EXECUTION_LABELS, row["executionStatus"]),
        "rows": (row.get("summary") or {}).get("rows", (row.get("summary") or {}).get("totalRows")),
        "issues": (row.get("summary") or {}).get("issues", (row.get("summary") or {}).get("issueCount")), "createTime": row["createTime"],
        "href": "/alerts/reports?report=" + row["id"],
    } for row in report_rows]
    problems = [{
        "id": row["id"], "reportId": row["reportId"], "recordKey": row.get("recordKey") or "",
        "ruleName": row.get("ruleName") or "", "message": row.get("message") or "",
        "severity": row.get("severity", row.get("level")) or "", "status": row.get("status") or "",
        "statusLabel": label(HANDLING_LABELS, row.get("status")),
        "note": row.get("note") or "", "version": row.get("version"),
        "createTime": row.get("createTime") or "", "href": "/alerts/reports?report=" + row["reportId"],
    } for row in problem_rows]
    status_counts = Counter(row.get("status") or "unknown" for row in problem_rows)
    severity_counts = Counter(row.get("severity") or "unknown" for row in problems)
    report_ids = {row["id"] for row in report_rows}
    related_problems = sum(row["reportId"] in report_ids for row in problem_rows)
    actions = [BusinessActionLink(label="打开预警报告", href="/alerts/reports", kind="primary", permission="alerts.view")]
    if problem_rows:
        actions.append(BusinessActionLink(label="查看问题列表", href="/alerts/problems", permission="alerts.view"))
    view = AlertOverviewView(
        title="设备预警与问题",
        summary={
            "returnedReports": len(report_rows), "returnedProblems": len(problem_rows),
            "statusCounts": dict(status_counts), "severityCounts": dict(severity_counts),
            "reportsMayHaveMore": reports_have_more,
            "problemsMayHaveMore": problems_have_more,
            "offset": payload.offset,
            "reportsPage": page_scope(reports_offset, len(report_rows), reports_have_more),
            "problemsPage": page_scope(problems_offset, len(problem_rows), problems_have_more),
            "relationship": ("specified_problem" if payload.problem_id else
                             "specified_report" if payload.report_id else "independent_recent_records"),
            "returnedProblemsInReportPage": related_problems,
            "returnedProblemsOutsideReportPage": len(problem_rows) - related_problems,
            "scopeNote": "数量仅代表本次当前账号已授权的返回页，不冒充全工作区总数",
        },
        reports=reports, problems=problems, actions=actions, processingHistory=history,
        provenance={
            "workspaceId": ctx.workspace_id, "actorUserId": ctx.user_id,
            "permission": "alerts.view", "retrievedAt": datetime.utcnow().isoformat(),
            "source": "持久化预警报告与问题；未重新请求设备",
        },
    )
    view.message = alert_evidence_message(view.model_dump(mode='json'))
    return view


def _request_hash(payload: DashboardDraftInput) -> str:
    """计算除防重复键外的规范化请求摘要，用于识别“同键不同参数”。

    用 sort_keys + 紧凑分隔符保证同样的参数得到同样的哈希；request_key 本身被排除，
    因为它只用于定位凭据，不参与内容比较。
    """
    document = payload.model_dump(by_alias=True, mode="json", exclude={"request_key"})
    return sha256(json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _existing_receipt(ctx: AuthContext, payload: DashboardDraftInput, digest: str) -> BusinessActionReceipt | None:
    """按 (工作区, 用户, requestKey) 查已存在的业务动作凭据，并给出幂等判定。

    返回 None 表示首次请求；返回 completed 凭据表示可安全回放既有结果；
    其余情况一律抛 ``ConflictError``：同键不同参数（换键）、仍在执行（禁止并发重试）、
    此前失败（失败不会自动重建，必须换新的 requestKey）。这样网络重试永远不会
    变成重复创建大屏，也不会把失败静默重试成“成功”。
    """
    receipt = ctx.db.scalar(select(BusinessActionReceipt).where(
        BusinessActionReceipt.workspace_id == ctx.workspace_id,
        BusinessActionReceipt.owner_user_id == ctx.user_id,
        BusinessActionReceipt.request_key == payload.request_key,
    ))
    if receipt is None:
        return None
    if receipt.action != "dashboard.generate" or receipt.request_hash != digest:
        raise ConflictError("此防重复键已用于另一项业务配置，请更换 requestKey",
                            receipt_id=receipt.id, resource_id=receipt.resource_id, retryable=False)
    if receipt.status == "completed":
        return receipt
    if receipt.status == "running":
        raise ConflictError("相同大屏请求仍在执行，请使用运行记录核对后再决定是否重试",
                            receipt_id=receipt.id, invocation_run_id=receipt.invocation_run_id,
                            receipt_status="running", execution_started=True, retryable=False)
    raise ConflictError("相同大屏请求此前失败；失败不会自动重复创建，请修正后使用新的 requestKey",
                        receipt_id=receipt.id, receipt_status="failed", execution_started=True, retryable=False)


def _completed_invocation_receipt(
    ctx: AuthContext,
    invocation_run_id: str | None,
) -> BusinessActionReceipt | None:
    """Return the first dashboard delivered by one conversational Run.

    A model may refine its arguments and call the creation tool again after it
    has already received a successful result.  Request-key idempotency alone
    cannot catch that case because the argument hash changes.  The business
    contract is one delivered dashboard per user message/Run, so a successful
    receipt is authoritative regardless of later wording changes.
    """
    if not invocation_run_id:
        return None
    return ctx.db.scalar(
        select(BusinessActionReceipt).where(
            BusinessActionReceipt.workspace_id == ctx.workspace_id,
            BusinessActionReceipt.owner_user_id == ctx.user_id,
            BusinessActionReceipt.action == "dashboard.generate",
            BusinessActionReceipt.invocation_run_id == invocation_run_id,
            BusinessActionReceipt.status == "completed",
        ).order_by(BusinessActionReceipt.created_at.asc(), BusinessActionReceipt.id.asc())
    )


def create_dashboard_draft(
    ctx: AuthContext,
    payload: DashboardDraftInput,
    *,
    invocation_run_id: str | None = None,
) -> DashboardDraftView:
    """Generate verified content, then save one recoverable draft in the report module."""

    for permission in ("dashboards.view", "dashboards.create", "dashboards.execute", "datasources.view"):
        require_permission(ctx, permission)
    if payload.template_mode in {"auto", "manual"}:
        require_permission(ctx, "templates.view")
    available_sources = list_analytics_sources(ctx, enabled_only=True)
    available_http_sources = [row for row in list_http_sources(ctx, limit=500) if row.enabled]
    available_ids = {source.id for source in (*available_sources, *available_http_sources)}
    if not available_ids:
        raise ValidationError("当前没有已授权且启用的数据源，请先在数据分析或固定 HTTP 采集模块接入或启用数据源")
    invalid_ids = [
        source_id for source_id in payload.datasource_ids
        if source_id not in available_ids
    ]
    if invalid_ids:
        raise ValidationError(
            "所选数据源不是当前账号可用的分析数据源；请使用数据源发现结果中的标识，或留空让系统自动发现",
            invalid_datasource_ids=invalid_ids,
            available_datasources=[
                {"id": source.id, "name": source.name}
                for source in [*available_sources, *available_http_sources][:8]
            ],
        )
    delivered = _completed_invocation_receipt(ctx, invocation_run_id)
    if delivered is not None:
        result = _scope_dashboard_result(delivered.result_json, ctx.workspace_id)
        if result != delivered.result_json:
            delivered.result_json = result
            ctx.db.commit()
        return DashboardDraftView.model_validate({**result, "reused": True})
    digest = _request_hash(payload)
    previous = _existing_receipt(ctx, payload, digest)
    if previous is not None:
        result = _scope_dashboard_result(previous.result_json, ctx.workspace_id)
        if result != previous.result_json:
            previous.result_json = result
            ctx.db.commit()
        return DashboardDraftView.model_validate({**result, "reused": True})

    # 输入范围不完整时尚未启动生成，不占用防重复键；补齐参数后可继续同一次交付。
    if not payload.datasource_ids and len(available_ids) > 1:
        raise ValidationError(
            "当前有多个可用数据源，请选择本次大屏的数据范围",
            requires_user_input=True, missing_fields=["datasource_ids"], recovery="select_datasource",
            execution_started=False, retryable=True, auto_retry=False,
            candidates=[{"id": source.id, "name": source.name}
                        for source in [*available_sources, *available_http_sources][:20]],
        )

    receipt = BusinessActionReceipt(
        workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id,
        action="dashboard.generate", request_key=payload.request_key,
        request_hash=digest, status="running", invocation_run_id=invocation_run_id,
    )
    ctx.db.add(receipt)
    try:
        ctx.db.commit()
    except IntegrityError:
        # Two network retries can pass the initial read before either inserts.
        # The unique actor/request key remains the final authority: re-read the
        # winner and return/raise the same deterministic idempotency outcome
        # instead of leaking a database 500 response.
        ctx.db.rollback()
        winner = _existing_receipt(ctx, payload, digest)
        if winner is not None:
            result = _scope_dashboard_result(winner.result_json, ctx.workspace_id)
            if result != winner.result_json:
                winner.result_json = result
                ctx.db.commit()
            return DashboardDraftView.model_validate({**result, "reused": True})
        raise
    try:
        report_title = payload.title or extract_requirement_title(payload.requirement) or None
        from app.modules.reports import query_delivery
        verified = query_delivery.verified_queries(ctx, payload, invocation_run_id)
        if verified:
            generated = query_delivery.generate(ctx, payload, verified)
            # The requirement now contains server-side query receipts. It is
            # execution context, not a customer-visible resource title.
            report_title = generated["title"]
        else:
            generated = report_generation.generate(ctx, GenerationInput(
                datasourceIds=payload.datasource_ids, theme=payload.requirement,
                requirementDocument=payload.requirement_document,
                title=report_title,
                width=payload.width, height=payload.height,
                templateMode=payload.template_mode, templateId=payload.template_id,
                sessionId=invocation_run_id or payload.request_key,
            ), kind="screen")
        source_ids = generated["datasourceIds"]
        sources = [get_source(ctx, source_id, provider="any") for source_id in source_ids]
        saved = report_service.save_report(ctx, ReportInput(
            reportName=payload.title or report_title or generated["title"],
            description="由 AI 工作台按真实授权数据生成的可编辑草稿；未发布，未执行生产写回。",
            datasourceId=source_ids[0] if source_ids else None,
            datasourceIds=source_ids,
            content={"canvas": generated["canvas"], "widgets": generated["widgets"]},
            templateMode=generated["templateMode"], templateId=generated["templateId"],
            width=int(generated["canvas"].get("width") or payload.width),
            height=int(generated["canvas"].get("height") or payload.height), status="draft",
            editTemplateLayout=bool(generated.get("templateAdapted")),
            thumbnail="" if generated.get("templateAdapted") else None,
        ), lineage={
            "entry": "ai_workbench", "capabilityId": "dashboard.generate",
            "generationId": generated["generationId"], "generationRunId": generated["runId"],
            "invocationRunId": invocation_run_id, "queryIds": generated["queryIds"],
            "sourceQueryIds": generated.get("sourceQueryIds") or [],
            "generationMode": generated.get("generationMode") or "model",
            "templateReference": generated.get("templateReference"),
            "queryCorrections": generated.get("queryCorrections") or [],
            "schemaSelection": generated.get("schemaSelection") or [],
            "customerRequirement": (
                payload.requirement_document.model_dump(mode="json", by_alias=True)
                if payload.requirement_document else {
                    "schemaVersion": "dashboard-requirement-v1",
                    "objective": payload.requirement,
                }
            ),
            "requestKey": payload.request_key,
        }, revision_reason="AI 工作台生成草稿")
        permissions = access_policy(ctx).permissions
        preview_path = _workspace_report_path("view", saved.id, ctx.workspace_id)
        designer_path = _workspace_report_path("designer", saved.id, ctx.workspace_id)
        actions = [
            BusinessActionLink(label="交互预览", href=preview_path, kind="primary", permission="dashboards.view"),
            BusinessActionLink(label="返回大屏列表", href="/dashboards", permission="dashboards.view"),
        ]
        if "dashboards.edit" in permissions:
            actions.insert(1, BusinessActionLink(label="打开完整设计器", href=designer_path,
                                                 permission="dashboards.edit"))
        view = DashboardDraftView(
            receiptId=receipt.id, reportId=saved.id, generationId=generated["generationId"],
            generationRunId=generated["runId"], invocationRunId=invocation_run_id,
            title=saved.reportName, version=saved.version,
            templateMode=generated["templateMode"], templateId=generated["templateId"],
            datasourceIds=source_ids, datasourceNames=[source.name for source in sources],
            queryIds=generated["queryIds"], widgetCount=len(generated["widgets"]),
            previewPath=preview_path, actions=actions,
            provenance={
                "source": "真实授权数据源与逐组件验证查询",
                "generationId": generated["generationId"], "generationRunId": generated["runId"],
                "queryIds": generated["queryIds"], "templateMode": generated["templateMode"],
                "sourceQueryIds": generated.get("sourceQueryIds") or [],
                "generationMode": generated.get("generationMode") or "model",
                "templateReference": generated.get("templateReference"),
                "published": False, "productionWriteback": False,
            }, reused=False,
        )
        receipt = ctx.db.get(BusinessActionReceipt, receipt.id)
        receipt.status, receipt.resource_type, receipt.resource_id = "completed", "dashboards", saved.id
        receipt.result_json, receipt.error_message = view.model_dump(mode="json"), ""
        ctx.db.commit()
        return view
    except AppError as exc:
        ctx.db.rollback()
        failed = ctx.db.get(BusinessActionReceipt, receipt.id)
        if failed is not None:
            failed.status, failed.error_message = "failed", exc.message[:3000]
            # Once generation starts, its durable claim cannot be reopened by
            # changing model arguments in the same conversational run.
            failed.result_json = {"retryable": False, "generationId": exc.extra.get("generation_id")}
            exc.extra["retryable"] = False
            ctx.db.commit()
        raise
    except Exception:
        ctx.db.rollback()
        failed = ctx.db.get(BusinessActionReceipt, receipt.id)
        if failed is not None:
            failed.status = "failed"
            failed.error_message = "业务动作执行失败；未登记为成功，请通过运行记录排查"
            failed.result_json = {"retryable": False}
            ctx.db.commit()
        raise
