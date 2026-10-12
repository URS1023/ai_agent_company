"""Authorization and live schema checks for explicit database write allowlists.

中文补充（写入目标与白名单校验）：
- 定位：写回功能的安全边界都在这里。写目标（WriteTarget）保存的是"用户显式授权
  的目标表 + 允许字段 + 允许模式（insert/update）+ 唯一键配置"，执行期只认这份白名单。
- 三层校验：
  1) 数据源侧白名单：connection_config.allowedTables / allowedColumns（表名/字段名）；
  2) 实时结构校验：目标必须是**真实普通表**（视图、sqlite_/pg_ 等系统表、
     未授权表一律拒绝），字段必须存在且非计算字段，MySQL 表必须是 InnoDB
     （否则无法保证整批回滚）；
  3) 版本一致性：row.source_version 与当前数据源版本必须一致，配置改动后强制重新确认。
- 权限：读目标要求 datasources.view；保存要求 datasources.edit + datasources.writeback；
  执行写回额外要求 datasources.writeback（见 get_target 的 execute 分支）。
- 删除语义：remove_target 只置 status=deleted（撤销后续写入），不删除外部记录与历史回执。
"""
from sqlalchemy import MetaData, Table, inspect, select
from sqlalchemy.engine import Connection

from app.common.deps import AuthContext
from app.common.pagination import page_records
from app.common.owned_records import owned_record, visible_records
from app.errors import AppError, NotFoundError, ValidationError
from app.models import DataSource
from app.modules.analytics.connections import connection_failure, readonly_connection, source_version
from app.modules.system.service_helpers import check_version, commit_change, flush_changes, scoped_record
from app.modules.writeback.models import WriteTarget
from app.modules.writeback.schemas import OutputTableConfig, TargetInput, TargetView


def target_view(row: WriteTarget) -> TargetView:
    """Return documented configuration only, never database credentials.

    中文补充：写目标的对外视图只含表名/字段/模式/版本等配置，凭据一律来自数据源
    记录，不在此结构中出现（因此可以安全地返回给前端）。
    """
    return TargetView(id=row.id, name=row.name, datasourceId=row.datasource_id, sourceVersion=row.source_version,
                      tableName=row.table_name, columns=row.columns, modes=row.modes, status=row.status, version=row.version)


def get_target(ctx: AuthContext, target_id: str, *, active: bool = False, execute: bool = False) -> tuple[WriteTarget, DataSource]:
    """Bind all visibility and execution rights to the exact destination source.

    中文补充：取写目标并绑定权限/状态/版本校验。``execute=True`` 额外要求
    datasources.writeback（真正的写权限）；``active=True`` 时要求目标与数据源均启用，
    且 source_version 与目标保存时记录的一致——数据源连接配置一旦被改，
    必须重新核对表结构并重新保存目标，避免把数据写到同名但不同的库上。
    异常：不存在/越权抛 NotFoundError；已停用或版本变化抛 ValidationError。
    """
    row = scoped_record(ctx, WriteTarget, target_id)
    source = owned_record(ctx, DataSource, row.datasource_id, "datasources.view")
    if execute:
        owned_record(ctx, DataSource, row.datasource_id, "datasources.writeback")
    if active:
        if row.status != "active" or source.status != "active":
            raise ValidationError("写入目标或数据源已停用，不能开始新写入")
        if row.source_version != source_version(source):
            raise ValidationError("目标数据源配置已改变，请重新核对目标表字段并保存授权目标")
    return row, source


def list_targets(ctx: AuthContext, *, limit: int, offset: int, paged: bool = False) -> list[TargetView] | dict:
    """Apply source visibility before pagination rather than filtering a page.

    中文补充：列表先按 datasources.view 的数据范围过滤数据源，再分页——先分页后过滤
    会导致某页只剩零星记录甚至空页。``paged=True`` 返回分页结构（含总数），
    否则返回列表（内部调用用）。已删除的目标不返回。
    """
    source_ids = visible_records(ctx, DataSource, "datasources.view").with_only_columns(DataSource.id)
    query = select(WriteTarget).where(
        WriteTarget.workspace_id == ctx.workspace_id, WriteTarget.status != "deleted",
        WriteTarget.datasource_id.in_(source_ids),
    ).order_by(WriteTarget.created_at.desc(), WriteTarget.id)
    if paged:
        return page_records(ctx.db, query, limit=limit, offset=offset, view=target_view)
    rows = ctx.db.scalars(query.offset(offset).limit(limit)).all()
    return [target_view(row) for row in rows]


def table_structure(connection: Connection, source: DataSource, name: str, columns: list[str]) -> tuple[Table, list[list[str]]]:
    """Reflect only an allowed real table. Views and nontransactional MySQL are refused.

    中文补充：写入前的结构闸门，返回 (Table, 唯一键候选列表)。
    - 表名以 "." / "_" / "sqlite_" / "pg_" 开头，或不在数据源 allowedTables 内 → 拒绝；
    - 字段不在 allowedColumns[表] 内，或表中不存在、属于计算字段 → 拒绝；
    - 名称必须是 inspector 认识的普通表（视图不在 get_table_names 中），
      因此不能借写入创建业务表或写视图；
    - MySQL 只接受 InnoDB，否则无法保证"整批失败回滚"；
    - 返回的唯一键候选用于 update 模式的定位校验（主键 + 唯一约束 + 唯一索引）。
    异常：任一条件不满足抛 ValidationError（本次不写入）。
    """
    config = source.connection_config or {}
    allowed = config.get("allowedTables") or []
    fields = config.get("allowedColumns") or {}
    if name.startswith((".", "_", "sqlite_", "pg_")) or (allowed and name not in allowed):
        raise ValidationError("目标表不在授权业务表范围内")
    if name in fields and not set(columns) <= set(fields[name]):
        raise ValidationError("目标字段不在数据源授权范围内")
    inspector = inspect(connection)
    if name not in inspector.get_table_names():
        raise ValidationError("目标普通表不存在，不能写入视图或自动创建业务表")
    if source.provider == "mysql":
        engine = str(inspector.get_table_options(name).get("mysql_engine", "")).lower()
        if engine != "innodb":
            raise ValidationError("目标 MySQL 表必须使用 InnoDB，以支持整批失败回滚")
    table = Table(name, MetaData(), autoload_with=connection, resolve_fks=False)
    if not set(columns) <= set(table.c.keys()):
        raise ValidationError("目标字段不存在，请刷新表结构并重新配置；本次未写入")
    if any(table.c[field].computed is not None for field in columns):
        raise ValidationError("不能向数据库计算字段直接写入")
    unique = [list(table.primary_key.columns.keys())]
    unique += [item.get("column_names") or [] for item in inspector.get_unique_constraints(name)]
    unique += [item.get("column_names") or [] for item in inspector.get_indexes(name)
               if item.get("unique") and not item.get("dialect_options") and not item.get("expressions")]
    return table, [keys for keys in unique if keys and None not in keys]


def save_target(ctx: AuthContext, payload: TargetInput, target_id: str | None = None) -> TargetView:
    """Save an explicitly authorized destination after a read-only schema check.

    中文补充：新建或更新写目标。权限链：datasources.edit + datasources.writeback +
    datasources.view 三重校验，且数据源必须是启用的关系库（mysql/postgres/sqlite）。
    保存前会用**只读连接**做一次真实结构检查（表/字段/唯一键/InnoDB），
    校验通过才落库——这样"保存成功"即代表该目标当时确实可写。
    已存在的目标不允许改绑数据源（避免把授权过的写入悄悄指向别的库）；
    版本号不匹配抛冲突，要求刷新后重新确认。
    副作用：写 write_target 表 + 审计日志。
    """
    source = owned_record(ctx, DataSource, payload.datasource_id, "datasources.edit")
    owned_record(ctx, DataSource, source.id, "datasources.writeback")
    owned_record(ctx, DataSource, source.id, "datasources.view")
    if source.provider not in {"mysql", "postgres", "sqlite"} or source.status != "active":
        raise ValidationError("请选择已启用的关系数据库")
    if payload.source_version != source_version(source):
        raise ValidationError("数据源配置版本已变化，请刷新后重新确认")
    row = scoped_record(ctx, WriteTarget, target_id) if target_id else None
    if row:
        check_version(row, payload.version)
        if row.datasource_id != source.id:
            raise ValidationError("现有写入目标不能改绑其他数据源，请新建目标")
        if row.status == "deleted":
            raise NotFoundError("写入目标已移除")
    try:
        with readonly_connection(ctx.db, source) as connection:
            table_structure(connection, source, payload.table_name, payload.columns)
    except AppError:
        raise
    except Exception as cause:
        raise connection_failure(source, cause) from cause
    if row is None:
        row = WriteTarget(workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id)
        ctx.db.add(row)
    for name, value in payload.model_dump(exclude={"version"}).items():
        setattr(row, name, value)
    flush_changes(ctx)
    commit_change(ctx, "write_target.save", "datasources", source.id, {"target_id": row.id, "version": row.version})
    return target_view(row)


def remove_target(ctx: AuthContext, target_id: str, version: int) -> None:
    """Revoke new writes without dropping any external record or old receipt.

    中文补充：软删除（status=deleted）：只撤销"以后的新写入"，外部库数据与
    历史回执全部保留（审计需要）。需要 datasources.writeback 与版本号匹配。
    """
    row, source = get_target(ctx, target_id, execute=True)
    owned_record(ctx, DataSource, source.id, "datasources.edit")
    check_version(row, version)
    row.status = "deleted"
    commit_change(ctx, "write_target.remove", "datasources", source.id, {"target_id": row.id})


def validate_node_target(ctx: AuthContext, config: OutputTableConfig, *, execute: bool = False, active: bool = False) -> tuple[WriteTarget, DataSource]:
    """Validate pinned allowlists and unique keys before upstream requests start.

    中文补充：写回节点的执行前校验（在上游请求开始前就失败，避免白跑一遍流程）。
    除 get_target 的权限/状态/版本校验外，还要求：节点写入模式在目标允许的 modes 内、
    节点字段是目标白名单字段的子集、target_version 匹配；
    真正执行时（execute=True）再用只读连接复核唯一键，update 模式必须使用
    数据库中真实存在的完整主键或唯一键定位。
    异常：超出白名单或唯一键不匹配抛 ValidationError。
    """
    row, source = get_target(ctx, config.target_id, active=active, execute=execute)
    if active:
        check_version(row, config.target_version)
        if config.mode not in row.modes or not {item.column for item in config.fields} <= set(row.columns):
            raise ValidationError("节点写入方式或字段超出目标白名单")
        if execute:
            try:
                with readonly_connection(ctx.db, source) as connection:
                    _table, unique = table_structure(connection, source, row.table_name, [item.column for item in config.fields])
                    if config.mode == "update" and set(config.keys) not in [set(keys) for keys in unique]:
                        raise ValidationError("更新定位必须使用数据库中完整的主键或唯一键")
            except AppError:
                raise
            except Exception as cause:
                raise connection_failure(source, cause) from cause
    return row, source
