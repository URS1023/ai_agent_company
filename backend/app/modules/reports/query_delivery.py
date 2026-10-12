"""Deliver an explicit conversion of verified query results without redesigning scope."""
from __future__ import annotations

from datetime import datetime
import json
import math
import re

from sqlalchemy import select

from app.errors import AppError, ValidationError
from app.ids import new_id
from app.models import Run, ToolCall
from app.modules.analytics.connections import source_version
from app.modules.analytics.models import QueryHistory
from app.modules.analytics.queries import execute_query
from app.modules.analytics.schemas import QueryInput
from app.modules.analytics.sources import get_source
from app.modules.reports.content import validate_content
from app.modules.reports.models import ReportGeneration
from app.modules.runtime import service as runtime


def conversion_requested(text: str) -> bool:
    """Only an explicit result conversion; extra metrics/design constraints keep the full designer."""
    return bool(re.fullmatch(
        r'\s*(?:请|麻烦|帮我|请帮我)?\s*(?:把|将)?\s*'
        r'(?:(?:刚才|上面|前面|上述|这些|这个|查询到的|查询|现有)?(?:的)?(?:查询结果|结果|数据))?\s*'
        r'(?:做成|制作成|生成|制作|创建|转成|转换成)\s*(?:一个|一张|一份)?\s*(?:数字)?(?:大屏|看板)\s*[。！!]?\s*',
        str(text or ''),
    ))


def verified_queries(ctx, payload, invocation_run_id: str | None) -> list[QueryHistory]:
    """取出"可直接转成大屏"的上一次查询结果，返回可用的 ``QueryHistory`` 列表（否则空列表）。

    仅在全部条件满足时启用：调用方给了运行 ID、不是 manual 固定模板、没有结构化需求文档
    （有明确需求就该走完整设计流程），且该运行的用户请求确实是"把刚才的结果做成大屏"
    （``conversion_requested`` 严格整句匹配）。随后回查同一会话最近一次成功的
    ``query_business_data`` 工具调用，逐个校验查询记录属于本人、当前工作区、状态成功、
    且数据源配置版本未变化——版本不一致抛 ``ValidationError`` 要求重新查询，
    宁可失败也不用旧结果顶替。

    上限 8 条查询；任何一条不满足就整体返回空列表，**绝不退回更早的无关成功记录**。
    """
    if not invocation_run_id or payload.template_mode == 'manual' or payload.requirement_document:
        return []
    run = ctx.db.get(Run, invocation_run_id)
    if (not run or run.workspace_id != ctx.workspace_id or run.started_by_user_id != ctx.user_id
            or not conversion_requested((run.context_snapshot or {}).get('user_request'))):
        return []
    selected = set(payload.datasource_ids or (run.context_snapshot or {}).get('datasource_ids') or [])
    calls = ctx.db.scalars(select(ToolCall).join(Run, Run.id == ToolCall.run_id).where(
        Run.workspace_id == ctx.workspace_id, Run.started_by_user_id == ctx.user_id,
        Run.conversation_id == run.conversation_id, Run.created_at <= run.created_at,
        ToolCall.workspace_id == ctx.workspace_id, ToolCall.tool_name == 'query_business_data',
    ).order_by(ToolCall.started_at.desc(), ToolCall.id.desc()).limit(1)).all()
    for call in calls:
        if call.status not in {'completed', 'success'}:
            return []
        data = (call.result_json or {}).get('data') or {}
        if not isinstance(data, dict) or data.get('success') is False:
            return []
        items = data.get('results') if isinstance(data.get('results'), list) else [data]
        ids = list(dict.fromkeys(item.get('queryId') for item in items if isinstance(item, dict) and item.get('queryId')))
        if not ids or len(ids) > 8:
            return []
        rows = []
        for query_id in ids:
            row = ctx.db.scalar(select(QueryHistory).where(
                QueryHistory.id == query_id, QueryHistory.workspace_id == ctx.workspace_id,
                QueryHistory.owner_user_id == ctx.user_id, QueryHistory.status == 'success',
            ))
            if row is None or not row.generated_sql.strip() or (selected and row.datasource_id not in selected):
                rows = []
                break
            source = get_source(ctx, row.datasource_id, active=True)
            if source_version(source) != row.datasource_version:
                raise ValidationError('原查询的数据来源配置已变化，请先重新查询再生成大屏')
            rows.append(row)
        if rows:
            return rows
        # Never substitute an older, unrelated success for the latest receipt.
        return []
    return []


def _text(identity, text, x, y, width, height, *, size=20):
    """构造一个纯文字组件（JText），用于结果转换时的说明与标题，不含任何查询绑定。"""
    return {'id': identity, 'component': 'JText', 'componentName': text[:100],
            'x': x, 'y': y, 'w': width, 'h': height,
            'config': {'option': {'body': {'text': text, 'fontSize': size, 'color': '#D9EEFF'}}}}


def generate(ctx, payload, queries: list[QueryHistory]) -> dict:
    """Revalidate the exact queries and build editable tables; no inferred totals or fields.

    结果转换路径：把已成功的查询结果原样变成可编辑组件（表格/文字），**重新执行**这些查询
    以确认仍然可用，然后直接落库生成记录。不推断合计、不新增字段、不重新设计范围——
    与 AI 生成的区别在于这里没有任何模型参与选指标。
    """
    from app.modules.reports.generation import _event
    record = ReportGeneration(id=new_id(), workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id,
        kind='screen', status='running', request_json={**payload.model_dump(mode='json'),
            'verified_query_ids': [q.id for q in queries]}, events=[], result_json={})
    ctx.db.add(record)
    ctx.db.flush()
    run = runtime.create_run(ctx.db, workspace_id=ctx.workspace_id, conversation_id='', user_id=ctx.user_id,
        context_snapshot={'entry': 'reports', 'generation_id': record.id, 'kind': 'screen'}, model_profile='none')
    run.status = 'running'
    record.usage = {'runId': run.id, 'generationMode': 'verified_query_layout', 'calls': []}
    ctx.db.commit()
    try:
        _event(ctx, record, 'query', '正在沿用已确认的查询范围核验数据', 20)
        results = []
        for old in queries:
            original = old.result_json or {}
            result = execute_query(ctx, QueryInput(datasourceId=old.datasource_id, mode='sql',
                query=old.generated_sql, parameters=original.get('parameters') or {}, maxRows=1000),
                permission='dashboards.execute')
            if not result.success:
                raise ValidationError('原查询未通过本次数据验证，未保存大屏')
            results.append(result)
        width, height = payload.width, payload.height
        # Small canvases keep one column; every result retains all returned fields.
        columns = 2 if len(results) > 1 and width >= 1000 else 1
        rows = math.ceil(len(results) / columns)
        cell_width = (width - 48 - (columns - 1) * 20) / columns
        cell_height = (height - 156 - (rows - 1) * 20) / rows
        if cell_height < 150:
            raise ValidationError('当前画布不足以清晰显示全部查询结果，请增大画布尺寸')
        title = payload.title.strip() or '查询结果数字大屏'
        widgets = [_text('query-title', title, 24, 18, width - 48, 48, size=30),
                   _text('query-scope', '展示原查询范围内返回的明细；单位按原始值显示，不做跨单位合计。',
                         24, 72, width - 48, 44, size=16)]
        for index, (old, result) in enumerate(zip(queries, results)):
            x = 24 + (index % columns) * (cell_width + 20)
            y = 132 + (index // columns) * (cell_height + 20)
            label = old.query_text.strip()
            if old.query_mode == 'sql' or len(label) > 100:
                label = f'查询结果 {index + 1}'
            widgets.append(_text(f'query-label-{index}', label, x, y, cell_width, 38, size=20))
            headers = [{'key': c.columnName, 'label': c.comment or c.columnName} for c in result.columns]
            parameters = (old.result_json or {}).get('parameters') or {}
            widget = {'id': f'query-data-{index}', 'component': 'JScrollBoard', 'componentName': label,
                'x': x, 'y': y + 42, 'w': cell_width, 'h': cell_height - 42,
                'query': {'datasourceId': old.datasource_id, 'sql': result.generatedSql, 'parameters': parameters},
                'datasourceId': old.datasource_id, 'generatedSql': result.generatedSql,
                'data': result.data, 'dataStatus': 'verified',
                'config': {'dataType': 3, 'datasourceId': old.datasource_id, 'sql': result.generatedSql,
                    'parameters': parameters, 'queryId': result.queryId, 'dataStatus': 'verified',
                    'verifiedAt': datetime.utcnow().isoformat(), 'chartDataParsed': result.data,
                    'chartData': json.dumps(result.data, ensure_ascii=False),
                    'option': {'header': headers, 'rowNum': max(2, min(12, int((cell_height - 84) / 32))),
                        'headerFontSize': 24, 'bodyFontSize': 22,
                        'headerBGC': '#124C70', 'oddRowBGC': '#0C263F', 'evenRowBGC': '#12334D',
                        'headerFontColor': '#E0F4FF', 'fontColor': '#D9EEFF'}},
            }
            widgets.append(widget)
        _event(ctx, record, 'design', '正在生成保留全部查询字段的可编辑布局', 80)
        content = validate_content({'canvas': {'width': width, 'height': height, 'backgroundColor': '#061225'},
                                    'widgets': widgets})
        result = {'success': True, 'generationId': record.id, 'runId': run.id, 'title': title, **content,
            'templateMode': 'none', 'templateId': None, 'templateAdapted': False,
            'datasourceIds': list(dict.fromkeys(q.datasource_id for q in queries)),
            'queryIds': [r.queryId for r in results], 'generationMode': 'verified_query_layout',
            'queryCorrections': [], 'sourceQueryIds': [q.id for q in queries],
            'semanticReview': {'method': 'exact_query_and_field_preservation', 'aligned': True,
                'scope': 'explicit_conversion_only', 'inferredMetrics': False},
            'schemaSelection': []}
        record.status, record.result_json = 'completed', result
        record.usage = {**record.usage, 'queryIds': result['queryIds']}
        runtime.finalize_run(ctx.db, run, status='completed', model_usage=record.usage)
        _event(ctx, record, 'completed', '查询范围和字段已核对，可以保存大屏', 100)
        return result
    except Exception as exc:
        if not ctx.db.is_active:
            ctx.db.rollback()
        message = exc.message if isinstance(exc, AppError) else '查询结果大屏生成未完成'
        record.status, record.error_message = 'failed', message
        runtime.finalize_run(ctx.db, run, status='failed', error_code=getattr(exc, 'code', 'generation_error'),
                             error_summary=message)
        ctx.db.commit()
        raise
