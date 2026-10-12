"""Transactional external writes with durable intent and honest commit uncertainty.

中文补充（写回执行——外部数据库写入的唯一通道）：
- 定位：工作流的"写回目标表"节点在此执行。写入的是**外部业务库**，与平台库不在
  同一个事务里，因此这里用"先落意图、再提交、最后落回执"的三段式来保证可追溯：
  WriteOperation 记录 prepared → committing → succeeded/failed/uncertain。
- 幂等回执：execution_id + node_id 唯一。同一节点重复执行时——
  已 succeeded 直接返回原回执（不重复写）；未成功则抛 ConflictError，
  要求人工核对提交结果，**绝不自动重发**（外部写入可能已生效）。
- 提交不确定：发出 COMMIT 后若拿不到确认，状态记为 uncertain 且 affected_rows 为空
  （而不是 0），提示"请核对目标库，禁止重试"；这是刻意的"诚实失败"语义。
- 整批回滚：所有行在同一个连接的事务里执行；任一行失败（含 UPDATE 未恰好命中 1 行）
  即回滚整批，并把状态记为 failed、受影响行数 0。
- 授权边界：写入前必须通过 writeback.targets 的表/字段白名单、唯一键与数据源
  版本校验；读取历史回执也要求当前 workflows.view 与 datasources.view 权限。
"""
import hashlib
import json
import time
from typing import Any

from sqlalchemy import Table, and_, select
from sqlalchemy.engine import Connection

from app.common.deps import AuthContext
from app.common.owned_records import owned_record
from app.errors import AppError, ConflictError, ToolExecutionError, ValidationError
from app.models import DataSource
from app.modules.analytics.connections import sql_engine
from app.modules.system.service_helpers import commit_change, flush_changes
from app.modules.workflows.models import WorkflowExecution
from app.modules.workflows.expressions import RuleBudget
from app.modules.writeback.models import WriteOperation
from app.modules.writeback.schemas import OperationView, OutputTableConfig
from app.modules.writeback.targets import table_structure, target_view, validate_node_target
from app.modules.writeback.values import mapped_rows


def operation_view(row: WriteOperation) -> OperationView:
    """Expose an unfinished receipt as unconfirmed, never successful or retryable.

    中文补充：把内部状态翻译成对外语义。prepared/committing 一律展示为
    unconfirmed（并附中文提示"请核对目标库，不要重复执行"），因为这两种状态都
    可能已经在目标库生效；只有 succeeded 才是确定成功。
    """
    status = "unconfirmed" if row.status in {"prepared", "committing"} else row.status
    return OperationView(id=row.id, executionId=row.execution_id, nodeId=row.node_id, targetId=row.target_id,
                         status=status, requestedRows=row.requested_rows, affectedRows=row.affected_rows,
                         errorMessage=row.error_message or ("尚未取得最终提交凭据；请核对目标库，不要重复执行" if status == "unconfirmed" else ""),
                         snapshot=row.snapshot, createTime=row.created_at.isoformat())


def for_execution(ctx: AuthContext, execution_id: str) -> list[OperationView]:
    """Read actual receipts through current execution and data-source permissions.

    中文补充：查询某次工作流执行产生的写入回执。双重鉴权：先按 workflows.view
    确认执行记录可见（并复核工作流归属），再对回执涉及的每个数据源校验
    datasources.view——避免通过回执列表探测他人数据源信息。
    返回按创建时间排序的回执视图列表；不触发任何写入或重试。
    """
    from app.modules.workflows.execution import get_execution
    execution = owned_record(ctx, WorkflowExecution, execution_id, "workflows.view")
    get_execution(ctx, execution.workflow_id, execution.id)
    rows = ctx.db.scalars(select(WriteOperation).where(
        WriteOperation.workspace_id == ctx.workspace_id, WriteOperation.execution_id == execution_id,
    ).order_by(WriteOperation.created_at, WriteOperation.id)).all()
    for row in rows:
        owned_record(ctx, DataSource, row.datasource_id, "datasources.view")
    return [operation_view(row) for row in rows]


def receipt(row: WriteOperation, value: Any) -> dict:
    """Return a small write summary and retain the business report deep link.

    中文补充：给下游节点的回执摘要（只保留必要字段，不回灌整批数据）。
    若上游 value 里带 alertReportId / qualityInspectionId，则透传出去，
    让后续节点/前端能直接跳转到对应业务报告——这是"写回-报告"链路的约定字段。
    """
    output = {"writeOperationId": row.id, "writeStatus": row.status, "affectedRows": row.affected_rows,
              "targetId": row.target_id, "tableName": row.snapshot["target"]["tableName"]}
    if isinstance(value, dict) and value.get("alertReportId"):
        output["alertReportId"] = value["alertReportId"]
    if isinstance(value, dict) and value.get("qualityInspectionId"):
        output["qualityInspectionId"] = value["qualityInspectionId"]
    return output


def _execute_rows(connection: Connection, table: Table, rows: list[dict], config: OutputTableConfig, budget: RuleBudget) -> None:
    """在同一连接的事务内逐行写入（insert 或按唯一键 update），失败即抛错由调用方回滚。

    update 模式要求"恰好命中 1 行"：rowcount ≠ 1 说明定位键不唯一或记录已消失，
    继续执行会污染数据，因此直接中止整批（配置的 keys 已在保存目标时校验为真实唯一键）。
    每行都会 budget.consume()，行数/耗时超预算时抛业务错误。
    """
    for row in rows:
        budget.consume()
        if config.mode == "insert":
            connection.execute(table.insert().values(**row))
        else:
            predicate = and_(*(table.c[key] == row[key] for key in config.keys))
            changes = {name: value for name, value in row.items() if name not in config.keys}
            result = connection.execute(table.update().where(predicate).values(**changes))
            if result.rowcount != 1:
                raise ValidationError("更新定位没有恰好命中一条记录；整批事务已停止")


def execute_write(ctx: AuthContext, config: OutputTableConfig, value: Any, budget: RuleBudget, *, execution_id: str, node_id: str) -> dict:
    """Reserve intent before DML; never retry a possibly committed external operation.

    The workflow key already prevents request replay. The additional unique
    execution/node receipt guards future resume logic. Local and external DBs
    are not a distributed transaction: a lost COMMIT acknowledgement is marked
    uncertain, not failed/zero rows. Reading history never reconnects to write.

    中文补充：执行一次写回的完整流程（供工作流写回节点调用）。
    参数：``config`` 是节点上已保存的输出表配置（目标/字段/唯一键/模式），
    ``value`` 是上游节点的数据，``budget`` 控制行数与时限，``execution_id``+``node_id``
    构成幂等键。
    返回：写入回执 dict（receipt），含 writeOperationId/writeStatus/affectedRows。
    异常：目标或数据源未授权/停用/结构变更抛 ValidationError 或 NotFoundError；
    同一节点已有未成功凭据抛 ConflictError；写入失败抛 ToolExecutionError
    （错误信息区分"未提交"与"提交结果未知"两种情况）。
    副作用：写外部数据库（insert/update）+ 写平台库的 WriteOperation 与审计日志。
    注意：本函数在外部库提交前后各写一次平台库状态，正是因为两者无法共用一个事务。
    """
    target, source = validate_node_target(ctx, config, active=True, execute=True)
    existing = ctx.db.scalar(select(WriteOperation).where(WriteOperation.execution_id == execution_id, WriteOperation.node_id == node_id))
    if existing:
        if existing.status == "succeeded":
            return receipt(existing, value)
        raise ConflictError("此节点已有写入凭据，不能自动重发；请先核对提交结果", write_operation_id=existing.id)
    operation, committing = None, False
    try:
        with sql_engine(ctx.db, source, write=True) as engine, engine.connect() as connection:
            if source.provider == "sqlite":
                connection.connection.driver_connection.set_progress_handler(lambda: int(time.monotonic() >= budget.deadline), 1000)
            table, unique = table_structure(connection, source, target.table_name, [field.column for field in config.fields])
            if config.mode == "update" and set(config.keys) not in [set(keys) for keys in unique]:
                raise ValidationError("目标唯一键结构已经改变，未进行写入")
            rows = mapped_rows(config, value, table, source.provider, budget)
            digest = hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False, default=str, allow_nan=False).encode()).hexdigest()
            operation = WriteOperation(workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id,
                                       execution_id=execution_id, node_id=node_id, target_id=target.id,
                                       datasource_id=source.id, requested_rows=len(rows),
                                       snapshot={"target": target_view(target).model_dump(), "mapping": config.model_dump(by_alias=True),
                                                 "provider": source.provider, "rowsSha256": digest})
            ctx.db.add(operation)
            flush_changes(ctx)
            commit_change(ctx, "database_write.prepare", "datasources", source.id, {"operation_id": operation.id, "rows": len(rows)})
            try:
                _execute_rows(connection, table, rows, config, budget)
                budget.consume()
                operation.status = "committing"
                commit_change(ctx, "database_write.commit_requested", "datasources", source.id, {"operation_id": operation.id})
                committing = True
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        operation.status, operation.affected_rows = "succeeded", len(rows)
        commit_change(ctx, "database_write.succeeded", "datasources", source.id, {"operation_id": operation.id, "rows": len(rows)})
        return receipt(operation, value)
    except Exception as cause:
        if operation is None:
            if isinstance(cause, AppError):
                raise
            raise ToolExecutionError("目标数据库连接或结构检查失败；本次未开始写入") from cause
        ctx.db.rollback()
        operation.status = "uncertain" if committing else "failed"
        operation.affected_rows = None if committing else 0
        operation.error_message = ("提交结果未能确认，请核对目标库与写入凭据，禁止自动重试" if committing else
                                   "写入事务未提交，请检查字段类型、唯一键、约束或目标数据库权限")
        commit_change(ctx, "database_write." + operation.status, "datasources", source.id, {"operation_id": operation.id})
        raise ToolExecutionError(operation.error_message, write_operation_id=operation.id) from cause
