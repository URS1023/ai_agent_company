"""Credential-safe, atomic import and export of governed data-source configs."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.common.audit import audit
from app.common.authorization import require_permission
from app.common.owned_records import visible_records
from app.errors import ConflictError, ValidationError
from app.ids import new_id
from app.models import DataSource
from app.modules.analytics.connections import DEFAULT_PORTS, database_name, source_version, validate_destination
from app.modules.analytics.governance import add_default_policy
from app.modules.analytics.schemas import DataSourceConfigurationImport, SourceInput


def _portable_item(row: DataSource) -> dict:
    """把一个数据源转成可迁移的配置项；显式排除 ID、凭据与元数据样本。

    ``requiresCredential`` 只说明“源配置曾使用加密凭据”，导出文件里**不含**凭据值，
    因此导入到新环境时这类连接会被自动保持停用，需要人工重新配置密码。
    """
    config = row.connection_config or {}
    return {
        "name": row.name,
        "type": row.provider,
        "host": str(config.get("host") or ""),
        "port": config.get("port") or DEFAULT_PORTS.get(row.provider),
        "databaseName": database_name(row),
        "username": str(config.get("username") or config.get("user") or ""),
        "description": str(config.get("description") or ""),
        "enabled": row.status == "active",
        "requiresCredential": bool(row.secret_id),
        "ssl": bool(config.get("ssl")),
        "allowedTables": list(config.get("allowedTables") or []),
        "allowedColumns": dict(config.get("allowedColumns") or {}),
        "timeoutSeconds": int(config.get("timeoutSeconds") or 15),
        "maxRows": int(config.get("maxRows") or 1000),
    }


def export_configurations(ctx) -> dict:
    """Export only currently visible configs; IDs, secrets and metadata samples stay local."""
    require_permission(ctx, "datasources.view")
    rows = ctx.db.scalars(visible_records(ctx, DataSource, "datasources.view").where(
        DataSource.status != "deleted",
        DataSource.provider.in_(["mysql", "postgres", "sqlite", "elasticsearch"]),
    ).order_by(DataSource.created_at, DataSource.id)).all()
    return {
        "formatVersion": 1,
        "exportedAt": datetime.utcnow().isoformat(),
        "items": [_portable_item(row) for row in rows],
    }


def _input(item, *, name: str, enabled: bool, version: int | None = None) -> SourceInput:
    """由配置包条目构造校验用入参；密码字段刻意不传（导入从不携带凭据）。"""
    return SourceInput(
        name=name,
        type=item.type,
        host=item.host,
        port=item.port,
        databaseName=item.databaseName,
        username=item.username,
        description=item.description,
        enabled=enabled,
        version=version,
        ssl=item.ssl,
        allowedTables=item.allowedTables,
        allowedColumns=item.allowedColumns,
        timeoutSeconds=item.timeoutSeconds,
        maxRows=item.maxRows,
    )


def _renamed(base: str, used: set[str]) -> str:
    """同名冲突时生成“名称（导入 N）”式的可用名称；上限 1000 次后抛 ``ConflictError``。

    用 casefold 比较（不是直接小写）以覆盖大小写不敏感的重名判断。
    """
    for number in range(2, 1002):
        suffix = f"（导入 {number}）"
        candidate = base[: 200 - len(suffix)] + suffix
        if candidate.casefold() not in used:
            return candidate
    raise ConflictError("同名数据源过多，请先整理现有配置")


def import_configurations(ctx, bundle: DataSourceConfigurationImport) -> dict:
    """Validate the whole package first, then apply all changes in one transaction."""
    require_permission(ctx, "datasources.create")
    if bundle.conflictMode == "update":
        require_permission(ctx, "datasources.edit")
    from app.modules.analytics import sources

    existing_rows = ctx.db.scalars(visible_records(ctx, DataSource, "datasources.view").where(
        DataSource.status != "deleted",
        DataSource.provider.in_(["mysql", "postgres", "sqlite", "elasticsearch"]),
    ).order_by(DataSource.created_at.desc())).all()
    by_name: dict[str, DataSource] = {}
    for row in existing_rows:
        by_name.setdefault(row.name.casefold(), row)
    used = set(by_name)
    plan: list[dict] = []

    for item in bundle.items:
        requested_name = item.name.strip()
        existing = by_name.get(requested_name.casefold())
        if existing and bundle.conflictMode == "skip":
            plan.append({"action": "skipped", "name": existing.name, "source": existing, "item": item})
            continue
        if existing and bundle.conflictMode == "rename":
            name = _renamed(requested_name, used)
            existing = None
            action = "created"
            renamed = True
        elif existing:
            name = existing.name
            action = "updated"
            renamed = False
        else:
            name = requested_name
            action = "created"
            renamed = False
        used.add(name.casefold())

        has_destination_credential = bool(existing and existing.secret_id)
        enabled = bool(item.enabled and (not item.requiresCredential or has_destination_credential))
        payload = _input(
            item, name=name, enabled=enabled,
            version=source_version(existing) if existing else None,
        )
        candidate = DataSource(
            workspace_id=ctx.workspace_id,
            provider=payload.type,
            connection_config=sources.source_configuration(
                payload, source_version(existing) + 1 if existing else 1,
            ),
        )
        validate_destination(candidate)
        plan.append({
            "action": action,
            "name": name,
            "source": existing,
            "item": item,
            "payload": payload,
            "candidate": candidate,
            "renamed": renamed,
            "disabledForCredential": bool(item.enabled and not enabled),
        })

    result_items = [{
        "action": entry["action"],
        "id": entry["source"].id if entry.get("source") else "",
        "name": entry["name"],
        "renamed": bool(entry.get("renamed")),
        "disabledForCredential": bool(entry.get("disabledForCredential")),
    } for entry in plan]
    summary = {
        "dryRun": bundle.dryRun,
        "created": sum(item["action"] == "created" for item in plan),
        "updated": sum(item["action"] == "updated" for item in plan),
        "skipped": sum(item["action"] == "skipped" for item in plan),
        "renamed": sum(bool(item.get("renamed")) for item in plan),
        "disabledForCredential": sum(bool(item.get("disabledForCredential")) for item in plan),
        "items": result_items,
    }
    if bundle.dryRun:
        return summary

    sync_after_commit: list[DataSource] = []
    try:
        for index, entry in enumerate(plan):
            if entry["action"] == "skipped":
                continue
            payload = entry["payload"]
            if entry["action"] == "created":
                row = DataSource(
                    id=new_id(),
                    workspace_id=ctx.workspace_id,
                    owner_user_id=ctx.user_id,
                    name=entry["name"],
                    provider=payload.type,
                    connection_config=entry["candidate"].connection_config,
                    secret_id=None,
                    status="active" if payload.enabled else "disabled",
                    visibility="workspace",
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
                ctx.db.add(row)
                add_default_policy(ctx, row)
                entry["source"] = row
            else:
                row = sources.get_source(ctx, entry["source"].id, "datasources.edit")
                ctx.db.execute(select(DataSource.id).where(
                    DataSource.id == row.id,
                    DataSource.workspace_id == ctx.workspace_id,
                ).with_for_update()).scalar_one()
                row.name = entry["name"]
                row.provider = payload.type
                row.connection_config = entry["candidate"].connection_config
                row.status = "active" if payload.enabled else "disabled"
                row.updated_at = datetime.utcnow()
            audit(
                ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
                action="datasource.config.import." + entry["action"],
                resource_type="datasources", resource_id=row.id,
                metadata={
                    "format_version": bundle.formatVersion,
                    "credential_imported": False,
                    "disabled_for_credential": bool(entry.get("disabledForCredential")),
                },
            )
            result_items[index]["id"] = row.id
            if row.status == "active":
                sync_after_commit.append(row)
        ctx.db.commit()
    except IntegrityError as exc:
        ctx.db.rollback()
        raise ConflictError("配置包与现有数据源编码或关联冲突，未导入任何配置") from exc
    except Exception:
        ctx.db.rollback()
        raise

    # Configuration mutation is already atomic. Metadata probes happen only
    # afterwards so one unavailable external system cannot roll back the batch.
    for row in sync_after_commit:
        sources._automatic_metadata_sync(ctx, row)
    summary["items"] = result_items
    return summary
