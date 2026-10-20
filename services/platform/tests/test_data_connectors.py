"""外部连接全部使用Mock，不连接任何既有本地、局域网或远程数据库。"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
import json

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from agent_platform.adapters.native import NativeConnection, OpenWebUIAdapter
from agent_platform.core.context import Actor
from agent_platform.core.database import Base
from agent_platform.core.errors import DomainError
from agent_platform.core.settings import Settings
from agent_platform.modules.data.connectors import ReadConnector, require_approved_target
from agent_platform.modules.data.service import DataService
from agent_platform.modules.data.sql_safety import authorize_sql


def test_unapproved_external_target_is_rejected_without_connection(tmp_path):
    source = SimpleNamespace(provider="postgres", connection_config={"host": "127.0.0.1", "port": 5432, "database": "isolated_fake", "schema_name": "public"}, target_approval={})
    with pytest.raises(DomainError) as error:
        require_approved_target(source, Settings(data_dir=tmp_path))
    assert error.value.code == "source_connections_disabled"
    with pytest.raises(DomainError) as error:
        require_approved_target(source, Settings(data_dir=tmp_path, allow_remote_connections=True))
    assert error.value.code == "source_target_not_approved"


@pytest.mark.parametrize("provider", ["postgres", "mysql"])
def test_readonly_driver_protocol_and_parameter_binding_are_mocked(provider):
    log = []
    class Cursor:
        description = [("value",)]
        def execute(self, sql, parameters=None):
            log.append((sql, parameters))
        def fetchone(self):
            return ([{"Plan": {"Plan Rows": 2}}] if provider == "postgres" else json.dumps({"query_block": {"table": {"rows_examined_per_scan": 2}}}),)
        def fetchmany(self, size):
            return [(Decimal("1.00000000000000000001"),)]
        def close(self):
            log.append(("cursor.close", None))
    class Connection:
        def cursor(self):
            return Cursor()
        def rollback(self):
            log.append(("rollback", None))
        def close(self):
            log.append(("connection.close", None))
    source = SimpleNamespace(provider=provider, connection_config={"host": "192.0.2.10", "port": 5432 if provider == "postgres" else 3306, "database": "fake_business", "schema_name": "public", "username": "readonly"})
    safe = authorize_sql("SELECT value FROM samples WHERE id = :sample", provider=provider, tables=[{"tableName": "samples", "columns": [{"columnName": "id"}, {"columnName": "value"}]}], max_rows=5, parameters={"sample": "A' OR 1=1"}, database="public" if provider == "postgres" else "fake_business")
    result = ReadConnector(sql_factory=lambda *args: Connection()).sql_read(source, safe, "mock-credential")
    assert result["rows"][0]["value"] == "1.00000000000000000001"
    assert any("READ ONLY" in sql for sql, _ in log)
    assert any(sql.startswith("EXPLAIN") for sql, _ in log)
    assert log[-2:] == [("rollback", None), ("connection.close", None)]
    assert any(params == {"sample": "A' OR 1=1"} for _, params in log)


def test_http_get_source_mock_with_local_snapshot_query():
    calls = []
    def respond(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.url.path == "/snapshots"
        return httpx.Response(200, json={"items": [{"id": "A", "value": "12.34"}, {"id": "B", "value": "56.78"}]})
    source = SimpleNamespace(provider="http_json", connection_config={"host": "192.0.2.15", "port": 8443, "tls": True, "endpoint_path": "/snapshots", "response_key": "items"}, schema_snapshot=[{"tableName": "response", "columns": [{"columnName": "id"}, {"columnName": "value"}]}])
    safe = authorize_sql("SELECT value FROM response WHERE id = :id", provider="sqlite", tables=source.schema_snapshot, max_rows=5, parameters={"id": "A"})
    result = ReadConnector(http_transport=httpx.MockTransport(respond)).http_read(source, safe, "mock-token")
    assert result["rows"] == [{"value": "12.34"}]
    assert len(calls) == 1


def test_nl2sql_uses_one_mocked_native_completion_then_authorizes(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "nl2sql.sqlite3").as_posix())
    Base.metadata.create_all(engine)
    actor = Actor("nl-test", "workspace", frozenset({"*"}), ("admin",))
    with Session(engine) as session:
        data = DataService(session, actor, Settings(data_dir=tmp_path / "nl-data"))
        dataset = data.import_csv({"name": "查询样本", "csv_text": "id,value\nA,42\n"})
        calls = []
        def respond(request):
            payload = json.loads(request.content)
            assert payload["stream"] is False
            assert payload["tool_ids"] == []
            calls.append(payload)
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"sql": f'SELECT value FROM "{dataset["table_name"]}" WHERE id = \'A\''})}}], "usage": {"total_tokens": 10}})
        adapter = OpenWebUIAdapter(NativeConnection("http://127.0.0.1:9100", "mock-api-key"), transport=httpx.MockTransport(respond))
        result = data.natural_query({"datasource_id": dataset["datasource_id"], "question": "A样本的值是多少", "model_profile_id": "mock-profile", "max_rows": 10, "execute": True}, lambda profile, messages, **kwargs: adapter.completion("mock-model", messages, **kwargs))
        assert result["rows"] == [{"value": "42"}]
        assert len(calls) == 1
        def unsafe(*args, **kwargs):
            return {"choices": [{"message": {"content": '{"sql":"DELETE FROM samples"}'}}]}
        with pytest.raises(DomainError) as error:
            data.natural_query({"datasource_id": dataset["datasource_id"], "question": "删除数据", "model_profile_id": "mock-profile"}, unsafe)
        assert error.value.code == "unsafe_sql"
        session.rollback()
    engine.dispose()


def test_target_approval_and_mocked_failure_do_not_retry(tmp_path):
    """模拟TimeoutError验证阻断补偿；没有发出实际数据库连接或超时查询。"""
    engine = create_engine("sqlite:///" + (tmp_path / "approval.sqlite3").as_posix())
    Base.metadata.create_all(engine)
    actor = Actor("approval-test", "workspace", frozenset({"*"}), ("admin",))
    settings = Settings(data_dir=tmp_path / "approval-data", allow_remote_connections=True)
    calls = []
    class MockConnector:
        def sql_read(self, *args):
            calls.append("mock-only")
            raise TimeoutError("unit-test simulated timeout; no network")
    with Session(engine) as session:
        service = DataService(session, actor, settings, connector=MockConnector())
        source = service.create_source({"name": "仅用于Mock的配置", "provider": "postgres", "connection_config": {"host": "192.0.2.99", "port": 5432, "database": "fake_only", "schema_name": "public"}, "credential_ref": "fake-reference", "schema_snapshot": [{"tableName": "measurements", "columns": [{"columnName": "value"}]}]})
        assert source["status"] == "pending_approval"
        approval = {"revision": source["revision"], "host": "192.0.2.99", "port": 5432, "database": "fake_only", "service_owner": "测试Mock对象，不对应外部数据库", "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(), "scope": ["query"], "note": "只做配置与Mock协议测试"}
        source = service.approve_target(source["id"], approval)
        session.commit()
        service._password = lambda row: "fake-credential"
        with pytest.raises(DomainError) as failed:
            service.execute_query({"datasource_id": source["id"], "sql": "SELECT value FROM measurements"})
        compensation = failed.value.after_rollback
        session.rollback()
        with Session(engine) as guard, guard.begin():
            compensation(guard)
        with pytest.raises(DomainError) as blocked:
            service.execute_query({"datasource_id": source["id"], "sql": "SELECT value FROM measurements"})
        assert blocked.value.code == "source_blocked"
        assert calls == ["mock-only"]
        row = service.get_source(source["id"])
        with pytest.raises(DomainError):
            service.approve_target(source["id"], {**approval, "revision": row.revision})
        session.rollback()
    engine.dispose()


@pytest.mark.parametrize('provider,tls,port', [('postgres', True, 5432), ('mysql', True, 3306), ('elasticsearch', True, 9200), ('http_json', False, 80)])
def test_default_target_port_is_persisted_for_exact_approval(tmp_path, provider, tls, port):
    """只在新临时SQLite登记离线配置，验证审批目标不会缺少实际默认端口。"""
    engine = create_engine('sqlite:///' + (tmp_path / 'default-target.sqlite3').as_posix())
    Base.metadata.create_all(engine)
    actor = Actor('target-owner', 'workspace', frozenset({'*'}), ('admin',))
    try:
        with Session(engine) as session:
            service = DataService(session, actor, Settings(data_dir=tmp_path / 'owned-data'))
            source = service.create_source({'name': '离线目标配置', 'provider': provider,
                'connection_config': {'host': '127.0.0.1', 'port': None, 'database': 'offline-only', 'tls': tls}})
            assert source['connection_config']['port'] == port
            source = service.approve_target(source['id'], {'revision': source['revision'],
                'host': '127.0.0.1', 'port': port, 'database': 'offline-only', 'service_owner': '仅离线配置，不存在运行数据库',
                'expires_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                'scope': ['metadata'], 'note': '仅验证批准元数据，不连接任何目标服务'})
            assert source['target_approval']['port'] == port
            patched = service.patch_source(source['id'], {'revision': source['revision'],
                'connection_config': {**source['connection_config'], 'port': None}})
            assert patched['connection_config']['port'] == port
            assert patched['status'] == 'pending_approval' and patched['target_approval'] == {}
            session.rollback()
    finally:
        engine.dispose()
