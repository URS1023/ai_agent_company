# -*- coding: utf-8 -*-
"""SQLAlchemy engine + scoped session factory.

职责：创建全局唯一的 SQLAlchemy Engine / sessionmaker，并提供请求级事务上下文。

系统位置：FastAPI 依赖（app.common.deps）与后台 worker 都从这里取会话；Engine 在模块
导入时创建，因此本模块不应被循环导入（它只依赖 app.config）。

约束：默认 autoflush=False、expire_on_commit=False —— 提交后 ORM 对象仍可读，服务层因此
需要自己判断字段是否已过期；MySQL 侧连接池 30 分钟回收（pool_recycle）以避开服务端
wait_timeout，SQLite 仅用于本地/测试。连接建立时统一 SET NAMES utf8mb4 并打开外键约束，
新增长连接池参数时需保持这两条连接级设置。
"""
from __future__ import annotations

import contextlib
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .config import settings

# 引擎参数按后端类型分支：SQLite（内存库）需要 StaticPool 才能让多连接共享同一份内存数据。
_database_url = make_url(settings.DATABASE_URL)
_engine_options = {"pool_pre_ping": True, "echo": False, "future": True}
if _database_url.get_backend_name() == "sqlite":
    _engine_options["connect_args"] = {"check_same_thread": False}
    if _database_url.database in (None, "", ":memory:"):
        _engine_options["poolclass"] = StaticPool
else:
    _engine_options.update(pool_recycle=1800, pool_size=10, max_overflow=20)
_engine = create_engine(_database_url, **_engine_options)


@event.listens_for(_engine, "connect")
def _set_utf8(dbapi_connection, connection_record):
    """连接级初始化：MySQL 强制 utf8mb4，SQLite 打开外键约束。

    约束：必须在每条新连接上执行（池复用下由 connect 事件保证），否则中文/emoji 会截断，
    或 SQLite 上的级联删除静默失效。
    """
    cursor = dbapi_connection.cursor()
    try:
        if _engine.dialect.name == "mysql":
            cursor.execute("SET NAMES utf8mb4")
        elif _engine.dialect.name == "sqlite":
            cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


_SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False, future=True)


def get_engine():
    """返回进程内共享的 Engine（供 schema 初始化与健康检查使用）。"""
    return _engine


def session_factory() -> sessionmaker:
    """返回 sessionmaker 本身，供需要自行管理会话生命周期的后台任务使用。"""
    return _SessionLocal


def new_session() -> Session:
    """新建一个会话，调用方负责 commit/rollback/close（不自动回收）。"""
    return _SessionLocal()


@contextlib.contextmanager
def session_scope() -> Iterator[Session]:
    """Provide a transactional scope around a series of operations.

    用途：后台任务/脚本里的"一次事务"写法；正常退出自动 commit。
    异常：块内任何异常都会先 rollback 再原样抛出，不做吞异常或重试。
    副作用：写库（提交事务）；会话在退出时一定关闭，不能跨 yield 之外复用。
    """
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
