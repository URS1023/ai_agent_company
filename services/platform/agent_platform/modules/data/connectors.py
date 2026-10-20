"""精提Test5只读连接边界：显式目标、凭据引用、只读事务与有限计划执行。

本模块不会读取原系统配置；默认关闭外部连接。测试通过注入HTTP传输或驱动
工厂验证协议，不需要也不得连接已有业务数据库。
"""
from contextlib import closing, contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
import ipaddress
import json
import math
import re
import sqlite3
import ssl
from time import monotonic

import httpx

from agent_platform.core.errors import DomainError
from .sql_safety import driver_query

DEFAULT_PORTS = {"postgres": 5432, "mysql": 3306, "http_json": 443, "elasticsearch": 9200}


def config_target(provider, config):
    """校验连接字段，不允许系统库、基础设施地址或通过路径夹带凭据。"""
    host = str(config.get("host", "")).strip()
    if not host or any(c in host for c in "/\\@?#\0\r\n"):
        raise DomainError(422, "source_host_invalid", "请填写有效主机地址")
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        if not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", host) or host.lower() in {"metadata.google.internal", "metadata.azure.internal"}:
            raise DomainError(422, "source_host_invalid", "主机地址不可用")
    else:
        if address.is_link_local or address.is_multicast or address.is_unspecified:
            raise DomainError(422, "source_host_invalid", "主机地址不可用")
    port = config.get("port") or (80 if provider == "http_json" and not config.get("tls", True) else DEFAULT_PORTS[provider])
    database = str(config.get("database", ""))
    if provider == 'elasticsearch' and (not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{0,159}', database)
                                       or database.startswith(('.', '_'))):
        raise DomainError(422, 'es_index_invalid', '请选择单个非隐藏索引，不使用通配或跨集群名称')
    if provider in ("postgres", "mysql"):
        if not database or database.lower() in {"mysql", "information_schema", "performance_schema", "sys", "pg_catalog", "postgres", "template0", "template1"}:
            raise DomainError(422, "source_database_invalid", "请选择独立业务数据库")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,99}", config.get("schema_name", "public")):
            raise DomainError(422, "source_schema_invalid", "数据库schema格式不正确")
    path = config.get("endpoint_path", "/data")
    if provider == "http_json" and (not path.startswith("/") or path.startswith("//") or any(c in path for c in ("?", "#", "\\", "\0")) or ".." in path.split("/")):
        raise DomainError(422, "source_endpoint_invalid", "HTTP数据端点路径无效")
    return {"host": host, "port": int(port), "database": database}


def require_approved_target(source, settings, scope="query"):
    """运行开关与精确目标批准同时满足才允许连接；回环地址不自动获得批准。"""
    target = config_target(source.provider, source.connection_config)
    if not settings or not settings.allow_remote_connections:
        raise DomainError(403, "source_connections_disabled", "当前环境尚未启用外部数据连接")
    approval = source.target_approval or {}
    try:
        expires = datetime.fromisoformat(approval.get("expires_at", "").replace("Z", "+00:00"))
        valid_time = expires.tzinfo is not None and expires > datetime.now(timezone.utc)
    except ValueError:
        valid_time = False
    if not valid_time or any(approval.get(k) != value for k, value in target.items()) or scope not in approval.get("scope", []) or not approval.get("service_owner"):
        raise DomainError(403, "source_target_not_approved", "请先核实该数据服务归属并批准本次连接范围")
    return target


def json_value(value):
    """提取Test5的精度保持策略，大整数和Decimal不转成浮点数。"""
    if isinstance(value, Decimal):
        return str(value) if value.is_finite() else None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return {"type": "binary", "size": len(value)}
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, int) and abs(value) > 9007199254740991:
        return str(value)
    return value


def plan_cost(plan):
    """只分析EXPLAIN输出，不执行ANALYZE；未知或过大计划默认拒绝。"""
    estimates = []
    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ("Plan Rows", "rows_examined_per_scan", "rows_produced_per_join", "rows") and isinstance(item, (int, float)):
                    estimates.append(item)
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(plan)
    if not estimates or max(estimates) > 500000:
        raise DomainError(422, "query_plan_budget_exceeded", "执行计划超出预算或无法确认预计行数")
    return max(estimates)


class ReadConnector:
    """每次读取使用短连接并回滚，不建立后台轮询或自动重试。"""
    def __init__(self, *, http_transport=None, sql_factory=None):
        self.http_transport, self.sql_factory = http_transport, sql_factory

    @contextmanager
    def _connection(self, source, password):
        config, provider = source.connection_config, source.provider
        target = config_target(provider, config)
        if self.sql_factory:
            connection = self.sql_factory(provider, target, config, password)
        elif provider == "postgres":
            import psycopg
            connection = psycopg.connect(host=target["host"], port=target["port"], dbname=target["database"], user=config.get("username", ""), password=password, connect_timeout=2, sslmode="verify-full" if config.get("tls", True) else "disable", options="-c statement_timeout=2000 -c lock_timeout=500 -c default_transaction_read_only=on")
        else:
            import pymysql
            connection = pymysql.connect(host=target["host"], port=target["port"], database=target["database"], user=config.get("username", ""), password=password, connect_timeout=2, read_timeout=2, write_timeout=2, local_infile=False, charset="utf8mb4", autocommit=False, ssl=ssl.create_default_context() if config.get("tls", True) else None)
        try:
            with closing(connection.cursor()) as cursor:
                if provider == "postgres":
                    cursor.execute("SET TRANSACTION READ ONLY")
                    schema = config.get("schema_name", "public")
                    cursor.execute('SET LOCAL search_path TO "' + schema + '", pg_catalog')
                else:
                    cursor.execute("SET TRANSACTION READ ONLY")
                    cursor.execute("START TRANSACTION READ ONLY")
                    cursor.execute("SET SESSION MAX_EXECUTION_TIME=2000")
            yield connection
        finally:
            try:
                connection.rollback()
            finally:
                connection.close()

    def sql_read(self, source, safe, password):
        sql, parameters = driver_query(safe, source.provider)
        if parameters is None:
            # 直接DBAPI在args=None时不做pyformat替换；保留字面量中的单个百分号。
            sql = safe.sql
        started = monotonic()
        with self._connection(source, password) as connection, closing(connection.cursor()) as cursor:
            cursor.execute(("EXPLAIN (FORMAT JSON) " if source.provider == "postgres" else "EXPLAIN FORMAT=JSON ") + sql, parameters or None)
            raw_plan = cursor.fetchone()[0]
            plan = json.loads(raw_plan) if isinstance(raw_plan, str) else raw_plan
            estimated = plan_cost(plan)
            cursor.execute(sql, parameters or None)
            columns = [c[0] for c in cursor.description]
            if len(columns) != len(set(columns)):
                raise DomainError(422, "duplicate_output_columns", "输出列名重复，请设置别名")
            rows = [{column: json_value(value) for column, value in zip(columns, row)} for row in cursor.fetchmany(safe.max_rows + 1)]
        return self._result(columns, rows, safe, plan, started, {"estimated_rows": estimated})

    def es_read(self, source, safe, credential):
        from .elasticsearch import ElasticsearchReader
        return ElasticsearchReader(self.http_transport).read(source, safe, credential)

    @staticmethod
    def _result(columns, rows, safe, plan, started, extra=None):
        if len(json.dumps(rows, ensure_ascii=False, default=str).encode()) > 8 * 1024 * 1024:
            raise DomainError(422, "query_result_budget_exceeded", "查询结果超过8MB，请缩小范围")
        return {"columns": columns, "rows": rows[:safe.max_rows], "row_count": min(len(rows), safe.max_rows), "truncated": len(rows) > safe.max_rows,
                "executed_sql": safe.sql, "plan": plan, "elapsed_ms": round((monotonic() - started) * 1000, 3),
                "budget": {"timeout_seconds": 2, "max_rows": safe.max_rows, **(extra or {})}}

    def http_read(self, source, safe, token):
        """只调用登记的GET端点，随后在新内存SQLite中执行已授权只读查询。"""
        config = source.connection_config
        target = config_target(source.provider, config)
        host = "[" + target["host"] + "]" if ":" in target["host"] else target["host"]
        url = ("https" if config.get("tls", True) else "http") + f"://{host}:{target['port']}" + config.get("endpoint_path", "/data")
        started = monotonic()
        with httpx.Client(timeout=httpx.Timeout(2, connect=2), follow_redirects=False, trust_env=False, transport=self.http_transport) as client:
            with client.stream("GET", url, headers={"Authorization": "Bearer " + token, "Accept": "application/json"}) as response:
                if response.status_code != 200:
                    raise DomainError(502, "http_source_failed", "HTTP数据服务未返回可读取结果")
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > 8 * 1024 * 1024 or monotonic() - started > 2:
                        raise DomainError(408, "http_source_budget_exceeded", "HTTP数据读取超出预算")
        try:
            rows = json.loads(content)
            if config.get("response_key"):
                rows = rows[config["response_key"]]
        except (ValueError, KeyError, TypeError) as exc:
            raise DomainError(502, "http_source_shape_invalid", "HTTP数据格式不符合已登记结构") from exc
        if not isinstance(rows, list) or len(rows) > 10000 or any(not isinstance(r, dict) for r in rows):
            raise DomainError(422, "http_source_rows_invalid", "HTTP数据须为不超过10000行的对象数组")
        tables = source.schema_snapshot
        if len(tables) != 1:
            raise DomainError(422, "http_source_schema_invalid", "HTTP数据源须登记一个虚拟表结构")
        table = tables[0]
        columns = [c["columnName"] for c in table["columns"]]
        quote = lambda text: '"' + text.replace('"', '""') + '"'
        # 仅为本次已抓取响应创建临时内存表，不接受路径或外部SQLite文件。
        with closing(sqlite3.connect(":memory:", timeout=1)) as connection:
            connection.execute(f"CREATE TABLE {quote(table['tableName'])} ({','.join(quote(c) for c in columns)})")
            connection.executemany(f"INSERT INTO {quote(table['tableName'])} VALUES ({','.join('?' for _ in columns)})", [[json_value(row.get(c)) if not isinstance(row.get(c), (dict, list)) else json.dumps(row[c], ensure_ascii=False) for c in columns] for row in rows])
            connection.execute("PRAGMA query_only=ON")
            connection.set_progress_handler(lambda: 1 if monotonic() - started > 2 else 0, 1000)
            sql, parameters = driver_query(safe, "sqlite")
            plan = [{"detail": row[3]} for row in connection.execute("EXPLAIN QUERY PLAN " + sql, parameters or {})]
            cursor = connection.execute(sql, parameters or {})
            columns = [c[0] for c in cursor.description]
            result = [dict(zip(columns, row)) for row in cursor.fetchmany(safe.max_rows + 1)]
        return self._result(columns, result, safe, plan, started, {"http_source_rows": len(rows)})

    def metadata(self, source, password):
        """读取固定系统目录并限量，不接受前端提供的元数据SQL。"""
        provider, config = source.provider, source.connection_config
        if provider == 'elasticsearch':
            from .elasticsearch import ElasticsearchReader
            return ElasticsearchReader(self.http_transport).metadata(source, password)
        statement = "SELECT table_name, column_name, data_type FROM information_schema.columns WHERE table_schema = %s ORDER BY table_name, ordinal_position LIMIT 10001"
        scope = config.get("schema_name", "public") if provider == "postgres" else config["database"]
        with self._connection(source, password) as connection, closing(connection.cursor()) as cursor:
            cursor.execute(statement, (scope,))
            rows = cursor.fetchmany(10001)
        if len(rows) > 10000:
            raise DomainError(422, "metadata_budget_exceeded", "元数据超过预算，请缩小schema范围")
        tables = {}
        for table, column, data_type in rows:
            if str(table).startswith(("pg_", "sqlite_", "_")):
                continue
            tables.setdefault(table, {"tableName": table, "columns": []})["columns"].append({"columnName": column, "dataType": data_type})
        return list(tables.values())
