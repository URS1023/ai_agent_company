"""Read-only source scans with sanitized, checksum-verifiable migration archives.

中文补充（迁移来源扫描）：
- 只读边界（最重要）：来源库必须是 settings.MIGRATION_SOURCE_SCHEMAS 白名单内的
  本机 MySQL 库，且不能是平台库 PLATFORM_DB；库名/表名都必须匹配
  ``^[A-Za-z0-9_]+$`` 才允许拼进 ``SELECT``（这里用的是标识符白名单而非参数化，
  因为表名无法参数化）。整个流程只执行 SELECT/COUNT，不做任何 DDL/DML，
  汇总里也显式声明 sourceMutations=0。
- 脱敏：字段名命中 _SENSITIVE（password/token/secret/api_key/…）时只保存哈希标记
  （{"redacted":true,"sha256":…}），不落明文；二进制只存大小与摘要。
- 可校验：每行算 payload_hash，批次算 sha256（表名+主键+行哈希串联），
  后续可据此核对归档是否被篡改或遗漏。
- 上限：来源总行数超过 settings.MIGRATION_MAX_ROWS 直接拒绝，要求分表扫描；
  归档时每 500 行 flush 一次以免大事务撑爆内存。
- 权限：查看 system.development.view，执行扫描 system.development.manage。
- 失败处理：异常时批次置 failed 并记录错误类型与摘要后原样抛出；连接引擎在 finally 中 dispose。
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.engine import URL

from app.common.authorization import require_permission
from app.common.deps import AuthContext
from app.config import settings
from app.errors import NotFoundError, ValidationError
from app.ids import new_id

from .models import MigrationBatch, MigrationRecord
from .schemas import MigrationScanInput


_IDENTIFIER = re.compile(r"^[A-Za-z0-9_]+$")
# 敏感字段名识别（不区分大小写）：命中即只存哈希，绝不落明文。
_SENSITIVE = re.compile(r"(?:password|passwd|pwd|secret|token|api[_-]?key|credential|cookie|private[_-]?key)", re.I)


def _iso(value: datetime | None) -> str | None:
    """时间转 ISO 字符串（None 保持 None）。"""
    return value.isoformat() if value else None


def allowed_sources(ctx: AuthContext) -> dict:
    """返回本机只读迁移来源的可用信息（不含凭据）；权限 system.development.view。

    返回 host/port/schemas 供前端选择来源库，并显式声明 credentialsExposed=False、
    readOnlyPolicy=True，让界面与调用方明确这是只读通道。
    """
    require_permission(ctx, "system.development.view")
    return {
        "provider": "mysql",
        "host": settings.MYSQL_HOST,
        "port": int(settings.MYSQL_PORT),
        "schemas": [item for item in settings.MIGRATION_SOURCE_SCHEMAS if item != settings.PLATFORM_DB],
        "targetSchema": settings.PLATFORM_DB,
        "credentialsExposed": False,
        "readOnlyPolicy": True,
    }


def _source_engine(schema: str):
    """为白名单内的来源库创建只读用途的引擎（凭据来自服务端配置，不来自请求）。

    库名必须先通过白名单与标识符双重校验；连接参数带 10 秒读写超时与 pool_pre_ping，
    避免扫描把连接挂死。异常：不在白名单/名称非法抛 ValidationError。
    注意：这里不做"只读账号"假设，只读保证来自"只发 SELECT"这一约定。
    """
    if schema not in settings.MIGRATION_SOURCE_SCHEMAS or schema == settings.PLATFORM_DB:
        raise ValidationError("来源数据库未列入本机只读迁移白名单")
    if not _IDENTIFIER.fullmatch(schema):
        raise ValidationError("来源数据库名称无效")
    url = URL.create(
        "mysql+pymysql", username=settings.MYSQL_USER, password=settings.MYSQL_PASSWORD,
        host=settings.MYSQL_HOST, port=int(settings.MYSQL_PORT), database=schema,
        query={"charset": "utf8mb4"},
    )
    return create_engine(url, pool_pre_ping=True, future=True, connect_args={"read_timeout": 10, "write_timeout": 10})


def _json_value(value: Any) -> Any:
    """把来源行的任意列值转成可 JSON 序列化的形态（二进制只留大小与摘要）。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        return {"binary": True, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    if isinstance(value, (dict, list, tuple)):
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    return str(value)


def _safe_payload(row: dict) -> tuple[dict, str]:
    """脱敏一行并返回 (可入库 payload, 原始行 sha256)。

    哈希基于**原始值**计算（脱敏前），因此即使字段被脱敏，校验和仍能反映源数据变化。
    敏感字段替换为 {"redacted":true,"sha256":…}，保留"是否变化"的可比性但不泄露内容。
    """
    normalized = {str(key): _json_value(value) for key, value in row.items()}
    raw = json.dumps(normalized, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    safe = {}
    for key, value in normalized.items():
        if _SENSITIVE.search(key):
            encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
            safe[key] = {"redacted": True, "sha256": hashlib.sha256(encoded.encode()).hexdigest()}
        else:
            safe[key] = value
    return safe, hashlib.sha256(raw.encode()).hexdigest()


def _source_key(row: dict, primary_keys: list[str], index: int) -> str:
    """计算来源行的稳定标识：主键值拼接；无主键时退化为行号；过长则改存 sha256。

    长度上限 350 是为了适配数据库列宽；哈希化后仍可用于比对（不可读但可判等）。
    """
    values = [str(_json_value(row.get(key))) for key in primary_keys]
    value = "|".join(values) if values else f"row:{index}"
    if len(value) > 350:
        value = "sha256:" + hashlib.sha256(value.encode()).hexdigest()
    return value


def batch_view(row: MigrationBatch) -> dict:
    """迁移批次视图（含 checksum 与 summary，前端据此展示扫描结果与只读声明）。"""
    return {
        "id": row.id, "sourceType": row.source_type, "sourceName": row.source_name,
        "sourceSchema": row.source_schema, "status": row.status, "tableCounts": row.table_counts,
        "rowCount": row.row_count, "importedCount": row.imported_count,
        "archivedCount": row.archived_count, "failedCount": row.failed_count,
        "checksum": row.checksum, "options": row.options, "summary": row.summary,
        "createdBy": row.created_by, "createTime": _iso(row.created_at), "completeTime": _iso(row.completed_at),
    }


def list_batches(ctx: AuthContext, limit: int = 100) -> list[dict]:
    """列出本工作区的迁移批次（按创建时间倒序，最多 limit 条）；权限 system.development.view。"""
    require_permission(ctx, "system.development.view")
    rows = ctx.db.scalars(select(MigrationBatch).where(
        MigrationBatch.workspace_id == ctx.workspace_id,
    ).order_by(MigrationBatch.created_at.desc()).limit(limit)).all()
    return [batch_view(row) for row in rows]


def get_batch(ctx: AuthContext, batch_id: str) -> dict:
    """读取批次详情（限定本工作区）；权限 system.development.view；不存在抛 NotFoundError。"""
    require_permission(ctx, "system.development.view")
    row = ctx.db.scalar(select(MigrationBatch).where(
        MigrationBatch.id == batch_id, MigrationBatch.workspace_id == ctx.workspace_id,
    ))
    if row is None:
        raise NotFoundError("迁移批次不存在")
    return batch_view(row)


def scan_source(ctx: AuthContext, payload: MigrationScanInput) -> dict:
    """Scan a whitelisted local source without DDL/DML and archive sanitized rows.

    中文补充：对白名单来源库做一次只读扫描并归档脱敏行；权限 system.development.manage。
    参数：``payload.source_schema`` 必须命中白名单；``include_tables`` 为空表示全部表，
    指定的表必须真实存在且名称合法；``archive_rows=False`` 时只统计行数不落归档行。
    返回：批次视图（表级计数、总行数、归档数、checksum 与只读声明摘要）。
    异常：库名/表名非法、表不存在、行数超上限抛 ValidationError；失败时批次标记为
    failed 并把错误摘要写入 summary 后重新抛出（不吞异常）。
    副作用：读取外部来源库 + 写 MigrationBatch/MigrationRecord + 审计日志；
    对来源库**不产生任何写入**。
    """
    require_permission(ctx, "system.development.manage")
    if not _IDENTIFIER.fullmatch(payload.source_schema):
        raise ValidationError("来源数据库名称无效")
    batch = MigrationBatch(
        id=new_id(), workspace_id=ctx.workspace_id, source_type="mysql",
        source_name=payload.source_name.strip(), source_schema=payload.source_schema,
        status="scanning", options={"includeTables": payload.include_tables, "archiveRows": payload.archive_rows},
        created_by=ctx.user_id,
    )
    ctx.db.add(batch)
    ctx.db.commit()
    engine = None
    try:
        engine = _source_engine(payload.source_schema)
        inspector = inspect(engine)
        available = sorted(inspector.get_table_names())
        requested = list(dict.fromkeys(payload.include_tables))
        unknown = sorted(set(requested) - set(available))
        if unknown:
            raise ValidationError("部分来源表不存在", tables=unknown)
        tables = requested or available
        if any(not _IDENTIFIER.fullmatch(name) for name in tables):
            raise ValidationError("来源表名称无效")
        counts: dict[str, int] = {}
        primary_keys: dict[str, list[str]] = {}
        with engine.connect() as connection:
            for table in tables:
                counts[table] = int(connection.scalar(text(f"SELECT COUNT(*) FROM `{table}`")) or 0)
                primary_keys[table] = list((inspector.get_pk_constraint(table) or {}).get("constrained_columns") or [])
        total = sum(counts.values())
        if total > settings.MIGRATION_MAX_ROWS:
            raise ValidationError(
                f"来源记录共 {total} 条，超过单批 {settings.MIGRATION_MAX_ROWS} 条安全上限，请分表扫描",
            )
        archived = 0
        digest = hashlib.sha256()
        if payload.archive_rows:
            with engine.connect() as connection:
                for table in tables:
                    result = connection.execute(text(f"SELECT * FROM `{table}`"))
                    for row_index, mapping in enumerate(result.mappings(), start=1):
                        payload_json, payload_hash = _safe_payload(dict(mapping))
                        source_key = _source_key(dict(mapping), primary_keys[table], row_index)
                        ctx.db.add(MigrationRecord(
                            workspace_id=ctx.workspace_id, batch_id=batch.id,
                            source_table=table, source_key=source_key, payload_hash=payload_hash,
                            payload=payload_json, status="archived",
                        ))
                        digest.update(f"{table}\0{source_key}\0{payload_hash}\n".encode("utf-8"))
                        archived += 1
                        if archived % 500 == 0:
                            ctx.db.flush()
        else:
            digest.update(json.dumps(counts, sort_keys=True).encode())
        batch.table_counts = counts
        batch.row_count = total
        batch.archived_count = archived
        batch.checksum = digest.hexdigest()
        batch.status = "completed"
        batch.summary = {
            "tables": len(tables), "readOnly": True, "rowsArchived": archived,
            "sensitiveValuesPersisted": False, "sourceMutations": 0,
        }
        batch.completed_at = datetime.utcnow()
        from app.common.audit import audit
        audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
              action="system.migration.scan", resource_type="migration_batch", resource_id=batch.id,
              metadata={"sourceSchema": batch.source_schema, "tables": len(tables), "rows": total})
        ctx.db.commit()
        return batch_view(batch)
    except Exception as exc:
        ctx.db.rollback()
        batch = ctx.db.get(MigrationBatch, batch.id)
        if batch:
            batch.status = "failed"
            batch.failed_count = 1
            batch.summary = {"errorType": type(exc).__name__, "message": str(exc)[:500], "sourceMutations": 0}
            batch.completed_at = datetime.utcnow()
            ctx.db.commit()
        raise
    finally:
        if engine is not None:
            engine.dispose()


def list_records(ctx: AuthContext, batch_id: str, *, table: str = "", limit: int = 100, offset: int = 0) -> dict:
    """分页读取某批次的归档行（可按来源表过滤）；权限 system.development.view。

    先 get_batch 校验批次归属（跨工作区/不存在即 404），再按 source_table + source_key
    稳定排序分页。返回 items/total/limit/offset；payload 已是脱敏后的内容。
    """
    require_permission(ctx, "system.development.view")
    get_batch(ctx, batch_id)
    statement = select(MigrationRecord).where(
        MigrationRecord.workspace_id == ctx.workspace_id, MigrationRecord.batch_id == batch_id,
    )
    if table:
        statement = statement.where(MigrationRecord.source_table == table)
    total = int(ctx.db.scalar(select(func.count()).select_from(statement.order_by(None).subquery())) or 0)
    rows = ctx.db.scalars(statement.order_by(MigrationRecord.source_table, MigrationRecord.source_key).limit(limit).offset(offset)).all()
    return {
        "items": [{
            "id": row.id, "sourceTable": row.source_table, "sourceKey": row.source_key,
            "payloadHash": row.payload_hash, "payload": row.payload, "status": row.status,
            "targetType": row.target_type, "targetId": row.target_id,
            "errorSummary": row.error_summary, "createTime": _iso(row.created_at),
        } for row in rows],
        "total": total, "limit": limit, "offset": offset,
    }
