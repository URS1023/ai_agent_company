"""Versioned fixed-target HTTP sources with encrypted, write-only credentials."""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import update

from app.common.authorization import require_permission
from app.common.pagination import page_records
from app.common.owned_records import visible_records
from app.errors import ConflictError, ValidationError
from app.ids import new_id
from app.models import DataSource
from app.modules.analytics.connections import source_version
from app.modules.analytics.sources import get_source
from app.modules.collection.schemas import HttpSourceInput, HttpSourceView
from app.modules.collection.transport import SECRET_REFERENCE, referenced_names
from app.modules.secret.service import create_secret, decrypt, get_active_secret
from app.modules.system.service_helpers import commit_change


def http_source(ctx, source_id: str, permission: str = "datasources.view", *, active: bool = False) -> DataSource:
    """Resolve only an HTTP resource within the current workspace/action scope."""
    return get_source(ctx, source_id, permission, active=active, provider="http")


def source_config(row: DataSource) -> HttpSourceInput:
    """Revalidate stored request configuration; credentials are loaded separately."""

    # 读取已保存配置时剔除 credentialNames（那是派生出的凭据名称列表，不是请求字段），
    # 再交给 Pydantic 重新校验：因此非法或被改坏的历史配置会在读取时立即暴露，
    # 而不是等到发请求才失败。
    config = {key: value for key, value in row.connection_config.items() if key != "credentialNames"}
    return HttpSourceInput(name=row.name, enabled=row.status == "active", **config)


def source_view(row: DataSource) -> HttpSourceView:
    """HTTP 数据源对外视图：只返回凭据**名称**与是否存在，绝不返回值本身。"""
    config = source_config(row).model_dump(by_alias=True, exclude={"credentials"})
    return HttpSourceView(**config, id=row.id, credentialNames=row.connection_config.get("credentialNames", []),
                          hasCredentials=bool(row.secret_id), createTime=row.created_at.isoformat(),
                          updateTime=row.updated_at.isoformat())


def list_sources(ctx, *, limit: int = 100, offset: int = 0, paged: bool = False) -> list[HttpSourceView] | dict:
    """列出当前可见的 HTTP 采集数据源（GET /api/v1/collection/sources）。

    权限：datasources.view 与数据范围；只返回 provider=http 且未删除的记录，
    不返回凭据值（仅名称）。``paged=True`` 返回分页字典，否则返回列表。
    """
    query = visible_records(ctx, DataSource, "datasources.view").where(
        DataSource.provider == "http", DataSource.status != "deleted",
    ).order_by(DataSource.created_at.desc(), DataSource.id)
    if paged:
        return page_records(ctx.db, query, limit=limit, offset=offset, view=source_view)
    return [source_view(row) for row in ctx.db.scalars(query.limit(limit).offset(offset))]


def credentials_for(ctx, row: DataSource) -> dict[str, str]:
    """Use a referenced secret without granting permission to inspect the vault."""
    if not row.secret_id:
        return {}
    secret = get_active_secret(ctx.db, row.secret_id)
    if not secret or secret.workspace_id != ctx.workspace_id or secret.provider != "http_credentials":
        raise ValidationError("HTTP 凭据不存在、已停用或不属于当前工作区，请重新配置")
    try:
        values = json.loads(decrypt(ctx.db, row.secret_id))
        if not isinstance(values, dict) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in values.items()):
            raise ValueError()
        return values
    except Exception as exc:
        raise ValidationError("HTTP 凭据无法解密，请重新配置") from exc


def validate_authority(ctx, payload: HttpSourceInput, previous: HttpSourceInput | None) -> None:
    """Privileged target and POST automation approval cannot survive an unsafe edit."""
    if payload.allow_private and (previous is None or not previous.allow_private or payload.url != previous.url):
        require_permission(ctx, "system.admin")
    if payload.allow_automatic and payload.method == "POST":
        fields = ("url", "method", "headers", "bodies", "allow_automatic")
        changed = previous is None or any(getattr(payload, key) != getattr(previous, key) for key in fields)
        if changed or payload.credentials is not None:
            require_permission(ctx, "datasources.publish")


def _configuration(ctx, payload: HttpSourceInput, row: DataSource | None, version: int) -> tuple[dict, str | None]:
    """由请求体生成待持久化配置与密钥 ID（凭据与公开配置严格分离）。

    规则：未提交 credentials 时沿用原凭据（避免编辑时误清空）；请求引用的凭据名必须
    都已配置；公开字段（请求头/请求体/说明等）中**不允许**出现凭据原文，只要发现
    明文即拒绝保存，防止凭据被写进可被导出的配置里。
    返回 ``(配置字典, secret_id)``；``secret_id`` 为 None 表示当前没有凭据。
    抛出：引用未配置凭据或公开配置含凭据原文时抛 ``ValidationError``。
    """
    credentials = payload.credentials
    if credentials is None:
        credentials = credentials_for(ctx, row) if row else {}
    references = referenced_names([payload.headers, payload.bodies])
    if references - credentials.keys():
        raise ValidationError("请求引用了尚未配置的凭据", missing_names=sorted(references - credentials.keys()))
    # Avoid inadvertently storing the credential itself in public fields too.
    public = payload.model_dump(by_alias=True, exclude={"credentials", "version"})
    def contains_secret(value):
        """递归检查公开配置里是否混入了凭据原文（含把凭据拼进较长文本的情况）。

        长度 >= 4 的凭据做子串匹配，短凭据只做全等匹配，避免把普通词误判为凭据。
        """
        if isinstance(value, str):
            text = SECRET_REFERENCE.sub("", value)
            return any(secret == text or (len(secret) >= 4 and secret in text) for secret in credentials.values())
        if isinstance(value, dict):
            return any(contains_secret(key) or contains_secret(child) for key, child in value.items())
        if isinstance(value, list):
            return any(contains_secret(child) for child in value)
        return False
    if contains_secret(public):
        raise ValidationError("公开配置包含凭据原文，请改为凭据引用后保存")
    secret_id = row.secret_id if row else None
    if payload.credentials is not None:
        secret_id = create_secret(ctx.db, workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id,
                                  name="HTTP 采集凭据", provider="http_credentials",
                                  value=json.dumps(credentials, ensure_ascii=False)).id if credentials else None
    config = payload.model_dump(by_alias=True, exclude={"credentials", "version", "name", "enabled"})
    return {**config, "version": version, "credentialNames": sorted(credentials)}, secret_id


def create_source(ctx, payload: HttpSourceInput) -> HttpSourceView:
    """创建 HTTP 采集配置（POST /api/v1/collection/sources），需要 ``datasources.create``。

    除创建权限外还会校验目标授权：内网/本机目标需要 ``system.admin``，
    POST 且允许自动调用需要 ``datasources.publish``（见 validate_authority）。
    本接口**不发送任何网络请求**，只保存配置；凭据加密入库并只回传名称。
    """
    require_permission(ctx, "datasources.create")
    validate_authority(ctx, payload, None)
    config, secret_id = _configuration(ctx, payload, None, 1)
    row = DataSource(id=new_id(), workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id,
                     name=payload.name, provider="http", connection_config=config, secret_id=secret_id,
                     status="active" if payload.enabled else "disabled", visibility="workspace",
                     created_at=datetime.utcnow(), updated_at=datetime.utcnow())
    ctx.db.add(row)
    commit_change(ctx, "collection.source.create", "datasources", row.id)
    return source_view(row)


def update_source(ctx, source_id: str, payload: HttpSourceInput) -> HttpSourceView:
    """按版本编辑 HTTP 采集配置（PUT /api/v1/collection/sources/{source_id}）。

    权限：``datasources.edit`` 与源数据范围，另外重新校验目标授权（改了地址或动到
    POST 自动调用配置会再次要求 system.admin / datasources.publish——授权不能靠
    一次确认长期延续）。并发：必须提交当前 version，写入 version+1；条件 UPDATE
    命中 0 行说明被他人改动，抛 409 而不覆盖。
    副作用：写配置与（可选）新凭据、提交审计；不发送网络请求。
    """
    row = http_source(ctx, source_id, "datasources.edit")
    version = source_version(row)
    if payload.version != version:
        raise ConflictError("HTTP 配置已变更，请重新加载后保存", current_version=version)
    validate_authority(ctx, payload, source_config(row))
    config, secret_id = _configuration(ctx, payload, row, version + 1)
    result = ctx.db.execute(update(DataSource).where(
        DataSource.id == row.id, DataSource.workspace_id == ctx.workspace_id,
        DataSource.connection_config["version"].as_integer() == version,
    ).values(name=payload.name, connection_config=config, secret_id=secret_id,
             status="active" if payload.enabled else "disabled", updated_at=datetime.utcnow())
        .execution_options(synchronize_session=False))
    if result.rowcount != 1:
        ctx.db.rollback()
        raise ConflictError("配置被其他操作修改，请重新加载")
    commit_change(ctx, "collection.source.update", "datasources", row.id, {"version": version + 1})
    ctx.db.refresh(row)
    return source_view(row)


def remove_source(ctx, source_id: str, version: int) -> None:
    """软删除 HTTP 采集配置（DELETE /api/v1/collection/sources/{source_id}）。

    权限：``datasources.delete`` + 源数据范围；必须提交当前 version，过期抛 409。
    **软删除**：只把状态置为 deleted 并推进版本，保留采集历史、运行记录与加密凭据；
    不会删除外部设备上的任何数据，也不撤销上游系统的账号。
    """
    row = http_source(ctx, source_id, "datasources.delete")
    if source_version(row) != version:
        raise ConflictError("配置已变更，请重新加载后删除")
    result = ctx.db.execute(update(DataSource).where(
        DataSource.id == row.id, DataSource.connection_config["version"].as_integer() == version,
    ).values(status="deleted", connection_config={**row.connection_config, "version": version + 1},
             updated_at=datetime.utcnow()).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        ctx.db.rollback()
        raise ConflictError("配置已变更，请重新加载后删除")
    commit_change(ctx, "collection.source.delete", "datasources", row.id, {"retained_history": True})
