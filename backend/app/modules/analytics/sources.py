"""Unified Cloud Brain data-source lifecycle with redacted, versioned contracts."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, or_, select, update

from app.common.authorization import require_permission
from app.common.owned_records import owned_record, visible_records
from app.errors import AppError, ConflictError, ValidationError
from app.ids import new_id
from app.models import DataSource
from app.modules.analytics.connections import (
    DEFAULT_PORTS, connection_failure, database_name, elastic_client, readonly_connection, source_version,
    validate_destination,
)
from app.modules.analytics.schemas import SourceInput, SourceView
from app.modules.secret.service import create_secret
from app.modules.system.service_helpers import commit_change


def _automatic_metadata_sync(ctx, row: DataSource) -> None:
    """保存配置后尽力同步；连接失败只标记本版本失败，配置改变则废弃迟到结果。"""

    if row.status != "active":
        return
    from app.modules.analytics.metadata import source_metadata
    from app.modules.analytics.profile_updates import update_profile
    expected_version = source_version(row)
    try:
        source_metadata(ctx, row, refresh=True)
        commit_change(
            ctx, "datasource.metadata.auto_sync", "datasources", row.id,
            {"status": "ready", "source_version": source_version(row)},
        )
    except AppError as exc:
        ctx.db.rollback()
        current = ctx.db.get(DataSource, row.id)
        if current is None:
            return
        profile = {
            "metadataStatus": "failed",
            "semanticIndexStatus": "failed",
            "metadataError": exc.message[:500],
            "metadataSyncedAt": datetime.utcnow().isoformat(),
        }
        try:
            # 失败状态也只属于发起同步的版本；不能污染随后保存或停用的新配置。
            update_profile(ctx.db, current, profile, expected_version=expected_version)
        except ConflictError:
            return
        ctx.db.commit()


def source_view(row: DataSource, health=None) -> SourceView:
    """把数据源实体转成对外视图：**永不返回密码、密钥或连接串**。

    ``hasPassword`` 只表达“是否配置了加密凭据”；说明文本按 configured > metadata
    （自动生成）> empty 的优先级选择，并回传 descriptionSource 让前端知道当前
    展示的是人工说明还是元数据生成说明。``environmentSummary`` 由方言 + 库名 +
    主机端口拼成，同样不含凭据。未做过健康检查时 healthStatus 为 unknown，不伪造健康。
    """
    config = row.connection_config or {}
    configured_description = str(config.get("description") or "").strip()
    auto_description = str(config.get("autoDescription") or "").strip()
    if configured_description:
        description_source = "configured"
    elif auto_description:
        description_source = "metadata"
    else:
        description_source = "empty"
    environment = f"{row.provider.upper()} · {database_name(row) or '未命名数据库'}"
    if row.provider != "sqlite" and config.get("host"):
        environment += f" · {config.get('host')}:{config.get('port') or DEFAULT_PORTS.get(row.provider)}"
    return SourceView(
        id=row.id, name=row.name, type=row.provider, host=str(config.get("host") or ""),
        port=config.get("port") or DEFAULT_PORTS.get(row.provider), databaseName=database_name(row),
        username=str(config.get("username") or config.get("user") or ""),
        hasPassword=bool(row.secret_id), description=configured_description or auto_description,
        configuredDescription=configured_description, autoDescription=auto_description,
        descriptionSource=description_source, environmentSummary=environment,
        enabled=row.status == "active", healthStatus=health.status if health else "unknown",
        lastHealthCheckAt=health.checked_at.isoformat() if health else "",
        healthLatencyMs=health.latency_ms if health else None,
        version=source_version(row),
        ssl=bool(config.get("ssl")), allowedTables=config.get("allowedTables") or [],
        allowedColumns=config.get("allowedColumns") or {}, timeoutSeconds=int(config.get("timeoutSeconds") or 15),
        maxRows=int(config.get("maxRows") or 1000),
        metadataStatus=str(config.get("metadataStatus") or "not_synced"),
        tableCount=int(config.get("tableCount") or 0),
        businessTableCount=int(config.get("businessTableCount") or 0),
        fieldCount=int(config.get("fieldCount") or 0),
        commentedTableCount=int(config.get("commentedTableCount") or 0),
        commentedFieldCount=int(config.get("commentedFieldCount") or 0),
        semanticIndexStatus=str(config.get("semanticIndexStatus") or "not_indexed"),
        semanticIndexedTableCount=int(config.get("semanticIndexedTableCount") or 0),
        businessDomains=list(config.get("businessDomains") or []),
        metadataSyncedAt=str(config.get("metadataSyncedAt") or ""),
        createTime=row.created_at.isoformat(),
        updateTime=row.updated_at.isoformat(),
    )


def _source_list_query(ctx, *, enabled_only: bool = False, keyword: str = ""):
    """数据源列表的基础查询：数据范围过滤 + 排除已删除 + 限定四类分析数据源。

    只包含 mysql/postgres/sqlite/elasticsearch：HTTP 采集源属于 collection 模块，
    在这里被排除（``get_source`` 也会拒绝 provider=http 的记录）。
    ``keyword`` 用 contains(autoescape=True)，因此用户输入里的 % 和 _ 按字面匹配。
    """
    query = visible_records(ctx, DataSource, "datasources.view").where(
        DataSource.status != "deleted", DataSource.provider.in_(["mysql", "postgres", "sqlite", "elasticsearch"]),
    )
    if enabled_only:
        query = query.where(DataSource.status == "active")
    if keyword.strip():
        query = query.where(DataSource.name.contains(keyword.strip(), autoescape=True))
    return query


def _source_list_views(ctx, rows) -> list[SourceView]:
    """批量装配列表视图：一次查询取回所有数据源的最新健康记录，避免逐行查询。"""
    from app.modules.analytics.governance import latest_health_checks
    checks = latest_health_checks(ctx, [row.id for row in rows])
    return [source_view(row, checks.get(row.id)) for row in rows]


def list_sources(ctx, *, enabled_only: bool = False) -> list[SourceView]:
    """Compatibility list for existing selectors; management uses bounded pages."""

    # 兼容旧选择器的全量列表：不做分页，因此只适合下拉/选择器场景，
    # 管理页必须使用 list_sources_page 以避免数据源很多时一次性全量返回。
    rows = ctx.db.scalars(_source_list_query(ctx, enabled_only=enabled_only).order_by(
        DataSource.created_at.desc(), DataSource.id.desc(),
    )).all()
    return _source_list_views(ctx, rows)


def list_sources_page(ctx, *, page: int, page_size: int, keyword: str = "", enabled_only: bool = False) -> dict:
    """分页搜索数据源（GET /api/v1/analytics/datasources/page），供管理页按需加载。

    返回 ``{items,total,page,pageSize}``。页码会被夹到有效区间：请求超出末页时返回
    最后一页而不是空结果，避免删除/筛选后出现“空白页”误解。
    """
    query = _source_list_query(ctx, enabled_only=enabled_only, keyword=keyword)
    total = ctx.db.scalar(select(func.count()).select_from(query.subquery())) or 0
    page = min(page, max(1, (total + page_size - 1) // page_size))
    rows = ctx.db.scalars(query.order_by(DataSource.created_at.desc(), DataSource.id.desc()).offset(
        (page - 1) * page_size,
    ).limit(page_size)).all()
    return {"items": _source_list_views(ctx, rows), "total": total, "page": page, "pageSize": page_size}


def get_source(ctx, source_id: str, permission: str = "datasources.view", *, active: bool = False, provider: str | None = None) -> DataSource:
    """按权限与数据范围解析一个分析数据源，是所有数据源操作的统一入口。

    参数：``permission`` 决定需要的数据范围；``active=True`` 要求数据源处于启用状态
    （用于执行类操作——停用后仍可查看历史，但不能发起新查询）；``provider`` 用于
    限定类型，传 ``"any"`` 表示不限，默认 ``None`` 会**拒绝 http 类型**，引导调用方
    改用 collection 模块的接口。

    抛出：无权限/越权抛权限类错误；类型不匹配抛 ``ValidationError``；已删除抛
    ``NotFoundError``（不是 404 空对象）；已停用且要求 active 时抛 ``ValidationError``。
    副作用：只读（可能绑定数据范围），不改动记录。
    """
    from app.common.data_scope import bind_data_scope
    bind_data_scope(ctx, permission)
    row = owned_record(ctx, DataSource, source_id, permission)
    if (provider not in (None, "any") and row.provider != provider) or (provider is None and row.provider == "http"):
        raise ValidationError("数据源类型与当前接口不匹配，请使用对应的数据源模块")
    if row.status == "deleted":
        from app.errors import NotFoundError
        raise NotFoundError("数据源已删除")
    if active and row.status != "active":
        raise ValidationError("数据源已停用；可以查看历史结果，但不能执行新查询")
    return row


def source_configuration(payload: SourceInput, version: int) -> dict:
    """由请求体生成待持久化的连接配置，并写入版本号。

    排除 id/password/name/enabled/version：密码单独进密钥库（write-only），名称与
    启用状态是实体列而不是配置项。``provider`` 冗余写入配置，便于配置包导出与
    历史排查时自解释。
    """
    values = payload.model_dump(by_alias=True, exclude={"id", "password", "name", "enabled", "version"})
    values["version"] = version
    values["provider"] = payload.type
    return values


def create_source(ctx, payload: SourceInput) -> SourceView:
    """创建分析数据源（POST /api/v1/analytics/datasources），需要 ``datasources.create``。

    副作用与顺序：校验目标地址 → 写入加密凭据（可选）→ 建记录 → 附加默认治理策略
    （并发/日限额）→ 提交变更审计 → **尽力而为**地自动同步一次元数据（失败只把状态
    标为 failed，不回滚已经安全保存的配置）。只读取远端结构，不写入用户数据库。
    抛出：指定了已有 ID 抛 ``ValidationError``；目标地址不合规抛 ``ValidationError``。
    """
    require_permission(ctx, "datasources.create")
    if payload.id:
        raise ValidationError("新建数据源不能指定已有 ID")
    row = DataSource(id=new_id(), workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id,
                     name=payload.name, provider=payload.type, connection_config=source_configuration(payload, 1),
                     status="active" if payload.enabled else "disabled", visibility="workspace",
                     created_at=datetime.utcnow(), updated_at=datetime.utcnow())
    validate_destination(row)
    if payload.password:
        secret = create_secret(ctx.db, workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id,
                               name="datasource-" + row.id, value=payload.password, provider="db_password")
        row.secret_id = secret.id
    ctx.db.add(row)
    from app.modules.analytics.governance import add_default_policy
    add_default_policy(ctx, row)
    commit_change(ctx, "datasource.create", "datasources", row.id)
    _automatic_metadata_sync(ctx, row)
    ctx.db.refresh(row)
    return source_view(row)


def update_source(ctx, source_id: str, payload: SourceInput) -> SourceView:
    """按版本编辑数据源（PUT /api/v1/analytics/datasources/{source_id}）。

    权限：``datasources.edit`` + 该资源的数据范围。并发控制：必须提交读取到的
    ``version``，新配置写入 version+1；随后用条件 UPDATE（匹配旧版本号）提交，
    rowcount 不为 1 说明期间被他人改过，返回 409 而不是覆盖。密码留空表示保留原
    加密凭据（不会把密码清空）。

    副作用：写入配置与（可选）新凭据、退役该数据源的旧连接池、提交审计、尝试重新
    同步元数据（失败仅标记状态）；最后按数据源启用状态协调健康检查调度。
    """
    row = get_source(ctx, source_id, "datasources.edit")
    if payload.id not in (None, source_id):
        raise ValidationError("请求中的数据源 ID 与路径不一致")
    current_version = source_version(row)
    if payload.version != current_version:
        raise ConflictError("数据源配置已修改，请刷新后重试", current_version=current_version)
    candidate = DataSource(workspace_id=ctx.workspace_id, provider=payload.type,
                           connection_config=source_configuration(payload, current_version + 1))
    validate_destination(candidate)
    new_secret_id = row.secret_id
    if payload.password:
        new_secret_id = create_secret(ctx.db, workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id,
                                     name="datasource-" + row.id, value=payload.password, provider="db_password").id
    condition = DataSource.connection_config["version"].as_integer() == current_version
    if current_version == 1:
        condition = or_(condition, DataSource.connection_config["version"].as_integer().is_(None))
    result = ctx.db.execute(update(DataSource).where(
        DataSource.id == source_id, DataSource.workspace_id == ctx.workspace_id, condition,
    ).values(name=payload.name, provider=payload.type, connection_config=candidate.connection_config,
             secret_id=new_secret_id, status="active" if payload.enabled else "disabled",
             updated_at=datetime.utcnow()).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        ctx.db.rollback()
        raise ConflictError("数据源被其他操作修改，请刷新后重试")
    commit_change(ctx, "datasource.update", "datasources", source_id)
    from app.modules.analytics.connection_pool import dispose_source_pools
    dispose_source_pools(ctx.workspace_id, source_id, minimum_version=current_version + 1)
    ctx.db.refresh(row)
    from app.modules.analytics.governance import reconcile_source_schedule
    reconcile_source_schedule(ctx, row)
    _automatic_metadata_sync(ctx, row)
    ctx.db.refresh(row)
    return source_view(row)


def remove_source(ctx, source_id: str) -> None:
    """Soft removal retains query provenance, encrypted references and old files."""
    row = get_source(ctx, source_id, "datasources.delete")
    row.status = "deleted"
    row.connection_config = {**row.connection_config, "version": source_version(row) + 1}
    row.updated_at = datetime.utcnow()
    commit_change(ctx, "datasource.delete", "datasources", source_id, {"retained_history": True})
    from app.modules.analytics.connection_pool import dispose_source_pools
    dispose_source_pools(ctx.workspace_id, source_id, minimum_version=source_version(row))
    from app.modules.analytics.governance import reconcile_source_schedule
    reconcile_source_schedule(ctx, row)


def test_source(ctx, payload: SourceInput | None = None, *, source_id: str | None = None) -> dict:
    """执行真实只读连接测试（POST /api/v1/analytics/datasources/test）。

    权限：``datasources.execute``；若提交的是**改动过的连接参数**（payload 带 id），
    还会额外要求 ``datasources.edit``——测试未保存的配置等于改用另一套连接，不能用
    执行权限绕过编辑权限。新建场景（无 id）改为要求 ``datasources.create``。

    返回 ``{ok,status,detail,provider}``；失败时抛脱敏后的 ``ToolExecutionError``，
    不回传驱动原始错误中的凭据或连接串。副作用：打开一次只读连接（SQL 执行
    ``SELECT 1``，ES 请求根路径），不写任何数据、不落库。
    """
    require_permission(ctx, "datasources.execute")
    password_override = None
    if source_id or (payload and payload.id):
        row = get_source(ctx, source_id or payload.id, "datasources.execute")
        if payload:
            # Testing changed connection parameters requires configuration edit rights.
            # 目标是“用新参数试连”，因此除执行权限外还必须具备编辑权限。
            get_source(ctx, row.id, "datasources.edit")
            row = DataSource(id=row.id, workspace_id=row.workspace_id, provider=payload.type,
                             connection_config=source_configuration(payload, source_version(row)), secret_id=row.secret_id)
            password_override = payload.password or None
    elif payload:
        require_permission(ctx, "datasources.create")
        row = DataSource(workspace_id=ctx.workspace_id, provider=payload.type,
                         connection_config=source_configuration(payload, 1))
        password_override = payload.password or ""
    else:
        raise ValidationError("请提供数据源或连接配置")
    try:
        if row.provider == "elasticsearch":
            with elastic_client(ctx.db, row, password_override=password_override) as client:
                response = client.get("/")
                response.raise_for_status()
                response.json()
        else:
            with readonly_connection(ctx.db, row, password_override=password_override) as connection:
                connection.exec_driver_sql("SELECT 1").fetchone()
    except AppError:
        raise
    except Exception as exc:
        raise connection_failure(row, exc) from exc
    return {"ok": True, "status": "healthy", "detail": "只读连接测试成功", "provider": row.provider}
