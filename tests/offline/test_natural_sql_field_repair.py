"""离线校验自然语言查询的 SQL/字段纠正预算与策略边界。

验证行为：非法 SQL 或字段在执行前被纠正；重复出错时纠正次数有上限（两次调用预算）；
策略违规不重试也不放宽；模型超时不重试；校验保留完整授权表结构。
隔离方式：Mock 模型响应 + SimpleNamespace 上下文，断言 queries 模块的决策，不连库、不执行 SQL。
边界：修复只针对语法/字段，策略与超时类错误必须直接失败。
"""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.errors import ModelError, ValidationError
from app.modules.analytics import queries
from app.modules.analytics.schemas import QueryInput
from app.modules.analytics.sql_safety import SqlFieldValidationError


TABLES = [{'tableName': 'inventory', 'columns': [{'columnName': 'name'}, {'columnName': 'stock'}]}]


@pytest.mark.parametrize('invalid', [
    {'content': '这里是查询结果'}, {'content': '{}'}, {'content': '{"sql": []}'},
    {'content': '{"sql":"SELECT name FROM inventory"}', 'finish_reason': 'length'},
])
def test_invalid_sql_output_is_corrected_before_execution(monkeypatch, invalid):
    model, run = generate(monkeypatch, [invalid, response('SELECT name FROM inventory')])
    sql, usage = run()
    assert sql == 'SELECT name FROM inventory' and model.call_count == 2
    assert usage['sqlRepairs'] == 1
    assert '数据库尚未执行' in model.call_args.args[1][-1]['content']


def test_repeated_missing_sql_has_bounded_failure(monkeypatch):
    model, run = generate(monkeypatch, [{'content': '{}'}] * 2)
    with pytest.raises(ModelError):
        run()
    assert model.call_count == 2


def response(sql):
    return {'content': json.dumps({'sql': sql, 'explanation': '库存明细'}),
            'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}}


def generate(monkeypatch, responses):
    model = Mock(side_effect=responses)
    monkeypatch.setattr(queries.model, 'chat_for_run', model)
    source = SimpleNamespace(id='source', provider='mysql', connection_config={'databaseName': 'fixture'})
    ctx = SimpleNamespace(workspace_id='workspace')
    payload = QueryInput(datasourceId='source', query='查询备品备件库存', mode='natural_language')
    return model, lambda: queries.natural_language_sql(ctx, source, payload, TABLES)


def test_invalid_field_is_corrected_before_any_database_execution(monkeypatch):
    model, run = generate(monkeypatch, [response('SELECT missing_name, stock FROM inventory'),
                                       response('SELECT name, stock FROM inventory')])
    sql, usage = run()
    assert sql == 'SELECT name, stock FROM inventory'
    assert model.call_count == 2 and usage['sqlRepairs'] == 1
    assert usage['usage']['total_tokens'] == 30
    correction = json.loads(model.call_args.args[1][-1]['content'])
    assert 'missing_name' in correction['validationError']
    assert '数据库尚未执行' in correction['instruction']


def test_repeated_invalid_field_stops_after_one_correction(monkeypatch):
    model, run = generate(monkeypatch, [response('SELECT missing_name FROM inventory')] * 2)
    with pytest.raises(SqlFieldValidationError):
        run()
    assert model.call_count == 2


@pytest.mark.parametrize('invalid', ['SELECT name FROM inventory WHERE (', "SELECT name FROM inventory WHERE name = 'open"])
def test_invalid_sql_grammar_is_corrected_before_execution(monkeypatch, invalid):
    model, run = generate(monkeypatch, [response(invalid), response('SELECT name FROM inventory')])
    sql, usage = run()
    assert sql == 'SELECT name FROM inventory'
    assert model.call_count == 2 and usage['sqlRepairs'] == 1
    assert '数据库尚未执行' in model.call_args.args[1][-1]['content']


def test_repeated_invalid_grammar_keeps_two_call_budget(monkeypatch):
    model, run = generate(monkeypatch, [response('SELECT name FROM inventory WHERE (')] * 2)
    with pytest.raises(ValidationError):
        run()
    assert model.call_count == 2


@pytest.mark.parametrize('sql', [
    'DELETE FROM inventory',
    'SELECT name FROM unauthorized',
    'SELECT SLEEP(5) FROM inventory',
    'SELECT name FROM other_database.inventory',
])
def test_policy_violations_are_not_retried_or_relaxed(monkeypatch, sql):
    model, run = generate(monkeypatch, [response(sql)])
    with pytest.raises(ValidationError):
        run()
    assert model.call_count == 1


def test_model_timeout_is_not_retried(monkeypatch):
    model, run = generate(monkeypatch, [TimeoutError('model timeout')])
    with pytest.raises(ModelError):
        run()
    assert model.call_count == 1 and model.call_args.kwargs['max_retries'] == 0


def test_validation_preserves_the_full_authorized_schema(monkeypatch):
    model = Mock(return_value=response('SELECT name FROM warehouses'))
    monkeypatch.setattr(queries.model, 'chat_for_run', model)
    source = SimpleNamespace(id='source', provider='mysql', connection_config={'databaseName': 'fixture'})
    ctx = SimpleNamespace(workspace_id='workspace')
    payload = QueryInput(datasourceId='source', query='查看仓库', mode='natural_language')
    authorized = TABLES + [{'tableName': 'warehouses', 'columns': [{'columnName': 'name'}]}]
    sql, usage = queries.natural_language_sql(ctx, source, payload, TABLES, authorization_tables=authorized)
    assert sql == 'SELECT name FROM warehouses' and usage['sqlRepairs'] == 0
    assert model.call_count == 1


@pytest.mark.parametrize('repair', [True, False])
def test_incomplete_planning_is_repaired_without_sql_execution(monkeypatch, repair):
    from app.errors import ModelResponseIncompleteError
    incomplete = ModelResponseIncompleteError('模型输出不完整', reason='truncated_response', retryable=True)
    model, run = generate(monkeypatch, [incomplete, response('SELECT name FROM inventory') if repair else incomplete])
    if repair:
        assert run()[0] == 'SELECT name FROM inventory'
    else:
        with pytest.raises(ModelResponseIncompleteError):
            run()
    assert model.call_count == 2
    assert all(c.kwargs['request_timeout'] is None for c in model.call_args_list)
    assert '数据库尚未执行' in model.call_args.args[1][-1]['content']
