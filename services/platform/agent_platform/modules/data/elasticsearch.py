"""精提Test5 Elasticsearch只读SQL链，固定端点、计划翻译和有限响应。

不接受DSL、脚本、任意路径、跨集群或游标翻页。测试必须注入MockTransport；
真实调用前仍由DataService核验精确目标、服务归属和本次授权范围。
"""
from base64 import b64encode
import json
from time import monotonic
from urllib.parse import quote

import httpx
from sqlglot import expressions as exp

from ...core.errors import DomainError


class ElasticsearchReader:
    def __init__(self, transport=None):
        self.transport = transport

    def client(self, source, credential):
        from .connectors import config_target
        target = config_target('elasticsearch', source.connection_config)
        host = '[' + target['host'] + ']' if ':' in target['host'] else target['host']
        scheme = 'https' if source.connection_config.get('tls', True) else 'http'
        username = source.connection_config.get('username')
        authorization = ('Basic ' + b64encode((username + ':' + credential).encode()).decode()
                         if username else 'ApiKey ' + credential)
        return httpx.Client(base_url=f'{scheme}://{host}:{target["port"]}',
                            headers={'Authorization': authorization, 'Accept': 'application/json'},
                            follow_redirects=False, trust_env=False, timeout=2, transport=self.transport)

    @staticmethod
    def request(client, method, path, body, started):
        remaining = 2 - (monotonic() - started)
        if remaining <= 0:
            raise DomainError(408, 'es_source_timeout', 'Elasticsearch读取超过预算，已停止后续查询')
        with client.stream(method, path, json=body, timeout=remaining) as response:
            if response.status_code != 200:
                raise DomainError(502, 'es_source_rejected', f'Elasticsearch拒绝本次只读请求（HTTP {response.status_code}）')
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > 8 * 1024 * 1024 or monotonic() - started > 2:
                    raise DomainError(408, 'es_source_budget', 'Elasticsearch响应超过预算，已停止后续查询')
                chunks.append(chunk)
        try:
            payload = json.loads(b''.join(chunks))
        except (ValueError, TypeError) as exc:
            raise DomainError(502, 'es_source_invalid', 'Elasticsearch未返回有效JSON') from exc
        if not isinstance(payload, dict):
            raise DomainError(502, 'es_source_invalid', 'Elasticsearch响应结构与协议不符')
        return payload

    def read(self, source, safe, credential):
        from .connectors import ReadConnector, json_value
        index = source.connection_config['database']
        if safe.parameters or safe.tables != (index,):
            raise DomainError(422, 'es_query_scope_invalid', 'Elasticsearch查询只允许已登记的单个索引，暂不接收命名参数')
        if safe.tree.find(exp.AggFunc):
            estimate = next((table.get('rowCount') for table in source.schema_snapshot if table['tableName'] == index), None)
            if not isinstance(estimate, int) or not 0 <= estimate <= 500000:
                raise DomainError(422, 'query_plan_budget_exceeded', '索引聚合代价未知或过大，请先核查范围')
        started = monotonic()
        body = {'query': safe.sql, 'fetch_size': safe.max_rows + 1}
        with self.client(source, credential) as client:
            plan = self.request(client, 'POST', '/_sql/translate', body, started)
            # 翻译只评估DSL，不直接执行它；脚本与运行时字段不进入本项目查询能力。
            if any(key in json.dumps(plan).lower() for key in ('"script"', '"runtime_mappings"', '"script_fields"')):
                raise DomainError(422, 'es_plan_unsupported', '执行计划包含当前未授权的脚本能力')
            result = self.request(client, 'POST', '/_sql?format=json', {**body, 'request_timeout': '2s',
                'page_timeout': '2s', 'allow_partial_search_results': False, 'field_multi_value_leniency': False}, started)
            cursor = result.get('cursor')
            if cursor:
                # 只关闭已分配游标，不获取下一页；这是资源释放，不是重试原查询。
                self.request(client, 'POST', '/_sql/close', {'cursor': cursor}, started)
        columns = [column.get('name') for column in result.get('columns', []) if isinstance(column, dict)]
        rows = result.get('rows', [])
        if (not columns or any(not isinstance(column, str) for column in columns) or len(columns) != len(set(columns))
                or not isinstance(rows, list) or len(rows) > safe.max_rows + 1
                or any(not isinstance(row, list) or len(row) != len(columns) for row in rows)
                or result.get('is_running') or result.get('is_partial')):
            raise DomainError(502, 'es_source_shape_invalid', 'Elasticsearch列、行或结果完整性不符合要求')
        values = [dict(zip(columns, [json_value(value) for value in row])) for row in rows]
        output = ReadConnector._result(columns, values, safe, plan, started, {'plan_type': 'elasticsearch_sql_translate'})
        output['truncated'] = output['truncated'] or bool(cursor)
        return output

    def metadata(self, source, credential):
        index = source.connection_config['database']
        with self.client(source, credential) as client:
            result = self.request(client, 'GET', '/' + quote(index, safe='') + '/_mapping', None, monotonic())
        if set(result) != {index}:
            raise DomainError(502, 'es_source_mapping_scope', '索引映射范围与登记目标不一致')
        columns = []
        def walk(properties, prefix='', depth=0):
            if not isinstance(properties, dict) or depth > 5:
                raise DomainError(422, 'es_mapping_budget', '索引字段结构超过预算')
            for name, data in properties.items():
                if not isinstance(data, dict):
                    raise DomainError(502, 'es_source_mapping_invalid', '索引字段定义无效')
                field = prefix + name
                if 'properties' in data:
                    walk(data['properties'], field + '.', depth + 1)
                else:
                    columns.append({'columnName': field, 'dataType': data.get('type', 'keyword')})
                if len(columns) > 100:
                    raise DomainError(422, 'es_mapping_budget', '索引字段超过100列，请缩小映射范围')
        walk(result[index].get('mappings', {}).get('properties', {}))
        return [{'tableName': index, 'columns': columns}]
