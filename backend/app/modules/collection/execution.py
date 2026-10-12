"""Shared bounded HTTP collection with batch outcomes and persisted provenance."""
from __future__ import annotations

import json
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from threading import BoundedSemaphore

from app.common.authorization import require_permission
from app.common.owned_records import owned_record, visible_records
from app.errors import AppError, ConflictError, NotFoundError, ToolExecutionError, ValidationError
from app.models import DataSource
from app.modules.analytics.models import QueryHistory
from app.modules.analytics.queries import history_view
from app.modules.analytics.schemas import ColumnView
from app.modules.analytics.statistics import describe
from app.modules.collection.extraction import MAX_RESULT_BYTES, extract_records
from app.modules.collection.schemas import CollectionResult, CollectInput, ExtractionPreview, RequestOutcome
from app.modules.collection.sources import credentials_for, http_source, source_config
from app.modules.collection.transport import request_json
from app.modules.collection.parameters import bind_parameters, MAX_DYNAMIC_BODY_BYTES
from app.modules.collection.transport import MAX_BODY_BYTES
from app.modules.runtime import service as runtime
from app.modules.system.service_helpers import commit_change

_COLLECTION_SLOTS = BoundedSemaphore(4)


class CollectionBusy(AppError):
    """采集槽位已满（HTTP 429）：本次**没有发出任何请求**，可稍后重试。"""

    status_code = 429
    code = "collection_busy"


def columns_for(rows: list[dict], fields: list) -> list[ColumnView]:
    """Infer only actual JSON types; identifiers that look numeric stay strings."""
    names = list(dict.fromkeys([field.name for field in fields] + [name for row in rows for name in row]))
    if len(names) > 200:
        raise ValidationError("合并结果超过 200 个字段，请明确选择输出字段")
    declared = {field.name: field for field in fields}
    columns = []
    for name in names:
        field = declared.get(name)
        values = [row.get(name) for row in rows if row.get(name) is not None]
        types = {"boolean" if isinstance(value, bool) else "integer" if isinstance(value, int)
                 else "decimal" if isinstance(value, float) else "json" if isinstance(value, (dict, list))
                 else "string" for value in values}
        data_type = next(iter(types)) if len(types) == 1 else "json" if len(types) > 1 else "unknown"
        if field and field.type != "auto":
            data_type = "json" if field.multiple else field.type
        columns.append(ColumnView(columnName=name, dataType=data_type, comment=field.comment if field else "",
                                  nullable=not field or not field.required))
    return columns


def extraction_preview(ctx, payload: ExtractionPreview) -> dict:
    """Parse user-provided sample data without contacting any external system."""
    require_permission(ctx, "datasources.view")
    if len({field.name for field in payload.fields}) != len(payload.fields):
        raise ValidationError("输出字段名不能重复")
    if len(json.dumps(payload.sample, ensure_ascii=False).encode("utf-8")) > 4 * 1024 * 1024:
        raise ValidationError("预览样本不能超过 4 MB")
    if not _COLLECTION_SLOTS.acquire(blocking=False):
        raise CollectionBusy("采集服务繁忙，请稍后重试；此次没有发送请求")
    try:
        rows, truncated = extract_records(payload.sample, payload, limit=payload.max_rows, deadline=time.monotonic() + 5)
        return {"data": rows, "columns": [column.model_dump() for column in columns_for(rows, payload.fields)],
                "totalCount": len(rows), "truncated": truncated}
    finally:
        _COLLECTION_SLOTS.release()


def collect(ctx, source_id: str, payload: CollectInput | None = None, *, automatic: bool = False,
            permission: str = "datasources.execute", deadline: float | None = None,
            parameters: list[dict] | None = None, max_rows: int | None = None,
            query_text: str = "", request_override: dict | None = None,
            request_retries: int = 0, retry_interval_ms: int = 500) -> CollectionResult:
    """Run saved requests, never arbitrary client URLs or unapproved POST retries.

    采集的唯一入口：只执行**已保存**的请求配置，不接受客户端传来的地址；手工采集
    必须提交与当前配置一致的 version，POST 还需显式 confirmed；自动调用（报表/工作流）
    要求数据源已授权 allow_automatic，否则拒绝——确认一次 POST 不等于授予长期自动权限。
    并发配额：进程内 BoundedSemaphore（4 个槽位）在**发请求之前**获取，占满即抛
    ``CollectionBusy``（429），因此排队不会消耗上游配额。
    HTTP 写请求（POST）不允许自动重试：确认写入不等于请求幂等。

    Args:
        ctx: Authenticated workspace context.
        source_id: Authorized HTTP source ID.
        payload: Confirmed version for manual collection.
        automatic: Whether a saved report/workflow is invoking the source.
        permission: Entry-point action checked in addition to data-source access.
        deadline: Optional enclosing workflow deadline using monotonic time.
        parameters: Server-validated per-sample values for explicit saved placeholders.
        max_rows: Optional per-invocation cap that may only reduce the saved source limit.
        query_text: Optional audited business question for AI-triggered collection.

    抛出：权限/启用状态不符、版本过期（``ConflictError``）、缺自动化授权、未确认 POST、
    槽位占满等都会在发请求前抛出；真正执行后的逐条失败体现在结果的 outcomes 里。
    副作用：发出一条或多条真实 HTTP 请求（只读语义由数据源约定保证，系统无法强制）。
    """
    require_permission(ctx, permission)
    source = http_source(ctx, source_id, "datasources.view" if automatic else "datasources.execute", active=True)
    config = source_config(source)
    if request_override:
        # Workflow HTTP nodes may override one request while retaining the
        # source's credentials, private-network decision, and automatic-use
        # approval. The override is never persisted as a new data source.
        config = config.model_copy(update=dict(request_override))
    if max_rows is not None:
        if max_rows < 1:
            raise ValidationError("返回行数必须大于 0")
        config = config.model_copy(update={"max_rows": min(config.max_rows, max_rows)})
    if automatic:
        if not config.allow_automatic:
            raise ValidationError("此数据源尚未确认允许自动只读采集，请先由配置人员授权")
    else:
        # 手工采集必须提交当前版本：配置在编辑期间被改动时拒绝执行，避免用旧意图
        # 打到新目标上。
        if payload is None or payload.version != config.version:
            raise ConflictError("HTTP 配置版本已变更，请重新加载并确认后采集")
        if config.method == "POST" and not payload.confirmed:
            raise ValidationError("POST 可能产生业务副作用，请确认已保存的请求体后再执行")
    config = bind_parameters(config, parameters)
    if request_retries and config.method not in {"GET", "HEAD"}:
        raise ValidationError("HTTP 写请求不支持自动重试；确认写入不等于请求幂等")
    credentials = credentials_for(ctx, source)
    if not _COLLECTION_SLOTS.acquire(blocking=False):
        raise CollectionBusy("采集服务繁忙，请稍后重试；此次没有发送请求")
    try:
        image_redactions = {f"quality_image_{index}": item["imageBase64"] for index, item in enumerate(parameters or []) if item.get("imageBase64")}
        return _execute(ctx, source, config, credentials, deadline=deadline, response_redactions=image_redactions,
                        body_limit=MAX_DYNAMIC_BODY_BYTES if parameters is not None else MAX_BODY_BYTES,
                        query_text=query_text, request_retries=request_retries,
                        retry_interval_ms=retry_interval_ms)
    finally:
        _COLLECTION_SLOTS.release()


def _execute(ctx, source, config, credentials, *, deadline: float | None = None, body_limit: int = MAX_BODY_BYTES,
             response_redactions: dict[str, str] | None = None, query_text: str = "",
             request_retries: int = 0, retry_interval_ms: int = 500) -> CollectionResult:
    """按已保存配置逐条发送请求并汇总结果（内部实现，调用方已完成授权检查）。

    批次上限：整批时间 = min(本轮 deadline, batch_timeout_seconds)；合并行数不超过
    config.max_rows、合并结果不超过 MAX_RESULT_BYTES、字段总数不超过 200——
    达到任一上限后**不再发送剩余请求**，并把未发送项标为 skipped（不是失败，也不
    静默丢弃）。分页请求由同一地址按查询参数递增生成，参数名固定。

    rowOrigins 与 data 一一对应，记录每行来自第几个请求体，导出与追溯据此还原来源。
    整体状态：全部成功且未截断为 success，有失败/未执行/截断为 partial，一条都没成功
    为 failed；partial/failed 时 success=False 且 errorMessage 明确说明只含成功请求的结果。

    副作用：创建 Run/Step、写 QueryHistory（query_mode=http）、提交审计；
    出错时回滚未提交结果并把失败写入运行记录，再抛 ``ToolExecutionError``。
    """
    if request_retries and config.method not in {"GET", "HEAD"}:
        raise ValidationError("HTTP 写请求不支持自动重试；确认写入不等于请求幂等")
    started = time.monotonic()
    deadline = min(started + config.batch_timeout_seconds, deadline) if deadline is not None else started + config.batch_timeout_seconds
    requests = [(config, body) for body in config.bodies]
    if config.pagination:
        paging = config.pagination
        parts = urlsplit(config.url)
        query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key != paging.parameter]
        requests = [(config.model_copy(update={"url": urlunsplit(parts._replace(query=urlencode([
            *query, (paging.parameter, str(paging.start + index * paging.step)),
        ])))}), None) for index in range(paging.pages)]
    snapshot = {"entry": "collection", "datasource_id": source.id, "datasource_version": config.version,
                "method": config.method, "request_count": len(requests), "response_format": config.response_format}
    run = runtime.create_run(ctx.db, workspace_id=ctx.workspace_id, conversation_id="", user_id=ctx.user_id,
                             context_snapshot=snapshot, model_profile="none")
    run.status = "running"
    history = QueryHistory(workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id, datasource_id=source.id,
                           run_id=run.id, query_text=query_text or source.name,
                           query_mode="http", datasource_version=config.version)
    ctx.db.add(history)
    ctx.db.flush()
    run.context_snapshot = {**snapshot, "query_id": history.id}
    step = runtime.start_step(ctx.db, workspace_id=ctx.workspace_id, run_id=run.id, sequence=1,
                              step_type="collection", name="HTTP 批量采集", input_data=snapshot)
    ctx.db.commit()
    rows, origins, outcomes = [], [], []
    field_names = {field.name for field in config.fields}
    size, truncated, stop_reason = 0, False, ""
    try:
        for index, (request_config, body) in enumerate(requests):
            if time.monotonic() >= deadline:
                stop_reason = stop_reason or "整批超时，未发送此请求"
            if len(rows) >= config.max_rows:
                stop_reason = stop_reason or "结果已达行数上限，未发送此请求"
            if stop_reason:
                outcomes.append(RequestOutcome(index=index, status="skipped", message=stop_reason))
                continue
            request_start = time.monotonic()
            metadata = {}
            try:
                attempts = 0
                while True:
                    try:
                        response, metadata = request_json(
                            request_config, body, credentials, deadline=deadline,
                            max_body_bytes=body_limit, response_redactions=response_redactions,
                        )
                        break
                    except ToolExecutionError:
                        if attempts >= request_retries or time.monotonic() >= deadline:
                            raise
                        attempts += 1
                        time.sleep(min(retry_interval_ms / 1000, max(0, deadline - time.monotonic())))
                selected, limited = extract_records(response, config, limit=config.max_rows - len(rows), deadline=deadline)
                selected_size = len(json.dumps(selected, ensure_ascii=False, allow_nan=False).encode("utf-8"))
                if size + selected_size > MAX_RESULT_BYTES:
                    raise ValidationError("整批结果超过 8 MB，请减少请求、输出字段或行数")
                names = {name for row in selected for name in row}
                if len(field_names | names) > 200:
                    raise ValidationError("合并结果超过 200 个字段，请明确选择输出字段")
                field_names |= names
                rows.extend(selected)
                origins.extend([index] * len(selected))
                size += selected_size
                truncated |= limited
                outcomes.append(RequestOutcome(index=index, status="success", rows=len(selected), truncated=limited,
                                                executionTime=int((time.monotonic() - request_start) * 1000), **metadata))
            except Exception as cause:
                exc = cause if isinstance(cause, AppError) else ToolExecutionError("HTTP 采集或字段提取失败，请检查配置")
                outcomes.append(RequestOutcome(index=index, status="failed", message=exc.message,
                                                executionTime=int((time.monotonic() - request_start) * 1000),
                                                httpStatus=metadata.get("httpStatus", exc.extra.get("http_status")),
                                                responseBytes=metadata.get("responseBytes", 0)))
                if not config.continue_on_error:
                    stop_reason = "上一请求失败，按配置停止；未发送此请求"
        completed = sum(item.status == "success" for item in outcomes)
        status = "failed" if not completed else "partial" if completed != len(outcomes) or truncated else "success"
        incomplete = status != "success"
        columns = columns_for(rows, config.fields)
        statistics, insights, explanation, hints = describe(rows, [column.model_dump() for column in columns], truncated=truncated)
        statistics["partial"] = incomplete
        explanation = f"本次 HTTP 采集返回 {len(rows)} 行，{completed}/{len(outcomes)} 个请求成功。统计仅覆盖已返回结果，不代表完整数据源。"
        if incomplete:
            explanation += " 存在失败、未执行或截断，请先核查逐条状态。"
        result = CollectionResult(queryId=history.id, runId=run.id, status=status, outcomes=outcomes, rowOrigins=origins,
                                  executionTime=int((time.monotonic() - started) * 1000), data=rows, columns=columns,
                                  totalCount=len(rows), truncated=truncated, statistics=statistics, insights=insights,
                                  analysis=explanation, chartRecommendations=hints, datasourceId=source.id,
                                  datasourceVersion=config.version, success=not incomplete,
                                  errorMessage="" if not incomplete else "采集未全部完成，请查看逐条状态；当前仅包含成功请求的结果")
        history.status, history.result_json, history.execution_ms = status, result.model_dump(mode="json"), result.executionTime
        history.error_message = result.errorMessage
        summary = {"query_id": history.id, "rows": len(rows), "status": status, "requests": len(outcomes), "truncated": truncated}
        if status == "failed":
            runtime.fail_step(ctx.db, step, "collection_failed", result.errorMessage)
        else:
            runtime.complete_step(ctx.db, step, summary)
        runtime.finalize_run(ctx.db, run, status="completed" if status == "success" else status)
        commit_change(ctx, "collection.execute", "datasources", source.id, summary)
        return result
    except Exception as cause:
        ctx.db.rollback()
        history.status, history.error_message = "failed", "采集运行未能完成，请查看运行记录"
        runtime.fail_step(ctx.db, step, "collection_failed", history.error_message)
        runtime.finalize_run(ctx.db, run, status="failed", error_code="collection_failed", error_summary=history.error_message)
        commit_change(ctx, "collection.failed", "datasources", source.id, {"query_id": history.id})
        raise ToolExecutionError(history.error_message, query_id=history.id, run_id=run.id) from cause


def list_history(ctx, source_id: str, *, limit: int = 100, offset: int = 0, paged: bool = False) -> list | dict:
    """Require current source and history scope, including after soft deletion."""

    # 权限与数据范围：要求数据源**当前**可见（软删除后立即不可读），记录本身也按
    # datasources.view 过滤；保留失败与部分成功记录——逐条状态是排查依据，不能只留成功。
    # 返回 [HistoryView]（paged=True 时返回分页字典）。
    # 抛出：数据源不存在或不是 HTTP 类型时抛 NotFoundError。
    source = owned_record(ctx, DataSource, source_id, "datasources.view")
    if source.provider != "http":
        raise NotFoundError("HTTP 数据源不存在")
    query = visible_records(ctx, QueryHistory, "datasources.view").where(
        QueryHistory.query_mode == "http", QueryHistory.datasource_id == source.id,
    ).order_by(QueryHistory.created_at.desc(), QueryHistory.id)
    if paged:
        from app.common.pagination import page_records
        return page_records(ctx.db, query, limit=limit, offset=offset, view=history_view)
    return [history_view(row) for row in ctx.db.scalars(query.limit(limit).offset(offset))]


def historical_result(ctx, history_id: str) -> CollectionResult:
    """读取一条已持久化的采集结果（GET /api/v1/collection/history/{history_id}）。

    权限：沿 datasources.view 同时校验采集记录与**其数据源**当前仍可见；撤销数据源
    后不能再读旧采集结果。不重新发送任何 HTTP 请求，只返回已保存的数据、逐条状态、
    rowOrigins 与配置版本。
    抛出：记录不存在/非 http 类型抛 ``NotFoundError``；尚无结果抛 ``ValidationError``。
    """
    history = owned_record(ctx, QueryHistory, history_id, "datasources.view")
    if history.query_mode != "http":
        raise NotFoundError("HTTP 采集记录不存在")
    owned_record(ctx, DataSource, history.datasource_id, "datasources.view")
    if not history.result_json:
        raise ValidationError("该采集尚无可用结果", status=history.status)
    return CollectionResult.model_validate(history.result_json)
