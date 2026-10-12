# -*- coding: utf-8 -*-
"""Data source service (MySQL/Postgres/http demo sources)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...errors import NotFoundError, ToolExecutionError
from ...ids import new_id
from ...models import DataSource
from ..secret.service import create_secret


def create_ds(db: Session, *, workspace_id: str, owner_user_id: str, name: str,
              provider: str, connection_config: dict, password: str | None = None) -> DataSource:
    """旧版建库函数：密码进密钥库（write-only），配置里不保留明文口令。

    与新版 sources.create_source 的差别：这里不做权限判定、不附加治理策略、不触发
    元数据同步，因此仅用于兼容旧调用方；新代码请走 analytics.sources。
    副作用：写入一条 DataSource（flush 但**不 commit**，事务由调用方控制）。
    """
    secret_id = None
    if password:
        sk = create_secret(db, workspace_id=workspace_id, owner_user_id=owner_user_id,
                           name=f"ds-pwd-{name}", value=password, provider="db_password")
        secret_id = sk.id
    cfg = dict(connection_config)
    cfg.setdefault("provider", provider)
    row = DataSource(
        id=new_id(), workspace_id=workspace_id, owner_user_id=owner_user_id,
        name=name, provider=provider, connection_config=cfg, secret_id=secret_id,
        status="active", created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
        visibility="workspace",
    )
    db.add(row)
    db.flush()
    return row


def list_ds(db: Session, workspace_id: str):
    """列出本工作区数据源（按创建时间倒序）；只做数据库过滤，不含权限与数据范围判定。"""
    return db.execute(select(DataSource).where(DataSource.workspace_id == workspace_id)
                      .order_by(DataSource.created_at.desc())).scalars().all()


def get_ds(db: Session, workspace_id: str, ds_id: str) -> DataSource:
    """按工作区取单个数据源；跨工作区或不存在都抛 ``NotFoundError``（不泄露是否存在）。"""
    row = db.get(DataSource, ds_id)
    if not row or row.workspace_id != workspace_id:
        raise NotFoundError("数据源不存在")
    return row


def delete_ds(db: Session, ds_id: str) -> None:
    """物理删除数据源记录（旧接口行为）；不删除外部数据库，也不清理密钥与历史。

    注意：新版接口使用软删除（保留查询追溯），因此本函数只服务于旧调用方。
    """
    row = db.get(DataSource, ds_id)
    if row:
        db.delete(row)
        db.commit()


def test_connection(db: Session, ds: DataSource) -> dict:
    """旧版连接测试：只对 mysql/postgres 尝试 `SELECT 1`，其余类型返回不支持。

    与新版 test_source 的差别（都属于已知局限，不要在新代码里照抄）：
      仅使用 pymysql（即使数据源是 postgres 也会走 MySQL 驱动），不发 postgres 分支；
      失败时把驱动原始异常文本直接返回，可能包含主机/库名等连接细节；
      不经过 validate_destination，也不区分超时与认证失败。
    返回 ``{ok, detail}``，不抛异常；只读，不写库。
    """
    cfg = ds.connection_config
    provider = ds.provider
    if provider in ("mysql", "postgres"):
        import pymysql
        password = cfg.get("password") or ""
        if ds.secret_id:
            from ..secret.service import decrypt
            try:
                password = decrypt(db, ds.secret_id)
            except Exception:
                pass
        try:
            conn = pymysql.connect(host=cfg.get("host", "127.0.0.1"), port=int(cfg.get("port") or 3306),
                                   user=cfg.get("username") or cfg.get("user") or "root",
                                   password=password, database=cfg.get("database") or None,
                                   charset="utf8mb4", connect_timeout=10)
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
            cur.close(); conn.close()
            return {"ok": True, "detail": "连接成功"}
        except Exception as exc:
            return {"ok": False, "detail": str(exc)}
    return {"ok": False, "detail": f"暂不支持测试 {provider} 类型"}
