"""Versioned additive initialization and fail-fast schema compatibility checks.

No table or user data is dropped. Existing missing columns require an explicit
migration instead of allowing a later request to fail with SQL syntax errors.

职责：启动期的结构初始化与兼容性校验——建缺失表、校验必需列、记录结构契约摘要。

系统位置：由 app.seed.bootstrap() 在 lifespan 启动阶段调用（app.main），是"服务可用"的前置条件；
本模块是唯一的建表入口，业务模块不得自行 create_all。

关键约束：
- 只做增量：只创建缺失的表/列所属的表，绝不 DROP 或 ALTER 既有列；缺列时直接拒绝启动，
  要求先跑显式迁移，避免运行期才暴露 SQL 语法错误。
- 结构契约摘要（_contract_checksum）来自 ORM 元数据：改了 models 但没同步 SCHEMA_VERSION 时
  启动会失败——这正是防止"模型与库不一致却照常对外服务"的闸门，因此升版必须同时改 SCHEMA_VERSION。
- 多进程/多监听并发启动时靠 MySQL GET_LOCK 串行化（SQLite/其他方言退化为进程内 RLock）。
- 这里所有的导入都在函数体内完成：models 分散在各业务模块，模块级导入会形成循环依赖。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from threading import RLock

from sqlalchemy import DateTime, String, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base

# 结构契约版本：任何会改变 ORM 表结构/列的改动都必须同时更新此值（见模块 docstring 约束）。
SCHEMA_VERSION = "20261004_33"
# 进程内兜底锁：非 MySQL 方言（如 SQLite 测试库）没有 GET_LOCK，用它避免同进程重复迁移。
_migration_lock = RLock()
RECEIPT_INDEX = "ix_run_events_receipt_key"


def validate_receipt_schema(connection):
    """回执唯一性是持久合同，列名齐全仍必须核验列类型与完整唯一索引。"""
    inspector = inspect(connection)
    column = next((item for item in inspector.get_columns("run_events") if item["name"] == "receipt_key"), None)
    index = next((item for item in inspector.get_indexes("run_events") if item["name"] == RECEIPT_INDEX), None)
    if (not column or not isinstance(column["type"], String) or column["type"].length != 64
            or column.get("nullable") is not True):
        raise RuntimeError("工具回执列结构不匹配，请先执行显式迁移")
    # PG 的普通索引会反射空 INCLUDE；只豁免这个精确默认值。
    if (not index or not index.get("unique") or index.get("column_names") != ["receipt_key"]
            or index.get("expressions")
            or any(value is not None and not (key == "postgresql_include" and value == [])
                   for key, value in (index.get("dialect_options") or {}).items())):
        raise RuntimeError("工具回执唯一索引不匹配，请先执行显式迁移")


class SchemaMigration(Base):
    """Immutable checksum of the ORM contract applied at each schema version.

    代表一条"该版本结构已应用到本库"的审计记录：版本号为主键，checksum 是当时 ORM 元数据的
    摘要。不变式：同一 version 只能有一行，重复应用同一版本且摘要一致时视为幂等成功。
    """

    __tablename__ = "schema_migrations"
    version: Mapped[str] = mapped_column(String(40), primary_key=True, comment="结构迁移版本")
    checksum: Mapped[str] = mapped_column(String(64), nullable=False, comment="表结构契约摘要")
    applied_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, comment="执行时间 UTC")


def _contract_checksum() -> str:
    """按表名排序汇总"表名 → [(列名, 列类型, 是否可空)]"，得到结构契约的 SHA-256 摘要。

    只取列名/类型/可空性这三项：索引与默认值变化不改变摘要，避免无关调整触发启动失败。
    """
    contract = {
        name: [(column.name, str(column.type), column.nullable) for column in table.columns]
        for name, table in sorted(Base.metadata.tables.items())
    }
    return hashlib.sha256(json.dumps(contract, sort_keys=True).encode("utf-8")).hexdigest()


def _register_models():
    """启动与显式迁移共用同一模型注册集合，保证版本摘要一致。"""
    # 在函数内导入各业务模块的 models：注册所有表到 Base.metadata（模块级导入会造成循环依赖）。
    from app.modules.system import models as system_models  # 注册业务域表。
    from app.modules.analytics import models as analytics_models
    from app.modules.reports import models as report_models
    from app.modules.workflows import models as workflow_models
    from app.modules.alerts import models as alert_models
    from app.modules.writeback import models as writeback_models
    from app.modules.quality import models as quality_models
    from app.modules.business_ai import models as business_ai_models
    from app.modules.knowledge_graph import models as knowledge_graph_models
    from app.modules.assets import models as asset_models
    from app.modules.documents import models as document_models
    from app.modules.applications import models as application_models
    from app.modules.system_tools import models as system_tools_models
    from app.modules.model import models as model_governance_models
    from app.modules.files import models as file_governance_models
    from app.modules.knowledge import models as knowledge_governance_models
    from app.modules.capabilities import models as capability_catalog_models
    from app.modules.chat import models as chat_context_models



def initialize_schema(engine: Engine | None = None) -> None:
    """在对外服务前创建缺失表并核验所有必需列与回执唯一约束。

    参数：engine 可选，缺省用 app.db 的全局 Engine（测试可注入独立库）。
    异常：RuntimeError —— 迁移锁超时、缺表/缺列、结构契约与迁移版本不一致、AUTO_MIGRATE=0
    且缺少版本记录；启动阶段直接抛出即"拒绝启动"，不要在此吞异常降级。
    副作用：建表、写 schema_migrations，并在 MySQL 上持有/释放 GET_LOCK。
    """
    # 显式迁移和离线契约只导入模型；实际启动才加载部署配置与默认引擎。
    from app.config import settings
    from app.db import get_engine

    _register_models()

    engine = engine or get_engine()
    checksum = _contract_checksum()
    # 锁名按目标库名派生：同一个 MySQL 实例上不同库互不阻塞（拿不到锁说明另一进程正在建表）。
    lock_name = "test5_schema_" + hashlib.sha256(str(engine.url.database).encode()).hexdigest()[:32]
    with _migration_lock, engine.connect() as connection:
        mysql_lock = False
        try:
            if engine.dialect.name == "mysql":
                acquired = connection.execute(text("SELECT GET_LOCK(:name, 30)"), {"name": lock_name}).scalar()
                if acquired != 1:
                    raise RuntimeError("数据库结构迁移正在执行，获取迁移锁超时")
                mysql_lock = True
            if settings.AUTO_MIGRATE:
                Base.metadata.create_all(connection)
            inspector = inspect(connection)
            existing = set(inspector.get_table_names())
            # 缺表按 "表.*" 记录、缺列按 "表.列" 记录，一次性汇总后报错，便于人工一次补齐。
            missing = []
            for table in Base.metadata.sorted_tables:
                if table.name not in existing:
                    missing.append(table.name + ".*")
                    continue
                columns = {item["name"] for item in inspector.get_columns(table.name)}
                missing.extend(table.name + "." + item.name for item in table.columns if item.name not in columns)
            if missing:
                raise RuntimeError("数据库结构不匹配，请先执行迁移：" + ", ".join(missing))
            validate_receipt_schema(connection)
            row = connection.execute(
                select(SchemaMigration.__table__).where(SchemaMigration.version == SCHEMA_VERSION),
            ).mappings().first()
            # 同版本但摘要不同：说明 ORM 改了却没升版本号，拒绝启动而不是带病对外服务。
            if row and row["checksum"] != checksum:
                raise RuntimeError("模型结构已改变但迁移版本未更新，拒绝启动不一致的接口")
            if not row:
                if not settings.AUTO_MIGRATE:
                    raise RuntimeError("当前数据库缺少迁移版本，请先显式运行结构迁移")
                connection.execute(SchemaMigration.__table__.insert().values(
                    version=SCHEMA_VERSION, checksum=checksum, applied_at=datetime.utcnow(),
                ))
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            # 必须先释放 GET_LOCK 再提交：连接归还池后锁不会自动释放。
            if mysql_lock:
                connection.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": lock_name})
                connection.commit()
