"""受控SQLite/CSV取数；查询先做AST授权、计划预算，再有限执行。

只读取平台本轮新建并登记的文件，拒绝外部路径或连接配置。一次查询最多
2秒、20万虚拟机指令；超时后源被阻断，不自动重试、切源或提高预算。
"""
from copy import deepcopy
from contextlib import closing
import csv
import json
from datetime import datetime, timezone
from hashlib import sha256
from io import StringIO
from pathlib import Path
import re
import sqlite3
from time import monotonic
from uuid import uuid4

from sqlalchemy import func, select, update

from agent_platform.core.errors import DomainError
from agent_platform.core.records import RecordStore

from . import models as m
from .sql_safety import SqlFieldValidationError, authorize_sql, driver_query
from .connectors import ReadConnector, config_target, require_approved_target
from .policy_sql import apply_row_policies, reject_reserved_parameters
from .row_policies import RowPolicyService


def view(row):
    result = {}
    for c in row.__table__.columns:
        if c.name == "managed_filename":
            continue
        value = getattr(row, c.name)
        if isinstance(value, datetime) and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        result[c.name] = value.isoformat() if isinstance(value, datetime) else value
    return result


class DataService:
    def __init__(self, session, actor, settings=None, *, connector=None):
        self.session, self.actor, self.settings = session, actor, settings
        self.connector = connector or ReadConnector()

    @staticmethod
    def _dialect(source):
        return "sqlite" if source.provider == "http_json" else source.provider

    @staticmethod
    def _schema_name(source):
        return (source.connection_config or {}).get("schema_name", "public") if source.provider == "postgres" else (source.connection_config or {}).get("database", "")

    def _directory(self):
        if self.settings is None:
            raise DomainError(503, "data_settings_missing", "数据存储尚未配置")
        directory = (Path(self.settings.data_dir) / "imports").resolve()
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _path(self, source):
        root = self._directory()
        if not re.fullmatch(r"source_[0-9a-f]{32}\.sqlite3", source.managed_filename):
            raise DomainError(403, "unmanaged_source", "仅允许使用平台创建的数据文件")
        path = (root / source.managed_filename).resolve()
        if path.parent != root or path.is_symlink():
            raise DomainError(403, "unmanaged_source", "数据文件不在平台受控目录")
        if not path.is_file():
            raise DomainError(404, "source_file_missing", "数据源文件不存在")
        return path

    def get(self, model, row_id, *, include_archived=False):
        clauses = [model.id == row_id, model.workspace_id == self.actor.workspace_id]
        if not include_archived:
            clauses.append(model.archived.is_(False))
        row = self.session.scalar(select(model).where(*clauses))
        if row is None:
            raise DomainError(404, "resource_not_found", "资源不存在或不在当前工作区")
        return row

    def get_source(self, row_id):
        return self.get(m.DataSource, row_id)

    def get_query(self, row_id):
        query = self.get(m.QueryRecord, row_id)
        source = self.get(m.DataSource, query.datasource_id, include_archived=True)
        snapshot = self.session.scalar(select(m.QueryAccessSnapshot).where(m.QueryAccessSnapshot.query_id == query.id, m.QueryAccessSnapshot.workspace_id == self.actor.workspace_id))
        if snapshot is not None:
            visible = {table["tableName"]: {column["columnName"] for column in table["columns"]} for table in self._visible_schema(source)}
            if any(not set(fields).issubset(visible.get(table, set())) for table, fields in snapshot.field_scope.items()):
                # 保留既有字段撤权错误合同；不能因重展开SELECT *少了列而静默读取旧结果。
                raise SqlFieldValidationError("历史结果引用了当前未授权字段")
        # 新快照按原意图重新计算范围；旧快照须核实际展开过的字段，不能用SELECT *吞掉撤权。
        sql = query.sql if snapshot is not None else query.result.get("executed_sql", query.sql)
        safe = self._authorize_query(source, sql, query.parameters, 10000, compile_sql=False)
        restricted = any(scope["mode"] == "rules" for scope in safe.access_scope["row_scope"].values())
        if (snapshot is not None and snapshot.scope_hash != safe.access_scope["scope_hash"]) or (snapshot is None and restricted):
            raise DomainError(403, "query_scope_denied", "该历史结果不在当前数据权限范围，请重新取数")
        return query

    def _authorize_query(self, source, sql, parameters, max_rows, *, compile_sql=True):
        """先校验用户行列引用，再注入服务端行条件；所有执行和历史读取共用此关口。"""
        reject_reserved_parameters(parameters)
        schema = self._visible_schema(source)
        safe = authorize_sql(sql, provider=self._dialect(source), tables=schema, max_rows=max_rows, parameters=parameters, database=self._schema_name(source))
        return apply_row_policies(safe, source, self.session, self.actor, schema, compile_sql=compile_sql)

    def visible_queries(self, *, q="", limit=50, offset=0):
        """先授权再分页；隐藏查询不会占用分页位置或泄露不可见总量。"""
        statement = select(m.QueryRecord).where(m.QueryRecord.workspace_id == self.actor.workspace_id, m.QueryRecord.archived.is_(False))
        if q:
            statement = statement.where(m.QueryRecord.name.contains(q, autoescape=True))
        visible = []
        for row in self.session.scalars(statement.order_by(m.QueryRecord.created_at.desc(), m.QueryRecord.id)):
            try:
                self.get_query(row.id)
            except DomainError as exc:
                if exc.status in (403, 404, 422):
                    continue
                raise
            visible.append(view(row))
        offset, limit = max(offset, 0), min(max(limit, 1), 200)
        return {"items": visible[offset:offset + limit], "total": len(visible)}

    def dataset_view(self, row_id):
        """数据集目录只投影当前字段；受行控表的全表数量不能冒充个人可见数量。"""
        row = self.get(m.Dataset, row_id)
        source = self.get(m.DataSource, row.datasource_id, include_archived=True)
        tables = {table["tableName"]: table for table in self._visible_schema(source)}
        table = tables.get(row.table_name)
        if table is None:
            raise DomainError(403, "dataset_scope_denied", "当前账号无权访问此数据集")
        scopes, _ = RowPolicyService(self.session, self.actor).effective(source, [row.table_name])
        if scopes[row.table_name]["mode"] == "rules" and not scopes[row.table_name]["clauses"]:
            raise DomainError(403, "dataset_scope_denied", "当前账号无权访问此数据集")
        fields = {column["columnName"] for column in table["columns"]}
        result = view(row)
        result["columns"] = [field for field in row.columns if field in fields]
        if any(policy.table_name == row.table_name and policy.mode == "rules" for policy in RowPolicyService(self.session, self.actor).policies(source.id)):
            result["row_count"] = None
        return result

    def visible_datasets(self, *, q="", limit=50, offset=0):
        statement = select(m.Dataset).where(m.Dataset.workspace_id == self.actor.workspace_id, m.Dataset.archived.is_(False))
        if q:
            statement = statement.where(m.Dataset.name.contains(q, autoescape=True))
        visible = []
        for row in self.session.scalars(statement.order_by(m.Dataset.created_at.desc(), m.Dataset.id)):
            try:
                visible.append(self.dataset_view(row.id))
            except DomainError as exc:
                if exc.status in (403, 404):
                    continue
                raise
        offset, limit = max(offset, 0), min(max(limit, 1), 200)
        return {"items": visible[offset:offset + limit], "total": len(visible)}

    def list(self, model, *, q="", limit=50, offset=0):
        clauses = [model.workspace_id == self.actor.workspace_id, model.archived.is_(False)]
        if q:
            clauses.append(model.name.contains(q, autoescape=True))
        total = self.session.scalar(select(func.count()).select_from(model).where(*clauses))
        rows = self.session.scalars(select(model).where(*clauses).order_by(model.created_at.desc()).limit(min(max(limit, 1), 200)).offset(max(offset, 0))).all()
        return {"items": [view(r) for r in rows], "total": total}

    def update(self, model, row_id, changes, revision):
        row = self.get(model, row_id)
        data = dict(changes)
        data.pop("revision", None)
        response = self.session.execute(update(model).where(model.id == row.id, model.workspace_id == self.actor.workspace_id, model.revision == revision).values(**data, revision=revision + 1, updated_at=m.now()))
        if response.rowcount != 1:
            raise DomainError(409, "revision_conflict", "内容已更新，请刷新后重试")
        self.session.flush()
        self.session.refresh(row)
        RecordStore(self.session, self.actor).audit("update", model.__tablename__, row.id)
        return view(row)

    def create_source(self, data):
        data = dict(data)
        data.pop("revision", None)
        data["connection_config"] = data.get("connection_config") or {}
        if data.get("provider", "sqlite") != "sqlite":
            self.actor.require("m05.admin")
            target = config_target(data["provider"], data["connection_config"])
            # 将实际采用的默认端口等目标字段保存下来，使审批控件与执行目标完全一致。
            data["connection_config"] = {**data["connection_config"], **target}
            self._validate_schema(data.get("schema_snapshot", []))
            source = m.DataSource(workspace_id=self.actor.workspace_id, managed_filename="", status="pending_approval", **data)
            self.session.add(source)
            self.session.flush()
            RecordStore(self.session, self.actor).audit("create", "datasource", source.id, {"provider": source.provider, "status": "pending_approval"})
            return view(source)
        if data.get("schema_snapshot"):
            raise DomainError(422, "sqlite_schema_managed", "SQLite表结构由实际导入数据建立")
        file_id = uuid4().hex
        filename = f"source_{file_id}.sqlite3"
        path = self._directory() / filename
        # 不打开已有文件，不继承原系统连接或用户提供的路径。
        if path.exists():
            raise DomainError(409, "source_exists", "数据文件标识冲突")
        with closing(sqlite3.connect(path, timeout=1)) as connection, connection:
            connection.execute("CREATE TABLE _platform_import_marker (id TEXT PRIMARY KEY)")
            connection.execute("INSERT INTO _platform_import_marker VALUES (?)", (file_id,))
        source = m.DataSource(workspace_id=self.actor.workspace_id, managed_filename=filename, **data)
        self.session.add(source)
        self.session.flush()
        RecordStore(self.session, self.actor).audit("create", "datasource", source.id)
        return view(source)

    def patch_source(self, row_id, data):
        source = self.get_source(row_id)
        changes = {k: v for k, v in data.items() if k not in ("field_roles", "revision") and v is not None}
        if any(key in changes for key in ("connection_config", "credential_ref", "schema_snapshot")):
            self.actor.require("m05.admin")
            if source.provider == "sqlite":
                raise DomainError(422, "sqlite_schema_managed", "SQLite连接与结构由实际导入数据管理")
            target = config_target(source.provider, changes.get("connection_config", source.connection_config))
            if "connection_config" in changes:
                changes["connection_config"] = {**changes["connection_config"], **target}
            self._validate_schema(changes.get("schema_snapshot", source.schema_snapshot))
            changes.update(target_approval={}, status="pending_approval")
        if data.get("field_roles") is not None:
            self.actor.require("m05.admin")
            snapshot = deepcopy(source.schema_snapshot)
            rules = data["field_roles"]
            known_tables = {t["tableName"]: {c["columnName"] for c in t["columns"]} for t in snapshot}
            if any(t not in known_tables or any(c not in known_tables[t] for c in columns) for t, columns in rules.items()):
                raise DomainError(422, "unknown_field", "字段权限引用了不存在的字段")
            for table in snapshot:
                for column in table["columns"]:
                    if column["columnName"] in rules.get(table["tableName"], {}):
                        column["allowed_roles"] = rules[table["tableName"]][column["columnName"]]
            changes["schema_snapshot"] = snapshot
        return self.update(m.DataSource, source.id, changes, data["revision"])

    @staticmethod
    def _validate_schema(tables):
        names = set()
        for table in tables:
            name = table.get("tableName", "")
            columns = table.get("columns", [])
            if not isinstance(name, str) or not name or len(name) > 160 or name.lower().startswith(("sqlite_", "pg_", "information_schema")) or name.casefold() in names or not 1 <= len(columns) <= 100:
                raise DomainError(422, "metadata_schema_invalid", "表名或字段范围无效")
            names.add(name.casefold())
            fields = [c.get("columnName", "") for c in columns]
            if any(not isinstance(f, str) or not f or len(f) > 160 or "\0" in f for f in fields) or len({f.casefold() for f in fields}) != len(fields):
                raise DomainError(422, "metadata_schema_invalid", "字段名须唯一且有效")

    def approve_target(self, row_id, data):
        source = self.get_source(row_id)
        if source.provider == "sqlite":
            raise DomainError(422, "managed_source_no_approval_needed", "平台隔离导入源无需外部目标批准")
        target = config_target(source.provider, source.connection_config)
        if any(data.get(k) != value for k, value in target.items()):
            raise DomainError(422, "approval_target_mismatch", "批准目标与当前主机、端口或库名不一致")
        try:
            expires = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00"))
            if expires.tzinfo is None or not 0 < expires.timestamp() - m.now().timestamp() <= 86400:
                raise ValueError()
        except ValueError as exc:
            raise DomainError(422, "approval_expiry_invalid", "批准有效期须包含时区且在未来24小时内") from exc
        if source.status == "blocked" and not data.get("resume_after_failure"):
            raise DomainError(409, "source_blocked", "请先核查异常，再明确确认恢复连接")
        approval = {k: v for k, v in data.items() if k not in ("revision", "resume_after_failure")}
        approval.update(approved_by=self.actor.user_id, approved_at=m.now().isoformat())
        return self.update(m.DataSource, source.id, {"target_approval": approval, "status": "active"}, data["revision"])

    def _password(self, source):
        if not source.credential_ref:
            raise DomainError(503, "datasource_credential_missing", "请为数据源选择独立凭据")
        from agent_platform.core.secrets import SecretVault
        return SecretVault(None, self.settings).get(source.credential_ref, self.actor.workspace_id, session=self.session)

    def recover_source(self, row_id, data):
        """管理员核查异常后的显式恢复；不在恢复动作中派发查询或连接测试。"""
        source = self.get_source(row_id)
        if source.provider != "sqlite":
            raise DomainError(422, "target_reapproval_required", "外部数据源请重新核实目标并批准范围")
        if source.status != "blocked":
            raise DomainError(409, "source_not_blocked", "数据源当前无需恢复")
        self._path(source)
        result = self.update(m.DataSource, source.id, {"status": "active"}, data["revision"])
        RecordStore(self.session, self.actor).audit("recover_datasource", "datasource", source.id, {"note": data["note"], "connection_attempted": False})
        return result

    def _source_failure(self, source, cause):
        source_id, workspace_id = source.id, self.actor.workspace_id
        timed_out = isinstance(cause, TimeoutError) or "timeout" in type(cause).__name__.lower() or getattr(cause, "status", None) == 408
        error = DomainError(408 if timed_out else 422, "query_failed", "数据读取失败，已停止后续访问，请核查数据源")
        def block_source(guard_session):
            guard_session.execute(update(m.DataSource).where(m.DataSource.id == source_id, m.DataSource.workspace_id == workspace_id).values(status="blocked", revision=m.DataSource.revision + 1, updated_at=m.now()))
        error.after_rollback = block_source
        return error

    def _visible_schema(self, source):
        result = []
        admin = self.actor.can("m05.admin")
        for table in source.schema_snapshot:
            columns = [c for c in table["columns"] if admin or not c.get("allowed_roles") or set(c["allowed_roles"]).intersection(self.actor.roles)]
            if columns:
                result.append({**table, "columns": columns})
        return result

    def metadata(self, source_id):
        source = self.get_source(source_id)
        restricted = {row.table_name for row in RowPolicyService(self.session, self.actor).policies(source.id) if row.mode == "rules"}
        tables = [{key: value for key, value in table.items() if key != "rowCount" or table["tableName"] not in restricted} for table in self._visible_schema(source)]
        return {"datasource_id": source.id, "revision": source.revision, "tables": tables}

    def check_connection(self, source_id):
        source = self.get_source(source_id)
        if source.status == "blocked":
            raise DomainError(409, "source_blocked", "数据源上次执行异常，须先排查")
        if source.provider == 'elasticsearch':
            require_approved_target(source, self.settings, 'metadata')
            password = self._password(source)
            try:
                self.connector.metadata(source, password)
            except Exception as exc:
                raise self._source_failure(source, exc) from exc
            return {'datasource_id': source.id, 'status': 'connected', 'provider': source.provider,
                    'managed': False, 'checked': 'index_mapping', 'rows_checked': 0}
        if source.provider != "sqlite":
            require_approved_target(source, self.settings, "query")
            safe = authorize_sql("SELECT 1 AS connected", provider=self._dialect(source), tables=self._visible_schema(source), max_rows=1, database=self._schema_name(source))
            try:
                password = self._password(source)
                result = (self.connector.es_read(source, safe, password) if source.provider == 'elasticsearch' else
                          self.connector.http_read(source, safe, password) if source.provider == "http_json" else self.connector.sql_read(source, safe, password))
            except DomainError:
                raise
            except Exception as exc:
                raise self._source_failure(source, exc) from exc
            return {"datasource_id": source.id, "status": "connected", "provider": source.provider, "managed": False, "rows_checked": result["row_count"]}
        path = self._path(source)
        try:
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)) as connection:
                connection.execute("SELECT 1").fetchone()
        except sqlite3.Error as exc:
            raise DomainError(422, "connection_failed", "隔离数据文件无法读取") from exc
        return {"datasource_id": source.id, "status": "connected", "provider": "sqlite", "managed": True}

    def import_csv(self, data):
        text = data["csv_text"].lstrip("\ufeff")
        reader = csv.reader(StringIO(text, newline=""))
        try:
            columns = next(reader)
        except StopIteration as exc:
            raise DomainError(422, "csv_empty", "CSV缺少表头") from exc
        columns = [column.strip() for column in columns]
        if not 1 <= len(columns) <= 100 or len({c.casefold() for c in columns}) != len(columns) or any(not c or len(c) > 100 or "\0" in c for c in columns):
            raise DomainError(422, "csv_columns_invalid", "表头须非空、唯一且不超过100列")
        rows = []
        for index, row in enumerate(reader, start=2):
            if not row:
                continue
            if len(rows) >= 10000:
                raise DomainError(422, "csv_budget_exceeded", "单次导入最多10000行，请拆分文件")
            if len(row) != len(columns) or any(len(v) > 10000 or "\0" in v for v in row):
                raise DomainError(422, "csv_row_invalid", f"CSV第{index}行列数或字段长度无效")
            rows.append(row)
        if not rows:
            raise DomainError(422, "csv_no_rows", "CSV没有数据行")
        source_id = data.get("datasource_id")
        if source_id is None:
            source_id = self.create_source({"name": data["name"], "provider": "sqlite"})["id"]
        source = self.get_source(source_id)
        if source.provider != "sqlite":
            raise DomainError(422, "import_target_not_managed", "CSV只能导入平台隔离SQLite数据源")
        if source.status != "active":
            raise DomainError(409, "source_unavailable", "数据源当前不可导入")
        path = self._path(source)
        table_name = "dataset_" + uuid4().hex[:16]
        quoted = lambda value: '"' + value.replace('"', '""') + '"'
        # CSV全部按TEXT保存原始精度；查询可显式CAST，判定使用Decimal。
        with closing(sqlite3.connect(path, timeout=1)) as connection, connection:
            connection.execute(f"CREATE TABLE {quoted(table_name)} ({', '.join(quoted(c) + ' TEXT' for c in columns)})")
            connection.executemany(f"INSERT INTO {quoted(table_name)} VALUES ({','.join('?' for _ in columns)})", rows)
        schema = deepcopy(source.schema_snapshot)
        schema.append({"tableName": table_name, "rowCount": len(rows), "columns": [{"columnName": c, "dataType": "TEXT"} for c in columns]})
        self.update(m.DataSource, source.id, {"schema_snapshot": schema}, source.revision)
        dataset = m.Dataset(workspace_id=self.actor.workspace_id, name=data["name"], datasource_id=source.id, table_name=table_name, row_count=len(rows), columns=columns, source_hash=sha256(text.encode()).hexdigest(), description=data.get("description", ""), source_kind=data.get("source_kind", "csv_import"))
        self.session.add(dataset)
        self.session.flush()
        RecordStore(self.session, self.actor).audit("import_csv", "dataset", dataset.id, {"rows": len(rows), "columns": len(columns)})
        return view(dataset)

    def execute_query(self, data):
        source = self.get_source(data["datasource_id"])
        if source.status != "active":
            raise DomainError(409, "source_blocked", "数据源当前不可查询，请先排查上次异常")
        schema = self._visible_schema(source)
        safe = self._authorize_query(source, data["sql"], data.get("parameters", {}), min(data.get("max_rows", 1000), source.max_rows))
        if source.provider != "sqlite":
            require_approved_target(source, self.settings, "query")
            password = self._password(source)
            try:
                result = (self.connector.es_read(source, safe, password) if source.provider == 'elasticsearch' else
                          self.connector.http_read(source, safe, password) if source.provider == "http_json" else self.connector.sql_read(source, safe, password))
            except DomainError as exc:
                if exc.code.startswith(("http_source", "es_source")):
                    raise self._source_failure(source, exc) from exc
                raise
            except Exception as exc:
                raise self._source_failure(source, exc) from exc
            return self._save_query(source, data, safe, result)
        used = [t for t in schema if t["tableName"] in safe.tables]
        workload = 1
        for table in used:
            workload *= max(table.get("rowCount", 1), 1)
        if len(safe.tables) > 3 or (len(safe.tables) > 1 and workload > 500000):
            raise DomainError(422, "query_plan_budget_exceeded", "关联查询预计工作量超出预算，请缩小范围")
        path = self._path(source)
        sql, params = driver_query(safe, "sqlite")
        started = monotonic()
        steps, budget_aborted = 0, False
        def progress():
            nonlocal steps, budget_aborted
            steps += 1000
            budget_aborted = steps > 200000 or monotonic() - started > 2
            return 1 if budget_aborted else 0
        try:
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA query_only=ON")
                connection.set_progress_handler(progress, 1000)
                plan = [dict(r) for r in connection.execute("EXPLAIN QUERY PLAN " + sql, params or {})]
                cursor = connection.execute(sql, params or {})
                columns = [c[0] for c in cursor.description]
                if len(set(columns)) != len(columns):
                    raise DomainError(422, "duplicate_output_columns", "查询输出列名重复，请设置别名")
                rows = [dict(r) for r in cursor.fetchmany(safe.max_rows + 1)]
                connection.set_progress_handler(None, 0)
        except sqlite3.Error as exc:
            # 原事务整体回滚后，仅用独立事务标记异常源，避免持久化半个业务结果。
            source_id, workspace_id = source.id, self.actor.workspace_id
            error = DomainError(408 if budget_aborted else 422, "query_budget_exceeded" if budget_aborted else "query_failed", "查询超出预算，已停止执行" if budget_aborted else "查询执行失败，数据源已暂停")
            def block_source(guard_session):
                guard_session.execute(update(m.DataSource).where(m.DataSource.id == source_id, m.DataSource.workspace_id == workspace_id).values(status="blocked", revision=m.DataSource.revision + 1, updated_at=m.now()))
            error.after_rollback = block_source
            raise error from exc
        result = {"columns": columns, "rows": rows[:safe.max_rows], "truncated": len(rows) > safe.max_rows,
                  "row_count": min(len(rows), safe.max_rows), "elapsed_ms": round((monotonic() - started) * 1000, 3),
                  "plan": plan, "executed_sql": safe.sql,
                  "budget": {"timeout_seconds": 2, "vm_steps": 200000, "max_rows": safe.max_rows}}
        # 保存原查询意图与实际执行SQL，刷新时重新授权并重新施加预算；不能把探测LIMIT当成用户范围。
        return self._save_query(source, data, safe, result)

    def natural_query(self, data, completion):
        """仅一次原生模型补全生成候选SQL，执行仍经过相同授权和预算。"""
        source = self.get_source(data["datasource_id"])
        if source.status != "active":
            raise DomainError(409, "source_unavailable", "请选择已启用的数据源")
        schema = self._visible_schema(source)
        if not schema:
            raise DomainError(422, "source_schema_missing", "当前账号没有可用的授权表字段")
        if source.provider != "sqlite":
            require_approved_target(source, self.settings, "query")
        metadata = [{"table": t["tableName"], "columns": [{"name": c["columnName"], "type": c.get("dataType", "TEXT")} for c in t["columns"]]} for t in schema]
        messages = [{"role": "system", "content": "你只生成一条只读SQL，不执行查询。仅使用给出的授权schema和字段；不能使用系统表、递归、写入、文件或扩展。不猜测未给出的字段。输出严格JSON对象{\"sql\":\"SELECT ...\"}；无法回答则输出{\"error\":\"缺少所需字段\"}。数据源方言：" + self._dialect(source) + "。授权schema：" + json.dumps(metadata, ensure_ascii=False)},
                    {"role": "user", "content": data["question"]}]
        response = completion(data["model_profile_id"], messages, max_tokens=1600)
        try:
            text = response["choices"][0]["message"]["content"]
            if not isinstance(text, str) or len(text) > 30000:
                raise ValueError()
            text = text.strip()
            if text.startswith("```"):
                text = re.sub(r"^```(?:json|sql)?\s*|\s*```$", "", text, flags=re.I)
            candidate = json.loads(text)
            sql = candidate.get("sql")
            if not isinstance(sql, str) or not sql.strip():
                raise ValueError()
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise DomainError(422, "model_sql_not_available", "模型没有生成可用的只读SQL，请检查问题与授权字段") from exc
        safe = self._authorize_query(source, sql, {}, min(data.get("max_rows", 1000), source.max_rows))
        if not data.get("execute", True):
            return {"status": "candidate", "sql": sql, "authorized_sql": safe.sql, "datasource_id": source.id, "executed": False}
        return self.execute_query({"datasource_id": source.id, "sql": sql, "parameters": {}, "max_rows": data.get("max_rows", 1000), "name": data["question"][:160]})

    def _save_query(self, source, data, safe, result):
        # 原参数只保存用户意图；内部行条件参数不能在报告刷新时作为用户参数重复提交。
        record = m.QueryRecord(workspace_id=self.actor.workspace_id, name=data.get("name", "数据查询"), datasource_id=source.id, sql=data["sql"], parameters=dict(data.get("parameters", {})), result=result, created_by=self.actor.user_id)
        self.session.add(record)
        self.session.flush()
        self.session.add(m.QueryAccessSnapshot(query_id=record.id, workspace_id=self.actor.workspace_id, subject_user_id=self.actor.user_id, **safe.access_scope))
        self.session.flush()
        RecordStore(self.session, self.actor).audit("query", "datasource", source.id, {"query_id": record.id, "tables": list(safe.tables), "rows": result["row_count"]})
        output = view(record)
        output.update(result)
        return output

    def sync_metadata(self, source_id):
        source = self.get_source(source_id)
        if source.provider in ("sqlite", "http_json"):
            self.check_connection(source_id)
            return self.metadata(source_id)
        if source.status != "active":
            raise DomainError(409, "source_unavailable", "数据源尚未批准或已暂停")
        require_approved_target(source, self.settings, "metadata")
        password = self._password(source)
        try:
            tables = self.connector.metadata(source, password)
        except DomainError as exc:
            # 服务端协议或响应预算异常同样隔离；配置/权限拒绝没有派发业务查询。
            if exc.code.startswith("es_source_") or exc.code == "es_mapping_budget":
                raise self._source_failure(source, exc) from exc
            raise
        except Exception as exc:
            raise self._source_failure(source, exc) from exc
        self._validate_schema(tables)
        # 同步不可扩大已有字段角色授权；新字段保留为管理员可见直至明确授权。
        old = {t["tableName"]: {c["columnName"]: c for c in t["columns"]} for t in source.schema_snapshot}
        for table in tables:
            for column in table["columns"]:
                previous = old.get(table["tableName"], {}).get(column["columnName"])
                column["allowed_roles"] = previous.get("allowed_roles", []) if previous else ["admin"]
        self.update(m.DataSource, source.id, {"schema_snapshot": tables}, source.revision)
        return self.metadata(source.id)

    def quality_check(self, data):
        from .quality import check_quality
        query = self.get_query(data["query_id"])
        return {"query_id": query.id, **check_quality(query.result["rows"], query.result["columns"], data, truncated=query.result["truncated"])}

    def process(self, data):
        from .processing import run_pipeline
        query = self.get_query(data["query_id"])
        if query.result["truncated"]:
            raise DomainError(422, "processing_input_truncated", "查询已截断，请缩小范围再处理")
        result = run_pipeline(query.result["rows"], query.result["columns"], data["operations"])
        record = m.ProcessingJob(workspace_id=self.actor.workspace_id, name=data["name"], query_id=query.id, operations=deepcopy(data["operations"]), result=result, status="succeeded")
        self.session.add(record)
        self.session.flush()
        RecordStore(self.session, self.actor).audit("process_data", "processing_job", record.id)
        return view(record)

    def get_processing(self, row_id):
        """处理结果继续受原查询的当前字段权限约束。"""
        row = self.get(m.ProcessingJob, row_id)
        self.get_query(row.query_id)
        for operation in row.operations:
            if operation.get("type") == "metric":
                self.actor.require("m05.read")
                RecordStore(self.session, self.actor).get("business_metric", operation["metric_id"])
        return row

    def visible_processing(self, *, q="", metric_id=None, limit=50, offset=0):
        """处理与指标历史沿来源逐条授权后分页，失权记录不会泄露总量或阻断目录。"""
        statement = select(m.ProcessingJob).where(m.ProcessingJob.workspace_id == self.actor.workspace_id, m.ProcessingJob.archived.is_(False))
        if q:
            statement = statement.where(m.ProcessingJob.name.contains(q, autoescape=True))
        visible = []
        for row in self.session.scalars(statement.order_by(m.ProcessingJob.created_at.desc(), m.ProcessingJob.id)):
            if metric_id and not any(operation.get("metric_id") == metric_id for operation in row.operations):
                continue
            try:
                self.get_processing(row.id)
            except DomainError as exc:
                if exc.status in (403, 404, 422):
                    continue
                raise
            visible.append(view(row))
        start, size = max(offset, 0), min(max(limit, 1), 200)
        return {"items": visible[start:start + size], "total": len(visible)}
