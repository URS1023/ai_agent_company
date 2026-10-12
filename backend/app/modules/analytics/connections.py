"""Bounded read-only adapters for explicitly configured analysis data sources."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

import httpx
from sqlalchemy import create_engine, event
from sqlalchemy.engine import URL
from sqlalchemy.pool import NullPool, QueuePool

from app.config import settings
from app.errors import ToolExecutionError, ValidationError
from app.models import DataSource, Secret
from app.modules.secret.service import decrypt
from app.modules.analytics.connection_pool import (
    POOL_SIZE, borrow_read_connection, pooled_engine,
)

SQL_PROVIDERS = {"mysql", "postgres", "sqlite"}
DEFAULT_PORTS = {"mysql": 3306, "postgres": 5432, "elasticsearch": 9200}


def source_version(source: DataSource) -> int:
    """读取数据源配置版本；早期记录没有该字段，按 1 处理（保证版本比较可进行）。"""
    return int((source.connection_config or {}).get("version") or 1)


def database_name(source: DataSource) -> str:
    """读取库名/受管 SQLite 文件名/ES 索引模式；兼容旧键 database 与 databaseName。"""
    config = source.connection_config or {}
    return str(config.get("databaseName") or config.get("database") or "")


def managed_database_path(workspace_id: str, filename: str, *, must_exist: bool = True) -> Path:
    """Never open an arbitrary server file or the platform's own SQLite database."""

    # 只允许访问 STORAGE_ROOT/analytics-databases/<workspace_id>/ 下的受管 SQLite 文件：
    # 工作区标识与文件名都按白名单正则校验（扩展名限 sqlite3/sqlite/db），再用
    # resolve + is_relative_to 阻断 .. 与符号链接逃逸。因此导入的 CSV/XLSX 生成的数据
    # 文件与平台自身库都无法被当作数据源打开。
    # 抛出：工作区/文件名不合法、路径越界或文件不存在时抛 ValidationError。
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,26}", workspace_id):
        raise ValidationError("无效的工作区标识")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,120}\.(sqlite3|sqlite|db)", filename):
        raise ValidationError("只能选择当前工作区已导入的受管 SQLite 数据文件")
    root = (Path(settings.STORAGE_ROOT) / "analytics-databases" / workspace_id).resolve()
    path = (root / filename).resolve()
    if not path.is_relative_to(root):
        raise ValidationError("数据文件不在当前工作区目录内")
    if must_exist and not path.is_file():
        raise ValidationError("受管数据文件不存在，请先从数据源页面导入 CSV、XLSX 或 SQLite 数据")
    return path


def connection_password(db, source: DataSource) -> str:
    """Credentials are only decrypted after the caller authorizes the source."""
    if not source.secret_id:
        # Old plaintext connection configs are deliberately not forwarded.
        if (source.connection_config or {}).get("password"):
            raise ValidationError("旧连接包含明文密码，请在数据源编辑页重新安全保存")
        return ""
    secret = db.get(Secret, source.secret_id)
    if secret is None or secret.workspace_id != source.workspace_id:
        raise ValidationError("数据源凭据不存在或不属于当前工作区")
    return decrypt(db, source.secret_id)


def validate_destination(source: DataSource) -> None:
    """A data connection must not expose platform identities, sessions or secrets."""
    config = source.connection_config or {}
    name = database_name(source)
    if source.provider == "sqlite":
        managed_database_path(source.workspace_id, name)
        return
    host = str(config.get("host") or "").strip()
    if not host or any(part in host for part in ("/", "\\", "@", "?", "#", "\0")):
        raise ValidationError("主机地址格式不正确")
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        if not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", host):
            raise ValidationError("主机名格式不正确")
        if host.lower() in {"metadata.google.internal", "metadata.azure.internal"}:
            raise ValidationError("不能将基础设施元数据服务配置为数据源")
    else:
        if address.is_link_local or address.is_multicast or address.is_unspecified:
            raise ValidationError("不能连接链路本地元数据、广播或未指定地址")
    if source.provider in {"mysql", "postgres"}:
        forbidden = {"information_schema", "mysql", "performance_schema", "sys", "pg_catalog", settings.PLATFORM_DB.lower()}
        if not name or name.lower() in forbidden:
            raise ValidationError("请选择业务数据库，不能开放平台库或数据库系统库")


def connection_failure(source: DataSource, exc: Exception) -> ToolExecutionError:
    """返回脱敏的数据源故障，并保留明确来源供宿主停止后续源访问。"""
    kind = type(exc).__name__
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)) or "timeout" in str(exc).lower() or "timed out" in str(exc).lower():
        detail = "连接或查询超时，请检查网络、查询范围与超时设置"
    else:
        detail = "数据连接或只读查询失败，请检查账号权限、数据库结构和服务状态"
    return ToolExecutionError(detail, provider=source.provider, error_type=kind, failure_domain="datasource")


@contextmanager
def sql_engine(db, source: DataSource, *, password_override: str | None = None, write: bool = False):
    """按配置版本复用已保存只读源；写入、预览及 SQLite 继续使用短连接。"""
    if source.provider not in SQL_PROVIDERS:
        raise ValidationError("该数据源不使用关系数据库驱动")
    validate_destination(source)
    config = source.connection_config or {}
    timeout = max(1, min(60, int(config.get("timeoutSeconds") or 15)))
    password = connection_password(db, source) if password_override is None else password_override
    if source.provider == "sqlite":
        path = managed_database_path(source.workspace_id, database_name(source))
        url = URL.create("sqlite", database="file:" + path.as_posix(), query={"mode": "rw" if write else "ro", "uri": "true"})
        connect_args = {"check_same_thread": False, "timeout": min(timeout, 10)}
    else:
        url = URL.create(
            "mysql+pymysql" if source.provider == "mysql" else "postgresql+psycopg",
            username=str(config.get("username") or config.get("user") or ""),
            password=password, host=config.get("host"),
            port=int(config.get("port") or DEFAULT_PORTS[source.provider]),
            database=database_name(source),
        )
        if source.provider == "mysql":
            connect_args = {"connect_timeout": min(timeout, 10), "read_timeout": timeout, "write_timeout": timeout,
                            "local_infile": False, "charset": "utf8mb4"}
        else:
            connect_args = {"connect_timeout": min(timeout, 10),
                            "options": f"-c statement_timeout={timeout * 1000} -c lock_timeout={timeout * 1000} -c default_transaction_read_only={'off' if write else 'on'}"}
    if reusable_source(source, password_override=password_override) and not write:
        fingerprint = hashlib.sha256(json.dumps(
            [url.render_as_string(hide_password=False), connect_args, source_version(source), source.secret_id],
            sort_keys=True, default=str,
        ).encode()).hexdigest()
        key = (source.workspace_id, source.id, fingerprint)

        def factory():
            """创建该数据源的连接池引擎（每个配置版本一个池，见 connection_pool 的键规则）。"""
            # 池参数即资源上限：最多 POOL_SIZE 条常驻连接（max_overflow=0 不临时超发）、
            # 等待借用最多 min(timeout,5) 秒、连接 300 秒回收、借出前 ping，
            # 归还时 rollback 清掉未提交的会话状态（只读场景下主要是锁与临时表）。
            return create_engine(
                url, poolclass=QueuePool, pool_size=POOL_SIZE, max_overflow=0,
                pool_timeout=min(timeout, 5), pool_recycle=300, pool_pre_ping=True,
                pool_use_lifo=True, pool_reset_on_return="rollback",
                connect_args=connect_args, hide_parameters=True,
            )
        with pooled_engine(key, factory, version=source_version(source)) as engine:
            yield engine
        return
    engine = create_engine(url, poolclass=NullPool, connect_args=connect_args, hide_parameters=True)
    if source.provider == "sqlite":
        @event.listens_for(engine, "connect")
        def configure_sqlite(dbapi_connection, _record):
            """SQLite 每条新连接的兜底设置；必须在连接建立时执行而不是执行语句前。

            ``query_only=ON`` 让只读语义下沉到数据库；关闭扩展加载，阻断
            load_extension 之类的逃逸路径。写入模式（导入 CSV/XLSX 建库）例外。
            """
            dbapi_connection.execute("PRAGMA foreign_keys=ON")
            if not write:
                dbapi_connection.execute("PRAGMA query_only=ON")
            dbapi_connection.enable_load_extension(False)
    try:
        yield engine
    finally:
        engine.dispose()


@contextmanager
def readonly_connection(db, source: DataSource, *, password_override: str | None = None):
    """补充数据库级只读事务；建立连接后取消也不能继续派发初始化语句。"""

    # 在 SQL AST 白名单之外再加一层数据库级只读约束（纵深防御）：
    #   MySQL：SET TRANSACTION READ ONLY + 只读事务 + 会话级 MAX_EXECUTION_TIME
    #          （MariaDB 用 max_statement_time）；
    #   Postgres：只读事务 + statement_timeout/lock_timeout + default_transaction_read_only；
    #   SQLite：由引擎层 PRAGMA query_only 与 authorizer 保证。
    # 任何异常（包括 SQL 报错）都会 invalidate 掉连接，绝不让出错会话状态流入下一次读取；
    # 退出路径统一 rollback：只读用途正常结束也不提交。
    from app.modules.runtime.task_control import checkpoint
    checkpoint()
    with sql_engine(db, source, password_override=password_override) as engine:
        checkpoint()
        with borrow_read_connection(engine, reusable=reusable_source(source, password_override=password_override)) as connection:
            try:
                checkpoint()
                if source.provider == "mysql":
                    connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                    checkpoint()
                    connection.exec_driver_sql("START TRANSACTION READ ONLY")
                    checkpoint()
                    timeout = max(1, min(60, int((source.connection_config or {}).get("timeoutSeconds") or 15)))
                    if getattr(engine.dialect, "is_mariadb", False):
                        connection.exec_driver_sql(f"SET SESSION max_statement_time={timeout}")
                    else:
                        connection.exec_driver_sql(f"SET SESSION MAX_EXECUTION_TIME={timeout * 1000}")
                elif source.provider == "postgres":
                    connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                checkpoint()
                yield connection
                connection.rollback()
            except BaseException:
                # Discard even SQL errors: session state must never reach another read.
                connection.invalidate()
                raise


def reusable_source(source, *, password_override=None):
    """判断能否复用进程内的连接池：仅 MySQL/Postgres 的已启用持久化数据源可复用。

    SQLite 与临时凭据（password_override 不为空，例如连接测试）必须使用短连接，
    避免把一次性连接留在池里。
    """
    return (source.provider in {"mysql", "postgres"} and bool(source.id)
            and source.status == "active" and password_override is None)


@contextmanager
def elastic_client(db, source: DataSource, *, password_override: str | None = None):
    """Elasticsearch access is constrained to server-selected read endpoints."""

    # 客户端只用于服务端选定的只读端点（_sql/_mapping 等）：
    # follow_redirects=False 防止被 302 引到内网其他服务；trust_env=False 避免继承
    # 宿主机的代理与凭据环境变量而绕过 validate_destination 的地址判定。
    validate_destination(source)
    config = source.connection_config or {}
    password = connection_password(db, source) if password_override is None else password_override
    username = str(config.get("username") or "")
    host = str(config["host"])
    if ":" in host and not host.startswith("["):
        host = "[" + host + "]"
    endpoint = ("https" if config.get("ssl") else "http") + f"://{host}:{int(config.get('port') or 9200)}"
    timeout = max(1, min(60, int(config.get("timeoutSeconds") or 15)))
    with httpx.Client(base_url=endpoint, timeout=timeout, follow_redirects=False,
                      auth=(username, password) if username else None, trust_env=False) as client:
        yield client


def elastic_index_pattern(source: DataSource) -> str:
    """校验并转义 Elasticsearch 索引/模式，禁止隐藏索引（``.``/``_`` 开头）与通配越权。

    允许 ``*``、``?``、``-`` 三种 Elasticsearch 语义字符（因此支持 ``logs-*``），
    其余字符必须落在白名单内，最后按 URL 路径规则做引号化，避免拼出越界路径。
    """
    value = database_name(source) or "*"
    if not re.fullmatch(r"[a-z0-9_*?-]{1,200}", value) or value.startswith((".", "_")):
        raise ValidationError("索引范围格式不正确；不能访问隐藏或系统索引")
    return quote(value, safe="*?-")
