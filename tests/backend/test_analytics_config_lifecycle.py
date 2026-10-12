"""用独立平台事务和假引擎验证配置收窄后旧任务不能恢复旧授权或旧连接。"""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app import db as database
from app.errors import ConflictError, ToolExecutionError
from app.main import create_app
from app.models import DataSource
from app.modules.analytics import connection_pool as pools, connections, metadata, semantic, sources
from app.modules.analytics.models import SourceMetadata, SourceSemanticDocument
from test_analytics import source


@pytest.fixture
def client(monkeypatch, tmp_path):
    """文件 SQLite 保证两个真实事务使用不同物理连接，不复用内存 StaticPool。"""
    engine = create_engine("sqlite:///" + (tmp_path / "platform.sqlite3").as_posix(),
                           connect_args={"check_same_thread": False})
    event.listen(engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
    monkeypatch.setattr(database, "_engine", engine)
    monkeypatch.setattr(database, "_SessionLocal", sessionmaker(
        bind=engine, autoflush=False, expire_on_commit=False))
    try:
        with TestClient(create_app(), raise_server_exceptions=False) as application:
            yield application
    finally:
        engine.dispose()


@pytest.mark.parametrize("stage", ["inspection", "embedding", "automatic_failure", "cached_index"])
@pytest.mark.parametrize("edit", ["narrow", "disable", "delete"])
def test_old_metadata_cannot_overwrite_formal_permission_edit(
    client, administrator, source, db, monkeypatch, stage, edit,
):
    row = db.get(DataSource, source["id"])
    old_tables = deepcopy(db.scalar(select(SourceMetadata).where(
        SourceMetadata.datasource_id == row.id)).tables_json)
    ctx = SimpleNamespace(db=db, workspace_id=row.workspace_id)
    old_connection = db.connection().connection.driver_connection
    update_connections = []
    actual_auto_sync = sources._automatic_metadata_sync
    monkeypatch.setattr(sources, "_automatic_metadata_sync", lambda *_: None)
    monkeypatch.setattr(semantic, "extract_relations", lambda *_args, **_kwargs: [])
    changed = []

    def edit_current():
        def record_update(connection, cursor, sql, *_args):
            if sql.startswith("UPDATE data_sources SET"):
                update_connections.append(connection.connection.driver_connection)
        event.listen(database.get_engine(), "before_cursor_execute", record_update)
        try:
            url = "/api/v1/analytics/datasources/" + row.id
            if edit == "delete":
                response = client.delete(url, headers=administrator)
            else:
                response = client.put(url, headers=administrator, json={
                    "name": "已收窄权限", "type": "sqlite", "databaseName": source["databaseName"],
                    "allowedTables": ["sales"], "allowedColumns": {"sales": ["id"]}, "version": 1,
                    "enabled": edit != "disable",
                })
            assert response.status_code == 200, response.text
            if edit != "delete":
                assert response.json()["data"]["version"] == 2
        finally:
            event.remove(database.get_engine(), "before_cursor_execute", record_update)
        changed.append(True)

    def inspect_old(*_args):
        if stage != "embedding":
            edit_current()
        if stage == "automatic_failure":
            raise ToolExecutionError("旧版本结构同步失败")
        return deepcopy(old_tables)

    def embed_old(texts, **_kwargs):
        edit_current()
        return [[] for _ in texts]

    monkeypatch.setattr(metadata, "inspect_sql_source", inspect_old)
    if stage == "embedding":
        monkeypatch.setattr(semantic.model, "embed_texts", embed_old)
    failure = None
    try:
        if stage == "automatic_failure":
            actual_auto_sync(ctx, row)
        elif stage == "cached_index":
            edit_current()
            semantic.build_semantic_index(ctx, row, old_tables, cached_only=True)
            db.commit()
        else:
            metadata.source_metadata(ctx, row, refresh=True)
            db.commit()
    except ConflictError as exc:
        failure = exc
        db.rollback()
    with database.new_session() as observer:
        saved = observer.get(DataSource, source["id"])
        assert saved.connection_config["version"] == 2
        if edit != "delete":
            assert saved.connection_config["allowedColumns"] == {"sales": ["id"]}
        assert saved.status == {"narrow": "active", "disable": "disabled", "delete": "deleted"}[edit]
        assert saved.connection_config.get("metadataStatus") != "failed"
        snapshot = observer.scalar(select(SourceMetadata).where(SourceMetadata.datasource_id == row.id))
        assert snapshot.source_version == 1
        assert all(item.source_version == 1 for item in observer.scalars(select(SourceSemanticDocument).where(
            SourceSemanticDocument.datasource_id == row.id)))
    assert changed == [True]
    assert update_connections and all(item is not old_connection for item in update_connections)
    if stage != "automatic_failure":
        assert isinstance(failure, ConflictError), "过期任务必须返回配置冲突，不能报告同步成功"


@pytest.fixture
def engines(monkeypatch):
    pools.dispose_read_pools()
    created = []

    def factory(*_args, **_kwargs):
        engine = Mock()
        engine.pool.checkedout.return_value = 0
        created.append(engine)
        return engine

    monkeypatch.setattr(connections, "create_engine", factory)
    monkeypatch.setattr(connections, "connection_password", lambda *_: "isolated-password")
    try:
        yield created
    finally:
        # 断言失败也要释放本夹具模拟的占用，避免假忙池污染后续用例；不触碰真实连接。
        for engine in created:
            engine.pool.checkedout.return_value = 0
        pools.dispose_read_pools()


def pool_source(version):
    return DataSource(id="lifecycle-source", workspace_id="lifecycle-workspace", provider="mysql",
                      status="active", connection_config={"host": "127.0.0.1", "databaseName": "fixture",
                                                          "version": version})


@pytest.mark.parametrize("keep_old_busy", [False, True])
def test_late_old_generation_neither_reopens_nor_retires_current(engines, keep_old_busy):
    old, new = pool_source(1), pool_source(2)
    with connections.sql_engine(None, old) as engine1:
        engine1.pool.checkedout.return_value = int(keep_old_busy)
    with connections.sql_engine(None, new) as engine2:
        pass
    before = len(engines)
    with pytest.raises(ToolExecutionError):
        with connections.sql_engine(None, old):
            pytest.fail("旧任务重新取得了旧配置引擎")
    assert len(engines) == before
    engine2.dispose.assert_not_called()
    with connections.sql_engine(None, new) as current:
        assert current is engine2
    engine1.pool.checkedout.return_value = 0
    pools.reap_idle_pools()


def test_edit_floor_blocks_never_created_old_pool_and_stale_retirement_preserves_new(engines):
    old, new = pool_source(1), pool_source(3)
    pools.dispose_source_pools(old.workspace_id, old.id, minimum_version=2)
    with pytest.raises(ToolExecutionError), connections.sql_engine(None, old):
        pass
    assert engines == []
    with connections.sql_engine(None, new) as engine:
        pass
    pools.dispose_source_pools(new.workspace_id, new.id, minimum_version=2)
    engine.dispose.assert_not_called()
    with connections.sql_engine(None, new) as reused:
        assert reused is engine
    assert len(engines) == 1


def test_idle_reaping_keeps_generation_barrier_and_workspace_isolation(engines, monkeypatch):
    old, new = pool_source(1), pool_source(2)
    with connections.sql_engine(None, new):
        pass
    monkeypatch.setattr(pools, "IDLE_SECONDS", -1)
    pools.reap_idle_pools()
    assert not pools._pools
    with pytest.raises(ToolExecutionError), connections.sql_engine(None, old):
        pass
    assert len(engines) == 1
    old.workspace_id = "another-workspace"
    with connections.sql_engine(None, old):
        pass
    with connections.sql_engine(None, new):
        pass
    assert len(engines) == 3


@pytest.mark.parametrize("mode", ["refresh", "cached_index", "failure", "legacy"])
def test_current_metadata_sync_and_failure_preserve_configuration(
    client, administrator, source, db, monkeypatch, mode,
):
    row = db.get(DataSource, source["id"])
    if mode == "legacy":
        row.connection_config = {key: value for key, value in row.connection_config.items() if key != "version"}
        db.commit()
    before = deepcopy(row.connection_config)
    ctx = SimpleNamespace(db=db, workspace_id=row.workspace_id)
    if mode == "failure":
        def fail(*_args, **_kwargs):
            raise ToolExecutionError("隔离结构失败")
        monkeypatch.setattr(metadata, "inspect_sql_source", fail)
        sources._automatic_metadata_sync(ctx, row)
        assert row.connection_config["metadataStatus"] == "failed"
    elif mode == "cached_index":
        tables = db.scalar(select(SourceMetadata).where(SourceMetadata.datasource_id == row.id)).tables_json
        semantic.build_semantic_index(ctx, row, tables, cached_only=True)
        db.commit()
        assert row.connection_config["semanticIndexStatus"] == "keyword_only"
    else:
        metadata.source_metadata(ctx, row, refresh=True)
        db.commit()
        assert row.connection_config["metadataStatus"] == "ready"
        assert isinstance(row.connection_config["businessDomains"], list)
    from app.modules.analytics.profile_updates import PROFILE_FIELDS
    assert {key: value for key, value in row.connection_config.items() if key not in PROFILE_FIELDS} == {
        key: value for key, value in before.items() if key not in PROFILE_FIELDS}


def test_profile_patch_preserves_other_same_version_fields_and_rejects_config_keys(
    client, administrator, source, db,
):
    from app.modules.analytics.profile_updates import update_profile
    from app.errors import ValidationError
    row = db.get(DataSource, source["id"])
    with database.new_session() as concurrent:
        current = concurrent.get(DataSource, row.id)
        current.connection_config = {**current.connection_config, "localNote": "另一个事务的新说明"}
        concurrent.commit()
    update_profile(db, row, {"metadataStatus": "ready", "businessDomains": ["设备", "质量"]}, expected_version=1)
    db.commit()
    assert row.connection_config["localNote"] == "另一个事务的新说明"
    assert row.connection_config["businessDomains"] == ["设备", "质量"]
    for invalid in ["version", "host", "allowedTables", "allowedColumns"]:
        with pytest.raises(ValidationError):
            update_profile(db, row, {invalid: "禁止改写"}, expected_version=1)


def test_mysql_profile_patch_has_atomic_version_predicate_and_no_full_config_payload():
    from sqlalchemy.dialects import mysql
    from app.modules.analytics.profile_updates import _profile_statement
    statement = _profile_statement(pool_source(2), {"businessDomains": ["设备"]}, 2, "mysql")
    compiled = statement.compile(dialect=mysql.dialect())
    sql = str(compiled)
    assert "json_set(data_sources.connection_config" in sql
    assert "JSON_EXTRACT(data_sources.connection_config" in sql
    assert "data_sources.status =" in sql
    assert "data_sources.workspace_id =" in sql
    assert '"设备"' in " ".join(str(value) for value in compiled.params.values())
    assert "allowedColumns" not in str(compiled.params)


def test_late_sync_keeps_newer_formally_saved_metadata_and_semantic_documents(
    client, administrator, source, db, monkeypatch,
):
    row = db.get(DataSource, source["id"])
    original_tables = deepcopy(db.scalar(select(SourceMetadata).where(
        SourceMetadata.datasource_id == row.id)).tables_json)
    ctx = SimpleNamespace(db=db, workspace_id=row.workspace_id)
    monkeypatch.setattr(semantic, "extract_relations", lambda *_args, **_kwargs: [])
    edit_responses = []

    def inspect_interleaved(_db, current):
        if connections.source_version(current) == 2:
            return metadata._column_scope(current, deepcopy(original_tables))
        response = client.put("/api/v1/analytics/datasources/" + row.id, headers=administrator, json={
            "name": "新授权及目录已提交", "type": "sqlite", "databaseName": source["databaseName"],
            "allowedTables": ["sales"], "allowedColumns": {"sales": ["id"]}, "version": 1,
        })
        assert response.status_code == 200, response.text
        assert response.json()["data"]["metadataStatus"] == "ready"
        edit_responses.append(response.status_code)
        return deepcopy(original_tables)

    monkeypatch.setattr(metadata, "inspect_sql_source", inspect_interleaved)
    with pytest.raises(ConflictError):
        metadata.source_metadata(ctx, row, refresh=True)
    assert edit_responses == [200]
    with database.new_session() as observer:
        snapshot = observer.scalar(select(SourceMetadata).where(SourceMetadata.datasource_id == source["id"]))
        assert snapshot.source_version == 2
        assert [column["columnName"] for column in snapshot.tables_json[0]["columns"]] == ["id"]
        documents = observer.scalars(select(SourceSemanticDocument).where(
            SourceSemanticDocument.datasource_id == source["id"])).all()
        assert documents and all(document.source_version == 2 for document in documents)
        assert all("department" not in document.document_text for document in documents)
