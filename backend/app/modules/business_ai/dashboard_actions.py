"""Conversation-scoped dashboard discovery, context and reversible theme edits."""
from __future__ import annotations

import json
import re
from copy import deepcopy

from sqlalchemy import select

from app.errors import NotFoundError, ValidationError
from app.models import Message, Run, ToolCall
from app.modules.reports import service, templates
from app.modules.reports.schemas import ReportInput

DARK_TECH = {
    "id": "dark-tech", "background": "#061225", "backgroundRaised": "#102840",
    "panel": "#102840E8", "panelStrong": "#102840F5", "primary": "#26D9FF",
    "secondary": "#477BFF", "accent": "#7FEFFF", "positive": "#35E4A4",
    "danger": "#FF627D", "grid": "#26D9FF44", "text": "#ECF7FF",
    "muted": "#92B2CF", "surfaceLift": "#174465", "surfaceRadius": 8,
    "palette": ["#26D9FF", "#477BFF", "#7FEFFF", "#35E4A4", "#A879FF", "#FF627D", "#FFD05B", "#58A6FF"],
}


def invocation(ctx, tool_context):
    """解析本次工具调用的对话 Run，并校验它属于当前用户与工作区。

    这是工具入口的身份边界：run_id 来自工具上下文，必须重新核对归属，否则一个
    伪造的 run_id 就能读取其他会话的大屏或上下文。
    抛出：不存在或跨用户/跨工作区时抛 ``NotFoundError``（不区分，避免探测）。
    """
    row = ctx.db.get(Run, tool_context.get("run_id"))
    if not row or row.workspace_id != ctx.workspace_id or row.started_by_user_id != ctx.user_id:
        raise NotFoundError("没有找到本次对话任务")
    return row


def recent_calls(ctx, run, names):
    """取同一会话中、本次运行之前成功调用过的指定工具（最多 12 条，按时间倒序）。

    只认 status 为 completed/success 的调用记录，且限定同一会话与当前用户：
    助手说的“已经做好了”不算证据，只有工具真实执行结果才算。
    """
    return ctx.db.scalars(select(ToolCall).join(Run, Run.id == ToolCall.run_id).where(
        Run.workspace_id == ctx.workspace_id, Run.started_by_user_id == ctx.user_id,
        Run.conversation_id == run.conversation_id, Run.created_at <= run.created_at,
        ToolCall.workspace_id == ctx.workspace_id,
        ToolCall.tool_name.in_(names), ToolCall.status.in_(["completed", "success"]),
    ).order_by(ToolCall.started_at.desc()).limit(12)).all()



def queried_datasource_ids(ctx, run):
    """仅继承本轮成功查询凭据中的唯一数据源，不以候选目录或模型自述定范围。"""
    from app.modules.analytics.models import QueryHistory
    if not run or run.workspace_id != ctx.workspace_id or run.started_by_user_id != ctx.user_id:
        return []
    calls = ctx.db.scalars(select(ToolCall).where(
        ToolCall.workspace_id == ctx.workspace_id, ToolCall.run_id == run.id,
        ToolCall.tool_name.in_(["query_business_data", "query_inventory_data"]),
    ).order_by(ToolCall.started_at.desc(), ToolCall.id.desc()).limit(32)).all()
    if not calls or len(calls) == 32 or calls[0].status not in {"completed", "success"}:
        return []
    sources = set()
    for call in calls:
        if call.status not in {"completed", "success"}:
            continue
        data = (call.result_json or {}).get("data") or {}
        if not isinstance(data, dict) or data.get("success") is False:
            continue
        items = data.get("results") if isinstance(data.get("results"), list) else [data]
        for item in items:
            if not isinstance(item, dict) or not item.get("queryId"):
                continue
            query = ctx.db.scalar(select(QueryHistory).where(
                QueryHistory.id == item["queryId"], QueryHistory.workspace_id == ctx.workspace_id,
                QueryHistory.owner_user_id == ctx.user_id, QueryHistory.status == "success",
            ))
            if query is not None:
                sources.add(query.datasource_id)
    # 多个实际来源仍需明确选择；后续服务会再次核对源当前是否启用和可访问。
    return list(sources) if len(sources) == 1 else []


def dashboard_failure_metadata(error):
    """区分可补齐输入与已开始执行的失败，避免把局部错误解释为整轮任务结束。"""
    extra = error.extra
    metadata = {key: extra[key] for key in (
        "retryable", "auto_retry", "requires_user_input", "missing_fields", "recovery",
        "execution_started", "receipt_id", "receipt_status", "generationId",
        "candidates", "invalid_datasource_ids", "available_datasources",
    ) if key in extra}
    if extra.get("recovery") == "select_datasource":
        metadata["guidance"] = (
            "大屏尚未开始生成，缺少明确数据源。仅可沿用本轮真实成功查询的唯一来源，"
            "或用户明确选择的来源补齐 datasource_ids；仍有多个范围时请用户选择。"
            "不要反复提交相同的空参数；其他已具备输入的交付可继续。"
        )
    elif extra.get("receipt_status") == "running":
        metadata["guidance"] = "同一大屏正在执行，请保留当前任务并查看运行记录；不要更换参数或请求键重复创建。"
    elif extra.get("retryable") is False:
        metadata["guidance"] = (
            "本次大屏未交付，请保留具体失败原因及已完成产物。已开始的数据库或生成步骤不得"
            "通过改标题、换模板或换请求键自动重跑；可继续不依赖该失败步骤的文档、表格等任务。"
            "若为数据库超时或断连，停止后续查询，待核对残留查询并取得继续授权。"
        )
    else:
        metadata["guidance"] = "按返回的具体错误修正缺失参数或权限；不得伪造大屏成功，也不要把局部失败当作其他交付已完成。"
    return metadata


def resolve_report(ctx, run, report_id=None, *, permission="dashboards.view"):
    """定位要操作的大屏：显式 report_id 优先，否则回退到本会话最近一次真实创建/读取结果。

    只有工具返回里携带 reportId 的记录才算“会话中的当前大屏”；权限由
    service.get_report 校验（更新场景需要 dashboards.edit）。
    抛出：会话里没有可定位的大屏时抛 ``ValidationError``（携带 requires_user_input，
    让聊天层向用户澄清要改哪一个大屏），而不是擅自挑一个。
    """
    if report_id:
        return service.get_report(ctx, report_id, permission)
    for call in recent_calls(ctx, run, ["create_business_dashboard", "update_business_dashboard", "get_business_dashboard"]):
        data = (call.result_json or {}).get("data") or {}
        if data.get("reportId"):
            return service.get_report(ctx, data["reportId"], permission)
    raise ValidationError("这段对话中还没有可修改的大屏。您想修改哪一个大屏？", requires_user_input=True)


def template_candidates(ctx, keyword="", limit=8):
    """按关键词挑选可用大屏模板；识别“深色/暗色/dark”并据此过滤与加权。

    已知边界（正则启发式）：深浅色与“科技/驾驶舱/蓝”等偏好都是按正则匹配模板名称、
    描述、标签与画布底色亮度得出的，因此同义说法（如“夜光”“冷色”）不会被识别，
    只会退化为普通关键词匹配，不影响正确性但排序可能不如预期。
    返回精简候选列表（id/名称/描述/预览/底色/组件数），供模型或用户选择；
    关键词非空时只保留得分大于 0 的候选，避免把完全不相关模板推给用户。
    """
    items = [item for item in templates.list_templates(ctx) if item.get("enabled", True)]
    terms = [term for term in re.split(r"[\s，、]+", keyword.strip()) if term]
    dark_requested = bool(re.search(r"深色|暗色|dark", keyword, re.I))
    def dark(item):
        """判断模板是否为深色：优先用画布底色的相对亮度，无法解析时回落到文字描述。"""
        canvas = (item.get("content") or {}).get("canvas") or {}
        color = str(canvas.get("backgroundColor") or (canvas.get("theme") or {}).get("background") or "")
        if re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            rgb = [int(color[i:i+2], 16) for i in (1, 3, 5)]
            return .2126 * rgb[0] + .7152 * rgb[1] + .0722 * rgb[2] < 100
        return bool(re.search(r"深色|暗色|dark", item.get("description", "") + item["name"], re.I))
    if dark_requested:
        items = [item for item in items if dark(item)]
    def score(item):
        """模板相关性打分：名称/描述/标签命中关键词 + 深色科技风的额外加权。

        纯启发式，仅用于排序候选；同分时按名称排序，保证结果稳定可复现。
        """
        haystack = " ".join([item["name"], item.get("description", ""), *item.get("tags", [])])
        points = sum(4 for term in terms if term.lower() in haystack.lower())
        if re.search(r"深色|科技|暗色|dark", keyword, re.I):
            canvas = (item.get("content") or {}).get("canvas") or {}
            color = str(canvas.get("backgroundColor") or "")
            if re.fullmatch(r"#[0-9a-fA-F]{6}", color):
                rgb = [int(color[i:i+2], 16) for i in (1, 3, 5)]
                if max(rgb) < 90:
                    points += 2
                if rgb[2] > rgb[0]:
                    points += 2
            if re.search(r"科技|驾驶舱|数据分析|蓝", haystack):
                points += 3
        return points
    ranked = sorted(items, key=lambda item: (-score(item), item["name"]))
    if keyword.strip():
        ranked = [item for item in ranked if score(item)]
    return [{"id": item["id"], "name": item["name"], "description": item.get("description", ""),
             "preview": item.get("preview", ""), "backgroundColor": (item.get("content") or {}).get("canvas", {}).get("backgroundColor"),
             "componentCount": item.get("componentCount", 0)} for item in ranked[:max(1, min(limit, 20))]]


def grounded_requirement(ctx, run, original):
    """使用当前运行或明确追问的授权会话证据补齐大屏依据，不让模型替换用户范围。"""

    # 复合任务沿用本轮真实查询的结果摘要与范围；明确追问才补充既有大屏和历史查询。
    # 证据来自 ToolCall 而非助手自述，拼接结果保存在本轮快照以供复用。
    context = (run.context_snapshot or {}).get("business_context")
    if isinstance(context, str) and context:
        return context
    # 同一句复合任务已完成的查询属于当前范围，无需用户再说“刚才”；历史范围仍需明确指代。
    follow_up = bool(re.search(r"这个|该大屏|这份|当前|刚才|前面|上述|重新|继续|做成", original))
    calls = recent_calls(ctx, run, ["query_business_data", "query_inventory_data"])
    current_calls = [call for call in calls if call.run_id == run.id]
    if not follow_up and not current_calls:
        return original
    if not follow_up:
        calls = current_calls
    parts = []
    try:
        report = resolve_report(ctx, run) if follow_up else None
    except (NotFoundError, ValidationError):
        report = None
    if report:
        prior = (report.lineage or {}).get("customerRequirement") or {}
        objective = prior.get("objective") if isinstance(prior, dict) else str(prior)
        parts.append("已有大屏：" + report.name)
        if objective:
            parts.append("已确认的业务范围：" + str(objective)[:7000])
        if re.search(r"这个|该大屏|这份|当前|刚才|前面|上述|重新|继续", original):
            run.context_snapshot = {**(run.context_snapshot or {}), "dashboard_business_title": report.name}
    selected = set((run.context_snapshot or {}).get("datasource_ids") or [])
    for call in calls:
        source = (call.arguments_json or {}).get("datasource_id")
        if selected and source and source not in selected:
            continue
        data = (call.result_json or {}).get("data") or {}
        # Store the tool's evidence and scope, not an assistant's success claim.
        def compact(item):
            """只保留查询工具返回的**可复核字段**（查询标识、SQL、表、范围、样本列）。

            刻意不做任何改写或补全：证据文本直接来自工具结果，助手声称的结论不进入
            业务上下文，避免后续生成把“助手说查到了”当成事实依据。
            """
            evidence = {key: item[key] for key in (
                "queryId", "datasourceId", "truncated", "totalCount", "query", "generatedSql", "parameters",
                "tableName", "topic", "summary", "scope", "candidateTables", "analysis", "statistics", "retrievalEvidence",
            ) if key in item}
            if item.get("columns"):
                evidence["columns"] = [{key: column[key] for key in ("columnName", "dataType", "comment") if key in column}
                                       for column in item["columns"] if isinstance(column, dict)]
            return evidence
        evidence = compact(data)
        if isinstance(data.get("results"), list):
            evidence["results"] = [compact(item) for item in data["results"] if isinstance(item, dict)]
        arguments = call.arguments_json or {}
        question = arguments.get("query") or (
            "库存业务结构化查询：" + json.dumps(arguments, ensure_ascii=False, default=str)
            if call.tool_name == "query_inventory_data" else "")
        parts.append("前一步业务提问：" + str(question)[:2000])
        parts.append("前一步已验证的查询依据（样本不能当全部合计）：" + json.dumps(evidence, ensure_ascii=False, default=str)[:10000])
        break
    if not parts:
        previous = ctx.db.scalars(select(Message.content).where(
            Message.workspace_id == ctx.workspace_id, Message.conversation_id == run.conversation_id,
            Message.role == "user", Message.created_at <= run.created_at,
        ).order_by(Message.sequence.desc()).limit(12)).all()
        for text in previous:
            if text != original and not re.search(r"大屏|模板|主题|ppt|重新生成|重试|继续生成", text, re.I):
                parts.append("前一步业务需求：" + text[:3000])
                break
    value = "\n\n".join([*parts, "本次用户要求（本次明确修改优先）：" + original])[:39000] if parts else original
    run.context_snapshot = {**(run.context_snapshot or {}), "business_context": value}
    ctx.db.commit()
    return value


def apply_theme(content, theme=DARK_TECH):
    """把大屏内容原地替换为深色科技风配色（只改视觉属性，不动数据绑定）。

    做法：先用旧主题的颜色值建立“颜色映射”，再对组件的 style/config 递归替换；
    明确跳过 data/dataset/query/sql/parameters/binding 等键，保证指标、查询与绑定
    完全保留。这是可逆的纯视觉编辑，因此不需要重新生成或重新取数。
    返回新的内容字典（deepcopy，不修改入参）。
    """
    content = deepcopy(content)
    canvas = content.setdefault("canvas", {})
    old = canvas.get("theme") or {}
    colors = {str(old[k]).upper(): str(v) for k, v in theme.items()
              if isinstance(v, str) and v.startswith("#") and old.get(k)}
    colors.update({str(a).upper(): b for a, b in zip(old.get("palette") or [], theme["palette"])})
    visual_keys = {"color", "background", "backgroundColor", "borderColor", "shadowColor", "glow", "fill", "stroke", "secondaryColor", "backgroundLayers"}
    def walk(value):
        """递归替换视觉颜色；遇到数据/查询/绑定相关键立即返回（不进入其子树）。"""
        if not isinstance(value, dict):
            return
        for key, child in value.items():
            if key in {"data", "dataset", "chartData", "chartDataParsed", "query", "sql", "parameters", "binding", "bindings"}:
                continue
            if key in visual_keys and isinstance(child, str):
                value[key] = re.sub(r"#[0-9a-fA-F]{8}|#[0-9a-fA-F]{6}", lambda m: colors.get(m[0].upper(), m[0]), child)
            elif key == "color" and isinstance(child, list):
                value[key] = [colors.get(str(c).upper(), c) for c in child]
            elif isinstance(child, dict):
                walk(child)
            elif isinstance(child, list):
                for item in child:
                    walk(item)
    for widget in content.get("widgets", []):
        walk(widget.get("style", {}))
        walk(widget.get("config", {}))
    canvas.update(themeId=theme["id"], theme=deepcopy(theme), backgroundColor=theme["background"],
                  backgroundImage="", styleLocked=False)
    return content


def report_result(ctx, report, *, message=""):
    """把大屏实体转换成聊天结果卡片所需的结构（含预览/设计器链接与来源信息）。

    ``metrics`` 只列出真正绑定了查询的组件，因此可用来判断“这张大屏有没有真实数据”；
    ``provenance`` 直接透传 report.lineage（生成 Run、查询标识、模板与发布状态），
    便于用户核对数据来源。权限：数据源名称通过 get_source(provider="any") 逐个校验，
    已失去访问权的数据源会直接报错而不是静默跳过。只读，不修改报表。
    """
    from app.modules.analytics.sources import get_source
    sources = [get_source(ctx, source_id, provider="any") for source_id in report.datasourceIds]
    return {"resultType": "dashboard", "reportId": report.id, "title": report.reportName,
            "version": report.version, "status": report.status, "templateId": report.templateId,
            "templateMode": report.templateMode, "datasourceIds": report.datasourceIds,
            "datasourceNames": [source.name for source in sources],
            "queryIds": list((report.lineage or {}).get("queryIds") or []), "provenance": report.lineage,
            "metrics": [{"name": widget.get("metricName") or widget.get("componentName") or widget.get("title"),
                         "unit": widget.get("unit"), "status": widget.get("dataStatus")}
                        for widget in json.loads(report.content).get("widgets", []) if widget.get("query")],
            "widgetCount": len(json.loads(report.content).get("widgets", [])), "refreshSeconds": report.refreshSeconds,
            "previewPath": f"/report/view/{report.id}?workspace_id={ctx.workspace_id}",
            "actions": [{"label": "预览大屏", "href": f"/report/view/{report.id}?workspace_id={ctx.workspace_id}", "kind": "primary"},
                        {"label": "进入设计器", "href": f"/report/designer/{report.id}?workspace_id={ctx.workspace_id}", "kind": "secondary"}],
            "message": message}


def execute(ctx, name, args, tool_context):
    """聊天工具入口：列表模板 / 读取大屏 / 切换深色科技风（仅这三类，其余名称不处理）。

    权限与安全：先解析并校验对话 Run 归属，再按动作选择权限（改版需要
    dashboards.edit，读取只需 dashboards.view）；主题目前只支持 dark-tech，
    其它主题名直接拒绝并建议先看模板，避免“看起来改了其实没改”。
    幂等：同一 Run 内重复调用会命中 context_snapshot 里保存的结果，不重复保存版本。
    副作用：切换主题会保存一个新的草稿版本并写入 Run 上下文；读取动作无副作用。
    """
    if name == "list_dashboard_templates":
        return {"templates": template_candidates(ctx, str(args.get("keyword") or ""), int(args.get("limit") or 8))}
    run = invocation(ctx, tool_context)
    report = resolve_report(ctx, run, args.get("report_id"), permission="dashboards.edit" if name == "update_business_dashboard" else "dashboards.view")
    if name == "get_business_dashboard":
        return report_result(ctx, service.report_view(report))
    if args.get("theme", "dark-tech") != "dark-tech":
        raise ValidationError("目前可直接调整为深色科技风；其他主题可以先查看模板。")
    saved_receipt = (run.context_snapshot or {}).get("dashboard_theme_result")
    if saved_receipt and saved_receipt.get("reportId") == report.id:
        return saved_receipt
    edited = apply_theme(report.content)
    saved = service.save_report(ctx, ReportInput(
        reportName=report.name, description=report.description, datasourceIds=report.datasource_ids,
        content=edited, version=report.version, templateMode=report.template_mode,
        templateId=report.template_id, editTemplateLayout=True,
        backgroundColor=DARK_TECH["background"], backgroundImage="", thumbnail="",
        refreshSeconds=report.refresh_seconds, status="draft",
    ), report.id if report.status == "draft" else None,
        lineage={**(report.lineage or {}), "themeEditRunId": run.id, "themeId": "dark-tech"},
        revision_reason="对话调整为深色科技风，保留原有指标和数据绑定")
    result = report_result(ctx, saved, message="已调整为深色科技风，原有指标、布局和数据绑定已保留。")
    run.context_snapshot = {**(run.context_snapshot or {}), "dashboard_theme_result": result}
    ctx.db.commit()
    return result
