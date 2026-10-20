"""Elasticsearch只读协议使用Mock验证，不连接任何本地或远程ES服务。"""
import json
from types import SimpleNamespace

import httpx
import pytest

from agent_platform.core.errors import DomainError
from agent_platform.modules.data.connectors import ReadConnector, config_target
from agent_platform.modules.data.sql_safety import authorize_sql
from agent_platform.modules.data.service import DataService


def source():
    return SimpleNamespace(provider='elasticsearch', connection_config={'host': '192.0.2.20', 'port': 9200,
        'database': 'quality-records', 'username': '', 'tls': True},
        schema_snapshot=[{'tableName': 'quality-records', 'columns': [{'columnName': 'sample'}, {'columnName': 'value'}]}])


def test_elasticsearch_translates_before_select_and_does_not_page_cursor():
    calls = []
    def respond(request):
        calls.append(request)
        assert request.headers['authorization'] == 'ApiKey isolated-mock-key'
        assert request.method == 'POST'
        if request.url.path == '/_sql/translate':
            return httpx.Response(200, json={'size': 3, 'fields': [{'field': 'value'}]})
        if request.url.path == '/_sql/close':
            assert json.loads(request.content) == {'cursor': 'fake-cursor'}
            return httpx.Response(200, json={'succeeded': True})
        assert request.url.path == '/_sql'
        body = json.loads(request.content)
        assert body['request_timeout'] == '2s' and body['allow_partial_search_results'] is False
        return httpx.Response(200, json={'columns': [{'name': 'value'}], 'rows': [[42], [43]], 'cursor': 'fake-cursor'})
    registered = source()
    safe = authorize_sql('SELECT value FROM `quality-records`', provider='elasticsearch',
                         tables=registered.schema_snapshot, max_rows=2)
    result = ReadConnector(http_transport=httpx.MockTransport(respond)).es_read(registered, safe, 'isolated-mock-key')
    assert [request.url.path for request in calls] == ['/_sql/translate', '/_sql', '/_sql/close']
    assert result['rows'] == [{'value': 42}, {'value': 43}]
    assert result['truncated'] is True


def test_elasticsearch_unknown_aggregation_cost_is_rejected_before_network():
    registered = source()
    safe = authorize_sql('SELECT COUNT(*) AS total FROM `quality-records`', provider='elasticsearch',
                         tables=registered.schema_snapshot, max_rows=2)
    def denied(request):
        raise AssertionError('未知代价不能发出HTTP请求')
    with pytest.raises(DomainError) as error:
        ReadConnector(http_transport=httpx.MockTransport(denied)).es_read(registered, safe, 'isolated-mock-key')
    assert error.value.code == 'query_plan_budget_exceeded'


def test_elasticsearch_mapping_is_single_index_and_preserves_nested_field_names():
    registered = source()
    def respond(request):
        assert request.method == 'GET' and request.url.path == '/quality-records/_mapping'
        return httpx.Response(200, json={'quality-records': {'mappings': {'properties': {
            'sample': {'type': 'keyword'}, 'device': {'properties': {'temperature': {'type': 'double'}}}}}}})
    result = ReadConnector(http_transport=httpx.MockTransport(respond)).metadata(registered, 'isolated-mock-key')
    assert [column['columnName'] for column in result[0]['columns']] == ['sample', 'device.temperature']
    with pytest.raises(DomainError):
        config_target('elasticsearch', {**registered.connection_config, 'database': '*quality*'})


def test_elasticsearch_script_plan_does_not_dispatch_query():
    registered, calls = source(), []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={'script_fields': {'value': {'script': 'unsafe'}}})
    safe = authorize_sql('SELECT value FROM `quality-records`', provider='elasticsearch',
                         tables=registered.schema_snapshot, max_rows=2)
    with pytest.raises(DomainError) as error:
        ReadConnector(http_transport=httpx.MockTransport(respond)).es_read(registered, safe, 'isolated-mock-key')
    assert error.value.code == 'es_plan_unsupported' and len(calls) == 1


def test_metadata_protocol_failure_isolated_without_retry(monkeypatch):
    """只用Mock服务异常验证阻断补偿的派发，不建立数据库或HTTP连接。"""
    registered = source()
    registered.id, registered.status = 'mock-source', 'active'
    calls = []
    def metadata(*args):
        calls.append('metadata')
        raise DomainError(408, 'es_source_timeout', '模拟的协议超时，未发送实际请求')
    service = DataService(None, SimpleNamespace(workspace_id='mock-workspace'),
                          connector=SimpleNamespace(metadata=metadata))
    monkeypatch.setattr(service, 'get_source', lambda row_id: registered)
    monkeypatch.setattr(service, '_password', lambda item: 'mock-only')
    monkeypatch.setattr('agent_platform.modules.data.service.require_approved_target', lambda *args: None)
    with pytest.raises(DomainError) as error:
        service.sync_metadata(registered.id)
    assert error.value.status == 408 and error.value.code == 'query_failed'
    assert callable(error.value.after_rollback) and calls == ['metadata']


def test_missing_credential_does_not_mark_uncontacted_source_failed(monkeypatch):
    registered = source()
    registered.id, registered.status = 'mock-source', 'active'
    def forbidden(*args):
        raise AssertionError('缺少凭据时不能派发外部连接')
    service = DataService(None, SimpleNamespace(workspace_id='mock-workspace'),
                          connector=SimpleNamespace(metadata=forbidden))
    monkeypatch.setattr(service, 'get_source', lambda row_id: registered)
    def missing(item):
        raise DomainError(503, 'datasource_credential_missing', '请配置凭据')
    monkeypatch.setattr(service, '_password', missing)
    monkeypatch.setattr('agent_platform.modules.data.service.require_approved_target', lambda *args: None)
    with pytest.raises(DomainError) as error:
        service.sync_metadata(registered.id)
    assert error.value.code == 'datasource_credential_missing'
    assert not hasattr(error.value, 'after_rollback')
