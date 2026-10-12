"""只在来源版本仍有效时原子更新派生画像，禁止迟到任务覆盖连接与授权配置。"""
from __future__ import annotations

import json

from sqlalchemy import JSON, cast, func, or_, update

from app.errors import ConflictError, ValidationError
from app.models import DataSource


PROFILE_FIELDS = frozenset({
    "autoDescription", "metadataStatus", "tableCount", "businessTableCount", "fieldCount",
    "commentedTableCount", "commentedFieldCount", "semanticIndexStatus", "semanticIndexedTableCount",
    "businessDomains", "metadataSyncedAt", "metadataError",
})


def _profile_statement(source, profile, expected_version, dialect):
    """使用数据库当前 JSON 做字段补丁；条件 UPDATE 在 MySQL RR 下也是当前读。"""
    if set(profile) - PROFILE_FIELDS:
        raise ValidationError("元数据画像包含非派生配置字段")
    config = DataSource.connection_config
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import JSONB, array
        config = cast(config, JSONB)
        for key, value in profile.items():
            config = func.jsonb_set(config, array([key]), cast(json.dumps(value, ensure_ascii=False), JSONB))
        config = cast(config, JSON)
    elif profile:
        arguments = []
        for key, value in profile.items():
            arguments.extend(["$." + key, func.json_extract(json.dumps(value, ensure_ascii=False), "$")])
        config = func.json_set(config, *arguments)
    version = DataSource.connection_config["version"].as_integer()
    condition = version == expected_version
    if expected_version == 1:
        condition = or_(condition, version.is_(None))
    return update(DataSource).where(
        DataSource.id == source.id, DataSource.workspace_id == source.workspace_id,
        DataSource.status == "active", condition,
    ).values(connection_config=config).execution_options(synchronize_session=False)


def update_profile(db, source, profile, *, expected_version):
    """校验成功后才让同事务的结构/索引落盘；冲突时撤销整次迟到同步。"""
    statement = _profile_statement(source, profile, expected_version, db.get_bind().dialect.name)
    with db.no_autoflush:
        result = db.execute(statement)
    if result.rowcount != 1:
        db.rollback()
        raise ConflictError("数据源配置或启用状态已改变，本次同步结果已废弃，请重新同步")
    # 这里只读取已成功条件更新的行；不能用事先 refresh 接受新版本并继续旧同步。
    db.refresh(source, attribute_names=["connection_config"])
