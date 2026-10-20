"""小量单条查询验证AST、字段权限与CSV导入预算。"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from agent_platform.core.context import Actor
from agent_platform.core.database import Base
from agent_platform.core.errors import DomainError
from agent_platform.core.settings import Settings
from agent_platform.modules.data.service import DataService
from agent_platform.modules.data.sql_safety import authorize_sql
from agent_platform.modules.data import models as m


@pytest.fixture
def context(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "controlled-test.sqlite3").as_posix())
    Base.metadata.create_all(engine)
    actor = Actor("test-user", "test-workspace", frozenset({"*"}), ("admin",))
    settings = Settings(data_dir=tmp_path / "isolated-data")
    with Session(engine) as session:
        yield DataService(session, actor, settings), session, settings
        session.rollback()
    engine.dispose()


@pytest.mark.parametrize("sql", ["DELETE FROM production", "SELECT * FROM sqlite_master", "SELECT load_extension('abc')", "SELECT * FROM production; SELECT 1", "WITH RECURSIVE a(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM a) SELECT * FROM a"])
def test_unsafe_sql_rejected_before_connection(sql):
    with pytest.raises(DomainError):
        authorize_sql(sql, provider="sqlite", tables=[], max_rows=5)


def test_csv_query_parameters_and_field_revocation(context):
    data, session, settings = context
    dataset = data.import_csv({"name": "样本", "csv_text": "sample_id,value,private_note\nA,10,secret\nB,20,secret\n"})
    table = dataset["table_name"]
    result = data.execute_query({"datasource_id": dataset["datasource_id"], "sql": f'SELECT sample_id,value FROM "{table}" WHERE value = :value', "parameters": {"value": "10"}, "max_rows": 5})
    assert result["rows"] == [{"sample_id": "A", "value": "10"}]
    assert result["plan"]
    source = data.get_source(dataset["datasource_id"])
    data.patch_source(source.id, {"revision": source.revision, "field_roles": {table: {"private_note": ["manager"]}}})
    reader = DataService(session, Actor("reader", "test-workspace", frozenset({"m06.read"}), ("operator",)), settings)
    assert "private_note" not in [c["columnName"] for c in reader.metadata(source.id)["tables"][0]["columns"]]
    with pytest.raises(DomainError):
        reader.execute_query({"datasource_id": source.id, "sql": f'SELECT private_note FROM "{table}"'})
    assert reader.get_query(result["id"]).result["rows"][0]["value"] == "10"
    outsider = DataService(session, Actor("other", "other-workspace", frozenset({"*"})), settings)
    with pytest.raises(DomainError) as error:
        outsider.get_source(source.id)
    assert error.value.status == 404


def test_snapshot_field_access_is_revoked_and_processing_is_real(context):
    data, session, settings = context
    dataset = data.import_csv({"name": "去重", "csv_text": "id,note\n1,a\n1,a\n2,\n"})
    table = dataset["table_name"]
    query = data.execute_query({"datasource_id": dataset["datasource_id"], "sql": f'SELECT * FROM "{table}"'})
    processed = data.process({"name": "清洗", "query_id": query["id"], "operations": [{"type": "drop_null", "field": "note"}, {"type": "deduplicate", "fields": ["id"]}]})
    assert processed["result"]["rows"] == [{"id": "1", "note": "a"}]
    assert data.quality_check({"query_id": query["id"], "required_fields": ["note"], "unique_fields": ["id"]})["outcome"] == "failed"
    source = data.get_source(dataset["datasource_id"])
    data.patch_source(source.id, {"revision": source.revision, "field_roles": {table: {"note": ["manager"]}}})
    reader = DataService(session, Actor("reader", "test-workspace", frozenset({"m06.read"}), ("operator",)), settings)
    with pytest.raises(DomainError):
        reader.get_query(query["id"])


def test_csv_and_query_limit_fail_closed(context):
    data, _, _ = context
    with pytest.raises(DomainError):
        data.import_csv({"name": "坏表", "csv_text": "a,a\n1,2\n"})
    dataset = data.import_csv({"name": "上限", "csv_text": "a\n1\n2\n3\n"})
    query = data.execute_query({"datasource_id": dataset["datasource_id"], "sql": f'SELECT * FROM "{dataset["table_name"]}"', "max_rows": 2})
    assert query["row_count"] == 2
    assert query["truncated"] is True
    with pytest.raises(DomainError):
        data.process({"name": "不完整数据", "query_id": query["id"], "operations": [{"type": "deduplicate"}]})
