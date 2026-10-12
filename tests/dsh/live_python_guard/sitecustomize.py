"""有限真实验收的 Python 启动保护；只有显式开关开启时才安装。"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import socket
import sys
import tempfile
import threading
import time
from typing import Any
from urllib.parse import unquote, urlsplit


class GuardError(RuntimeError):
    """验收边界拒绝；错误文字不包含凭据、SQL 字面量或业务结果。"""


def _text(value: Any) -> str:
    return value.decode("utf-8", "strict") if isinstance(value, bytes) else str(value)


def temporary_sqlite_path(value: Any) -> None:
    """平台只可打开系统临时目录内的 SQLite 或内存 SQLite。"""
    name = os.fspath(value)
    if isinstance(name, bytes):
        name = os.fsdecode(name)
    if name in {"", ":memory:"}:
        return
    if name.startswith("file:"):
        target = urlsplit(name)
        if target.netloc or target.query or target.fragment:
            raise GuardError("sqlite_uri_not_allowed")
        name = unquote(target.path)
        if os.name == "nt" and re.match(r"^/[A-Za-z]:/", name):
            name = name[1:]
    target = Path(name).resolve()
    if not target.is_relative_to(Path(tempfile.gettempdir()).resolve()):
        raise GuardError("sqlite_must_be_temporary")


@dataclass(frozen=True)
class GuardConfig:
    """固定数据库边界；调用方只能提供表名单和审计文件位置。"""

    allowed_tables: frozenset[str]
    audit_path: Path
    stop_path: Path

    @classmethod
    def from_env(cls) -> "GuardConfig":
        try:
            tables = json.loads(os.environ["LIVE_ALLOWED_TABLES"])
            audit = Path(os.environ["LIVE_DB_AUDIT_PATH"])
            stop = Path(os.environ["LIVE_DB_STOP_PATH"])
            database_url = os.environ["DATABASE_URL"]
        except (KeyError, ValueError, TypeError) as exc:
            raise GuardError("missing_or_invalid_guard_configuration") from exc
        if (not isinstance(tables, list) or not tables
                or any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_]+", name) for name in tables)):
            raise GuardError("invalid_allowed_tables")
        if not audit.is_absolute() or not stop.is_absolute() or audit.resolve() == stop.resolve():
            raise GuardError("audit_and_stop_require_distinct_absolute_paths")
        prefix = next((item for item in ("sqlite:///", "sqlite+pysqlite:///")
                       if database_url.startswith(item)), None)
        if prefix is None or "?" in database_url:
            raise GuardError("platform_database_must_be_temporary_sqlite")
        temporary_sqlite_path(database_url[len(prefix):])
        return cls(frozenset(name.lower() for name in tables), audit.resolve(), stop.resolve())


class LiveDatabaseGuard:
    """单个验收 Worker 的进程级串行查询与预算保护。"""

    MAX_CONNECTIONS = 12
    MAX_SELECTS = 6
    MAX_COMMANDS = 120
    # 保留一次诊断连接的初始化、两次核对，以及最多十二条自身 KILL QUERY。
    DIAGNOSTIC_RESERVE = 18
    QUERY_SECONDS = 3
    STOP_CODES = frozenset({2002, 2003, 2006, 2013, 2055, 3024, 1205, 1317, 1927,
                            1037, 1038, 1040, 1041, 1114, 1206})
    REVIEWED_TABLES = frozenset({"t_devicemanagement", "t_inspectiondevice",
                               "t_dinspectionmaintenanceplan", "t_xunjianjihuarunhuabaoyang"})

    def __init__(self, config: GuardConfig):
        self.config = config
        self.lock = threading.RLock()
        self.audit_lock = threading.Lock()
        self.stop_lock = threading.Lock()
        self.local = threading.local()
        self.stopped = threading.Event()
        self.connections = 0
        self.business_selects = 0
        self.commands = 0
        self.diagnostic_connections = 0
        self.reviewed_join_executions = 0
        # 只接收本轮审阅计划后显式给出的逐表小扫描上限，默认仍保持原来的 50 行限制。
        self.reviewed_scan_limits = json.loads(os.environ.get("LIVE_REVIEWED_SCAN_LIMITS", "{}"))
        if (not isinstance(self.reviewed_scan_limits, dict) or any(
                name not in config.allowed_tables or type(limit) is not int or not 1 <= limit <= 3000
                for name, limit in self.reviewed_scan_limits.items())):
            raise GuardError("invalid_reviewed_scan_limits")
        self.owned_ids: set[int] = set()
        self._diagnostic_options: dict[str, Any] | None = None
        self._connection_class = None
        config.audit_path.parent.mkdir(parents=True, exist_ok=True)
        config.stop_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def phase(self, name: str):
        """内部 EXPLAIN 与故障诊断使用线程局部阶段，避免误计业务 SELECT。"""
        old = getattr(self.local, "phase", "")
        self.local.phase = name
        try:
            yield
        finally:
            self.local.phase = old

    @staticmethod
    def connection_id(connection: Any) -> int | None:
        try:
            return int(connection.thread_id())
        except (AttributeError, TypeError, ValueError):
            return None

    def ensure_running(self) -> None:
        if self.stopped.is_set() or self.config.stop_path.exists():
            raise GuardError("database_acceptance_stopped")

    def audit(self, event: str, **fields: Any) -> None:
        """仅接受调用点提供的脱敏字段；原始异常文字与结果集均不写盘。"""
        record = {"event": event, "utc": datetime.now(timezone.utc).isoformat(),
                  "pid": os.getpid(), **fields}
        try:
            with self.audit_lock, self.config.audit_path.open("a", encoding="utf-8") as output:
                output.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
                output.flush()
        except (OSError, ValueError, TypeError) as exc:
            self.mark_stopped("audit_write_failed")
            raise GuardError("audit_write_failed") from exc

    def mark_stopped(self, reason: str, *, error_code: int | None = None) -> None:
        """停止标记采用独占创建，不覆盖本轮或基准查询已经留下的故障证据。"""
        self.stopped.set()
        with self.stop_lock:
            try:
                with self.config.stop_path.open("x", encoding="utf-8") as output:
                    json.dump({"reason": reason, "error_code": error_code, "pid": os.getpid(),
                               "utc": datetime.now(timezone.utc).isoformat(),
                               "connection_ids": sorted(self.owned_ids)}, output, ensure_ascii=False)
            except FileExistsError:
                pass
            except OSError:
                # 文件系统故障时仍先关闭本进程的数据库访问，不能以写标记失败恢复查询。
                self.stopped.set()

    @staticmethod
    def error_code(error: BaseException) -> int | None:
        args = getattr(error, "args", ())
        return args[0] if args and isinstance(args[0], int) else None

    def handle_failure(self, connection: Any, error: BaseException) -> None:
        code = self.error_code(error)
        if (isinstance(error, (OSError, TimeoutError)) or code in self.STOP_CODES
                or self.stopped.is_set()):
            self.mark_stopped("database_timeout_or_disconnect", error_code=code)
            if getattr(self.local, "phase", "") != "diagnostic":
                self.diagnose()

    def normalize_control(self, sql: str) -> str | None:
        """只放行驱动初始化和只读事务控制，应用超时只能收紧不能放宽。"""
        normalized = " ".join(sql.strip().rstrip(";").split())
        if any(token in normalized for token in (";", "/*", "--", "#", "\x00")):
            return None
        upper = normalized.upper()
        if upper in {"ROLLBACK", "BEGIN", "START TRANSACTION", "START TRANSACTION READ ONLY",
                     "SET TRANSACTION READ ONLY", "SET SESSION TRANSACTION READ ONLY"}:
            return normalized
        if re.fullmatch(r"SET NAMES [A-Z0-9_]+(?: COLLATE [A-Z0-9_]+)?", upper):
            return normalized
        if re.fullmatch(r"SET (?:SESSION )?AUTOCOMMIT\s*=\s*[01]", upper):
            return normalized
        if re.fullmatch(r"SET (?:SESSION )?SQL_MODE\s*=\s*'[A-Z0-9_, ]*'", upper):
            return normalized
        timeout = re.fullmatch(r"SET (?:SESSION )?MAX_EXECUTION_TIME\s*=\s*(\d+)", upper)
        if timeout:
            return f"SET SESSION MAX_EXECUTION_TIME={min(max(int(timeout[1]), 1), 3000)}"
        if upper in {"SELECT 1", "SELECT VERSION()", "SELECT DATABASE()", "SELECT CONNECTION_ID()"}:
            return normalized
        if re.fullmatch(r"SELECT @@(?:SESSION\.)?(?:SQL_MODE|LOWER_CASE_TABLE_NAMES|"
                        r"TRANSACTION_ISOLATION|TX_ISOLATION|AUTOCOMMIT|VERSION|VERSION_COMMENT|"
                        r"CHARACTER_SET_CLIENT|CHARACTER_SET_CONNECTION|CHARACTER_SET_RESULTS|"
                        r"COLLATION_CONNECTION)", upper):
            return normalized
        return None

    def inspect_select(self, sql: str) -> list[str]:
        """解析整个 SQL 的表引用，子查询也必须受名单约束；不接受动态函数。"""
        import sqlglot
        from sqlglot import exp
        from sqlglot.optimizer.scope import traverse_scope

        # MySQL 可执行注释与优化器提示可能改变只读约束或超时，不透传。
        if re.search(r"/\*[!+]|\x00", sql):
            raise GuardError("sql_executable_comment_not_allowed")
        try:
            statements = sqlglot.parse(sql, read="mysql", error_level=sqlglot.ErrorLevel.RAISE)
        except Exception as exc:
            raise GuardError("sql_parse_failed") from exc
        if len(statements) != 1 or not isinstance(statements[0], (exp.Select, exp.Union)):
            raise GuardError("only_business_select_allowed")
        statement = statements[0]
        blocked = {"into", "lock", "command", "insert", "update", "delete", "merge", "create",
                   "drop", "alter", "copy", "transaction", "commit", "rollback", "parameter",
                   "sessionparameter", "propertyeq", "nextvaluefor", "userdefinedfunction"}
        for node in statement.walk():
            if node.key.lower() in blocked:
                raise GuardError("sql_write_or_control_not_allowed")
            if isinstance(node, exp.Anonymous):
                # 未识别函数可能是 UDF/存储函数，不能只靠 SELECT 前缀假定没有副作用。
                raise GuardError("sql_unrecognized_function_not_allowed")
        if any(node.args.get("recursive") for node in statement.find_all(exp.With)):
            raise GuardError("recursive_cte_not_allowed")
        tables = []
        # 按作用域识别真实底表，防止 CTE 与未授权底表同名时被别名白名单跳过。
        physical_tables = [source for scope in traverse_scope(statement)
                           for source in scope.sources.values() if isinstance(source, exp.Table)]
        for table in physical_tables:
            name = table.name.lower()
            if (not isinstance(table.this, exp.Identifier) or table.catalog
                    or (table.db and table.db.lower() != "szh_new") or name not in self.config.allowed_tables):
                raise GuardError("sql_table_not_allowed")
            tables.append(name)
        if not tables:
            raise GuardError("business_select_requires_allowed_table")
        return sorted(set(tables))

    def inspect_plan(self, raw_plan: str, allowed_tables: list[str]) -> list[dict[str, Any]]:
        """只保留表名、访问方式与估计行数，丢弃计划中可能含业务值的条件。"""
        try:
            document = json.loads(raw_plan)
        except (ValueError, TypeError) as exc:
            raise GuardError("explain_json_invalid") from exc
        tables: list[dict[str, Any]] = []

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                if "table_name" in value:
                    name = str(value.get("table_name", ""))
                    access = str(value.get("access_type", "")).upper()
                    rows = value.get("rows_examined_per_scan", value.get("rows"))
                    if isinstance(rows, bool) or not isinstance(rows, (int, float)) or not math.isfinite(rows):
                        raise GuardError("explain_row_estimate_missing")
                    if rows < 0 or rows > 3000 or not access:
                        raise GuardError("explain_scan_budget_exceeded")
                    tables.append({"table": name, "access": access, "rows": rows})
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(document)
        larger_scans = any(table["access"] == "ALL" and table["rows"] > 50 for table in tables)
        if larger_scans:
            by_alias = {table["table"].lower(): table for table in tables}
            # 单底表别名仍归属于已审阅的实际表；多表只能按实际表名匹配，不能泛化放宽。
            single_name = allowed_tables[0] if len(allowed_tables) == 1 else ""
            explicitly_bounded = all(table["access"] != "ALL" or table["rows"] <= 50
                or table["rows"] <= self.reviewed_scan_limits.get(table["table"].lower(),
                    self.reviewed_scan_limits.get(single_name, 0)) for table in tables)
            # 本轮已人工审阅的四表小 JOIN 仅允许一次；不能放宽普通查询的 ALL 上限。
            reviewed = (os.environ.get("LIVE_REVIEWED_SMALL_JOIN") == "1"
                        and frozenset(allowed_tables) == self.REVIEWED_TABLES
                        and len(tables) == 4 and set(by_alias) == {"d", "i", "p", "r"}
                        and by_alias["i"]["rows"] <= 2000 and by_alias["r"]["rows"] <= 50
                        and all(by_alias[name]["access"] in {"SYSTEM", "CONST", "EQ_REF", "REF"}
                                for name in ("d", "p"))
                        and sum(table["rows"] for table in tables) <= 2100)
            if not reviewed and not explicitly_bounded:
                raise GuardError("explain_scan_budget_exceeded")
            if reviewed and self.reviewed_join_executions:
                raise GuardError("reviewed_join_single_execution_exhausted")
            if reviewed:
                self.reviewed_join_executions += 1
        # 优化器完全折叠的查询没有物理扫描；常量结果可安全执行。
        if not tables:
            rendered = json.dumps(document).lower()
            if not any(term in rendered for term in ("no matching", "impossible where", "select tables optimized away")):
                raise GuardError("explain_has_no_verifiable_tables")
        return tables

    def reserve_command(self) -> None:
        diagnostic = getattr(self.local, "phase", "") == "diagnostic"
        limit = self.MAX_COMMANDS if diagnostic else self.MAX_COMMANDS - self.DIAGNOSTIC_RESERVE
        if self.commands >= limit:
            raise GuardError("database_command_budget_exhausted")
        self.commands += 1

    def bounded_call(self, connection: Any, callback: Any) -> Any:
        """绝对三秒看门狗关闭当前套接字，避免分包持续到达绕过 read_timeout。"""
        completed = threading.Event()

        def timeout() -> None:
            if completed.is_set():
                return
            self.mark_stopped("database_query_deadline")
            active_socket = getattr(connection, "_sock", None)
            if active_socket is not None:
                try:
                    active_socket.shutdown(socket.SHUT_RDWR)
                except (OSError, AttributeError):
                    pass

        timer = threading.Timer(self.QUERY_SECONDS, timeout)
        timer.daemon = True
        timer.start()
        started = time.monotonic()
        try:
            result = callback()
            if time.monotonic() - started >= self.QUERY_SECONDS:
                self.mark_stopped("database_query_deadline")
                raise GuardError("database_query_deadline")
            return result
        finally:
            completed.set()
            timer.cancel()

    def query(self, connection: Any, sql: Any, unbuffered: bool = False) -> Any:
        sql = _text(sql)
        with self.lock:
            phase = getattr(self.local, "phase", "")
            if phase in {"explain", "diagnostic", "internal"}:
                return self.bounded_call(connection, lambda: self._original_query(connection, sql, unbuffered=False))
            self.ensure_running()
            control = self.normalize_control(sql)
            sql_hash = hashlib.sha256(sql.encode("utf-8")).hexdigest()
            started = time.monotonic()
            fields: dict[str, Any] = {"connection_id": self.connection_id(connection), "sql_hash": sql_hash}
            try:
                if unbuffered:
                    raise GuardError("unbuffered_queries_not_allowed")
                if control is not None:
                    fields["summary"] = "initialization_or_readonly_control"
                    result = self.bounded_call(connection, lambda: self._original_query(connection, control, unbuffered=False))
                else:
                    tables = self.inspect_select(sql)
                    fields.update(summary="business_select", tables=tables)
                    if self.business_selects >= self.MAX_SELECTS:
                        raise GuardError("business_select_budget_exhausted")
                    # 拒绝的计划也占一次尝试预算，模型不能不断改写高成本查询试探。
                    self.business_selects += 1
                    with self.phase("explain"):
                        self.query(connection, "EXPLAIN FORMAT=JSON " + sql)
                    rows = connection._result.rows
                    if not rows or len(rows) != 1 or len(rows[0]) != 1:
                        raise GuardError("explain_result_invalid")
                    fields["plan"] = self.inspect_plan(_text(rows[0][0]), tables)
                    self.audit("query_plan", **fields)
                    self.ensure_running()
                    with self.phase("business"):
                        result = self.bounded_call(connection, lambda: self._original_query(connection, sql, unbuffered=False))
                self.audit("query_completed", **fields, elapsed_ms=round((time.monotonic() - started) * 1000, 3),
                           business_selects=self.business_selects, commands=self.commands)
                return result
            except BaseException as exc:
                self.audit("query_failed", **fields, elapsed_ms=round((time.monotonic() - started) * 1000, 3),
                           error_type=type(exc).__name__, error_code=self.error_code(exc),
                           reason=str(exc) if isinstance(exc, GuardError) else "database_error")
                self.handle_failure(connection, exc)
                raise

    def execute_command(self, connection: Any, command: int, sql: Any) -> Any:
        """覆盖不经过 query 的驱动初始化、ping 与事务控制入口。"""
        from pymysql.constants import COMMAND

        with self.lock:
            phase = getattr(self.local, "phase", "")
            if phase != "diagnostic":
                self.ensure_running()
                connection_id = self.connection_id(connection)
                if connection_id is not None:
                    # 驱动初始化 SET 也可能超时；握手取得的连接号须在初始化结束前纳入诊断。
                    self.owned_ids.add(connection_id)
            if command == COMMAND.COM_QUERY:
                if phase not in {"business", "explain", "diagnostic", "internal"}:
                    normalized = self.normalize_control(_text(sql))
                    if normalized is None:
                        raise GuardError("direct_database_command_not_allowed")
                    sql = normalized
            elif command != COMMAND.COM_PING:
                raise GuardError("database_protocol_command_not_allowed")
            self.reserve_command()
            self.audit("database_command", connection_id=self.connection_id(connection),
                       command="ping" if command == COMMAND.COM_PING else "query",
                       phase=phase or "control", command_number=self.commands,
                       sql_hash=hashlib.sha256(_text(sql).encode("utf-8")).hexdigest(),
                       summary="ping" if command == COMMAND.COM_PING else phase or "readonly_control")
            try:
                return self._original_execute_command(connection, command, sql)
            except BaseException as exc:
                self.handle_failure(connection, exc)
                raise

    def connect(self, connection: Any, sock: Any = None) -> None:
        """构造器、连接池重连和别名入口都必须经过同一个固定目标检查。"""
        with self.lock:
            diagnostic = getattr(self.local, "phase", "") == "diagnostic"
            if not diagnostic:
                self.ensure_running()
            if (sock is not None or connection.host != "127.0.0.1" or connection.port != 3306
                    or _text(connection.db) != "szh_new" or connection.unix_socket):
                raise GuardError("mysql_target_not_allowed")
            if getattr(connection, "_live_connected_once", False):
                raise GuardError("automatic_reconnect_not_allowed")
            if not diagnostic:
                if self.connections >= self.MAX_CONNECTIONS:
                    raise GuardError("database_connection_budget_exhausted")
                self.connections += 1
            connection._live_connected_once = True
            connection.connect_timeout = self.QUERY_SECONDS
            connection._read_timeout = self.QUERY_SECONDS
            connection._write_timeout = self.QUERY_SECONDS
            connection._local_infile = False
            # 禁止多语句协议，即使某次调用者传入了 client_flag 也不能放宽。
            from pymysql.constants import CLIENT
            connection.client_flag &= ~CLIENT.MULTI_STATEMENTS
            if not diagnostic:
                self._diagnostic_options = {
                    "host": "127.0.0.1", "port": 3306, "database": "szh_new",
                    "user": connection.user, "password": connection.password,
                    "charset": connection.charset, "autocommit": True,
                    "connect_timeout": 3, "read_timeout": 3, "write_timeout": 3,
                }
            started = time.monotonic()
            try:
                self.local.opening_database = True
                self._original_connect(connection, sock=None)
                connection_id = self.connection_id(connection)
                if connection_id is None:
                    raise GuardError("database_connection_id_missing")
                if not diagnostic:
                    self.owned_ids.add(connection_id)
                with self.phase("diagnostic" if diagnostic else "internal"):
                    self.query(connection, "SET SESSION MAX_EXECUTION_TIME=3000")
                    self.query(connection, "SET SESSION TRANSACTION READ ONLY")
                self.audit("connection_opened", connection_id=connection_id, diagnostic=diagnostic,
                           connection_number=self.connections,
                           elapsed_ms=round((time.monotonic() - started) * 1000, 3))
            except BaseException as exc:
                self.audit("connection_failed", error_type=type(exc).__name__, error_code=self.error_code(exc),
                           diagnostic=diagnostic, elapsed_ms=round((time.monotonic() - started) * 1000, 3))
                self.handle_failure(connection, exc)
                raise
            finally:
                self.local.opening_database = False

    def diagnose(self) -> None:
        """故障后仅一次原始诊断连接，核对并终止本轮自身仍执行中的语句。"""
        if self.diagnostic_connections or not self._diagnostic_options or not self.owned_ids:
            return
        self.diagnostic_connections += 1
        connection = None
        try:
            with self.phase("diagnostic"):
                connection = self._connection_class(**self._diagnostic_options)
                identifiers = ",".join(str(number) for number in sorted(self.owned_ids))
                sql = ("SELECT ID, COMMAND, TIME FROM information_schema.PROCESSLIST "
                       f"WHERE ID IN ({identifiers}) AND ID <> CONNECTION_ID() "
                       "AND USER = SUBSTRING_INDEX(CURRENT_USER(), '@', 1)")
                self.query(connection, sql)
                remaining = []
                for row in connection._result.rows or ():
                    identifier = int(row[0])
                    if identifier not in self.owned_ids:
                        continue
                    running = str(row[1]).lower() == "query"
                    remaining.append({"connection_id": identifier, "running": running})
                    if running:
                        self.query(connection, f"KILL QUERY {identifier}")
                        self.audit("own_query_killed", connection_id=identifier)
                self.query(connection, sql)
                residual = [{"connection_id": int(row[0]), "running": str(row[1]).lower() == "query"}
                            for row in connection._result.rows or () if int(row[0]) in self.owned_ids]
                self.audit("diagnostic_completed", before=remaining, after=residual)
        except BaseException as exc:
            self.audit("diagnostic_failed", error_type=type(exc).__name__, error_code=self.error_code(exc))
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass

    def install_pymysql(self) -> None:
        """在驱动类上保护全部别名，避免 SQLAlchemy 绕过 pymysql.connect 别名。"""
        import pymysql.connections

        connection_class = pymysql.connections.Connection
        self._connection_class = connection_class
        self._original_connect = connection_class.connect
        self._original_query = connection_class.query
        self._original_execute_command = connection_class._execute_command
        original_ping = connection_class.ping
        original_read_packet = connection_class._read_packet
        guard = self

        def connect(connection, sock=None):
            return guard.connect(connection, sock)

        def query(connection, sql, unbuffered=False):
            return guard.query(connection, sql, unbuffered)

        def execute_command(connection, command, sql):
            return guard.execute_command(connection, command, sql)

        def ping(connection, reconnect=False):
            with guard.lock:
                guard.ensure_running()
                try:
                    return guard.bounded_call(connection, lambda: original_ping(connection, reconnect=False))
                except BaseException as exc:
                    guard.handle_failure(connection, exc)
                    raise

        def read_packet(connection, *args, **kwargs):
            try:
                return original_read_packet(connection, *args, **kwargs)
            except BaseException as exc:
                guard.handle_failure(connection, exc)
                raise

        connection_class.connect = connect
        connection_class.query = query
        connection_class._execute_command = execute_command
        connection_class.ping = ping
        connection_class._read_packet = read_packet

    def check_address(self, address: Any) -> None:
        """仅数字回环地址可联网；数据库端口只向受保护 PyMySQL 连接开放。"""
        if not isinstance(address, tuple) or len(address) < 2:
            raise GuardError("only_numeric_loopback_network_allowed")
        try:
            allowed = ipaddress.ip_address(address[0]).is_loopback
        except (ValueError, TypeError):
            allowed = False
        if not allowed or address[1] in {8011, 8012}:
            raise GuardError("network_target_not_allowed")
        if address[1] == 3306 and not getattr(self.local, "opening_database", False):
            raise GuardError("database_socket_requires_guarded_pymysql")

    def install_network_and_sqlite(self) -> None:
        """保留进程内部 socketpair，阻断非本机接口、在用服务和持久平台库。"""
        import sqlite3

        guard = self
        original_connect = socket.socket.connect
        original_connect_ex = socket.socket.connect_ex
        original_sendto = socket.socket.sendto
        original_getaddrinfo = socket.getaddrinfo
        original_sqlite_connect = sqlite3.dbapi2.connect

        def connect(sock, address):
            guard.check_address(address)
            return original_connect(sock, address)

        def connect_ex(sock, address):
            guard.check_address(address)
            return original_connect_ex(sock, address)

        def sendto(sock, data, *args):
            guard.check_address(args[-1] if args else None)
            return original_sendto(sock, data, *args)

        def local_host(host):
            # 先限制解析目标，防止 create_connection 在 connect 拒绝前向外部 DNS 发请求。
            if host in {"localhost", b"localhost"}:
                return "127.0.0.1"
            try:
                address = ipaddress.ip_address(_text(host))
            except (TypeError, ValueError):
                raise GuardError("external_name_resolution_not_allowed") from None
            if not address.is_loopback:
                raise GuardError("external_name_resolution_not_allowed")
            return str(address)

        def getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
            if host is None and flags & socket.AI_PASSIVE:
                return original_getaddrinfo(host, port, family, type, proto, flags)
            return original_getaddrinfo(local_host(host), port, family, type, proto, flags | socket.AI_NUMERICHOST)

        def gethostbyname(host):
            return local_host(host)

        def gethostbyname_ex(host):
            return ("localhost", [], [local_host(host)])

        def gethostbyaddr(host):
            return ("localhost", [], [local_host(host)])

        def sqlite_connect(database, *args, **kwargs):
            temporary_sqlite_path(database)
            return original_sqlite_connect(database, *args, **kwargs)

        socket.socket.connect = connect
        socket.socket.connect_ex = connect_ex
        socket.socket.sendto = sendto
        socket.getaddrinfo = getaddrinfo
        socket.gethostbyname = gethostbyname
        socket.gethostbyname_ex = gethostbyname_ex
        socket.gethostbyaddr = gethostbyaddr
        sqlite3.connect = sqlite_connect
        sqlite3.dbapi2.connect = sqlite_connect

    def install(self) -> None:
        """安装在应用模块导入之前；禁止 PostgreSQL 的同步与异步入口。"""
        self.ensure_running()
        self.install_network_and_sqlite()
        self.install_pymysql()
        import psycopg

        def blocked_postgres(*args, **kwargs):
            raise GuardError("postgres_not_allowed_in_live_acceptance")

        psycopg.connect = blocked_postgres
        psycopg.Connection.connect = blocked_postgres
        psycopg.AsyncConnection.connect = blocked_postgres
        self.audit("guard_ready", target="127.0.0.1:3306/szh_new",
                   allowed_tables=sorted(self.config.allowed_tables), max_connections=self.MAX_CONNECTIONS,
                   max_business_selects=self.MAX_SELECTS, max_commands=self.MAX_COMMANDS,
                   query_seconds=self.QUERY_SECONDS, diagnostic_connections=1,
                   reviewed_small_join=os.environ.get("LIVE_REVIEWED_SMALL_JOIN") == "1")
        os.environ["LIVE_DB_GUARD_READY"] = "1"


if os.environ.get("LIVE_DSH_ACCEPTANCE") == "1":
    try:
        _ACTIVE_GUARD = LiveDatabaseGuard(GuardConfig.from_env())
        _ACTIVE_GUARD.install()
    except BaseException as error:
        # Python 默认忽略 sitecustomize 导入错误；必须退出，不能无保护启动业务进程。
        sys.stderr.write("live database guard initialization failed: " + type(error).__name__ + "\n")
        sys.stderr.flush()
        os._exit(78)
