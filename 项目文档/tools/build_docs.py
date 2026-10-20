"""从项目范围和接口基线生成目录、OpenAPI与统计，并检查交叉引用。"""

from collections import Counter
from pathlib import Path
import csv
import json
import re

ROOT = Path(__file__).resolve().parents[1]


def write_json(path, value):
    """统一保存可复核的UTF-8文档数据。"""
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def write_csv(path, rows):
    """导出可用Excel打开的目录，生成文件不作为手工维护入口。"""
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def fields_schema(fields, partial=False):
    """将带必填标记的字段定义转换为JSON Schema。"""
    properties, required = {}, []
    for name, value in fields.items():
        properties[name] = {key: item for key, item in value.items() if key != 'required'}
        if value.get('required') and not partial:
            required.append(name)
    schema = {'type': 'object', 'additionalProperties': False, 'properties': properties}
    if required:
        schema['required'] = required
    if partial:
        schema['minProperties'] = 1
    return schema


def sync_markdown_counts(summary):
    """从事实源刷新正文数量，固定段落缺失时失败，防止旧数字静默保留。"""
    total = summary['api_count']
    feature = summary['feature_count']
    page = summary['page_count']
    module = summary['module_count']
    types = summary['api_implementation_types']
    adaptations = types.get('复用适配', 0)
    business = types.get('新增聚合或业务实现', 0)
    table = ['| 模块 | 功能 | 页面 | 平台接口 |', '|---|---:|---:|---:|']
    for item in summary['modules']:
        table.append(f"| {item['id']} {item['name']} | {item['features']} | {item['pages']} | {item['apis']} |")
    table.append(f'| **合计** | **{feature}** | **{page}** | **{total}** |')
    rules = {
        'README.md': [
            (r'\*\*规划规模：[^\n]+?\*\*', f'**规划规模：{module} 个模块、{feature} 项可验收功能、{page} 个逻辑页面、{total} 项平台业务接口契约。**'),
            (r'\| 模块 \| 功能 \| 页面 \| 平台接口 \|\n[\s\S]*?\| \*\*合计\*\*[^\n]+', '\n'.join(table)),
            (r'\d+项中，\d+项为平台聚合/领域实现，\d+项为来源或存储适配。\*\*适配仍需开发和测试，不代表\d+项已可直接使用。\*\* 原生聊天与Dify的全部接口继续由原项目维护；\d+不是整系统物理接口总数。', f'{total}项中，{business}项为平台聚合/领域实现，{adaptations}项为来源或存储适配。**适配仍需开发和测试，不代表{adaptations}项已可直接使用。** 原生聊天与Dify的全部接口继续由原项目维护；{total}不是整系统物理接口总数。'),
            (r'，\d+项功能首次交付', f"，{summary['feature_phases'].get('P1', 0)}项功能首次交付"),
            (r'\| P2 完整集成 \| \d+项功能', f"| P2 完整集成 | {summary['feature_phases'].get('P2', 0)}项功能"),
            (r'\| P3 成熟交付 \| \d+项后续功能', f"| P3 成熟交付 | {summary['feature_phases'].get('P3', 0)}项后续功能"),
        ],
        '01_产品功能与UX.md': [
            (r'本版规划包含 \*\*\d+ 个业务模块、\d+ 项可验收功能、\d+ 个逻辑页面\*\*', f'本版规划包含 **{module} 个业务模块、{feature} 项可验收功能、{page} 个逻辑页面**'),
        ],
        '03_开发计划与验收.md': [
            (r'规划功能 \d+ 项、用户可到达页面 \d+ 个、平台接口 \d+ 个', f'规划功能 {feature} 项、用户可到达页面 {page} 个、平台接口 {total} 个'),
        ],
    }
    for filename, changes in rules.items():
        path = ROOT / filename
        original = path.read_text(encoding='utf-8')
        content = original
        for pattern, replacement in changes:
            content, matches = re.subn(pattern, lambda _: replacement, content)
            if matches != 1:
                raise ValueError(f'数量摘要定位失败：{filename}/{pattern}，命中{matches}处')
        if content != original:
            path.write_text(content, encoding='utf-8')


def main():
    """生成目录并在发现重复编号、悬空引用或数量漂移时退出失败。"""
    scope = json.loads((ROOT / '范围基线.json').read_text(encoding='utf-8'))
    api = json.loads((ROOT / 'contracts/接口基线.json').read_text(encoding='utf-8'))
    modules = scope['modules']
    features = [dict(item, module_id=module['id'], module_name=module['name']) for module in modules for item in module['features']]
    pages = [dict(item, module_id=module['id'], module_name=module['name']) for module in modules for item in module['pages']]
    operations = api['operations']
    assert len({x['id'] for x in modules}) == len(modules), '模块ID重复'
    assert len({x['id'] for x in features}) == len(features), '功能ID重复'
    assert len({x['id'] for x in pages}) == len(pages), '页面ID重复'
    assert len({x['route'] for x in pages}) == len(pages), '逻辑路由重复'
    assert len({(x['method'], x['path']) for x in operations}) == len(operations), '接口方法路径重复'
    assert len({x['operation_id'] for x in operations}) == len(operations), 'operationId重复'
    assert len({x['id'] for x in operations}) == len(operations), '接口ID重复'
    feature_map = {x['id']: x for x in features}
    page_map = {x['id']: x for x in pages}
    module_map = {x['id']: x for x in modules}
    for item in features:
        assert item['page_ids'] and all(pid in page_map for pid in item['page_ids']), f'功能页面引用缺失：{item["id"]}'
        for pid in item['page_ids']:
            assert item['id'] in page_map[pid]['feature_ids'], f'页面与功能反向引用不一致：{pid}/{item["id"]}'
        for key in ('role', 'trigger', 'inputs', 'outputs', 'acceptance', 'implementation', 'source', 'phase'):
            assert item.get(key), f'功能字段缺失：{item["id"]}/{key}'
    for item in pages:
        assert item['feature_ids'] and all(fid in feature_map for fid in item['feature_ids']), f'页面功能引用缺失：{item["id"]}'
    for item in operations:
        assert item['module_id'] in module_map, '接口模块不存在'

    schemas = {
        'Resource': {'type': 'object', 'additionalProperties': False, 'required': ['id', 'version', 'workspace_id'], 'properties': {'id': {'type': 'string'}, 'version': {'type': 'integer', 'minimum': 1}, 'workspace_id': {'type': 'string'}, 'name': {'type': 'string'}, 'status': {'type': 'string', 'description': '领域状态，按具体资源状态机校验'}, 'created_at': {'type': 'string', 'format': 'date-time'}, 'updated_at': {'type': 'string', 'format': 'date-time'}}},
        'TaskRef': {'type': 'object', 'additionalProperties': False, 'required': ['task_id', 'state', 'trace_id'], 'properties': {'task_id': {'type': 'string'}, 'state': {'type': 'string', 'enum': ['queued', 'running', 'waiting_input', 'cancel_requested', 'cancelled', 'succeeded', 'partial_success', 'failed', 'unknown']}, 'engine': {'type': 'string', 'enum': ['platform', 'dify', 'openwebui', 'qknow', 'analytics']}, 'upstream_run_id': {'type': ['string', 'null']}, 'resource_id': {'type': ['string', 'null']}, 'trace_id': {'type': 'string'}}},
        'Problem': {'type': 'object', 'required': ['type', 'title', 'status', 'trace_id'], 'properties': {'type': {'type': 'string', 'format': 'uri-reference'}, 'title': {'type': 'string'}, 'status': {'type': 'integer'}, 'detail': {'type': 'string'}, 'instance': {'type': 'string', 'format': 'uri-reference'}, 'code': {'type': 'string'}, 'trace_id': {'type': 'string'}, 'errors': {'type': 'array', 'items': {'type': 'object', 'required': ['field', 'message'], 'properties': {'field': {'type': 'string'}, 'message': {'type': 'string'}}}}}},
        'Evidence': {'type': 'object', 'additionalProperties': False, 'required': ['resource_id', 'version', 'locator', 'source_kind'], 'properties': {'resource_id': {'type': 'string'}, 'version': {'type': 'integer'}, 'locator': {'type': 'string', 'description': '页码段落、原始记录或查询定位'}, 'source_kind': {'type': 'string', 'enum': ['database', 'manual', 'maintenance_case', 'public_page', 'derived']}, 'data_cutoff': {'type': 'string', 'format': 'date-time'}, 'note': {'type': 'string'}}},
        'MaintenanceAnalysis': {'type': 'object', 'required': ['id', 'version', 'workspace_id', 'data_cutoff', 'findings', 'evidence'], 'properties': {'id': {'type': 'string'}, 'version': {'type': 'integer'}, 'workspace_id': {'type': 'string'}, 'data_cutoff': {'type': 'string', 'format': 'date-time'}, 'findings': {'type': 'array', 'items': {'type': 'object', 'required': ['device_id', 'observation', 'cause_status'], 'properties': {'device_id': {'type': 'string'}, 'observation': {'type': 'string'}, 'cause_status': {'type': 'string', 'enum': ['suspected', 'confirmed', 'insufficient_data']}, 'trend': {'type': 'string'}, 'recommendation': {'type': 'string'}, 'evidence_ids': {'type': 'array', 'items': {'type': 'string'}}}}}, 'evidence': {'type': 'array', 'items': {'$ref': '#/components/schemas/Evidence'}}}},
        'InspectionResult': {'type': 'object', 'required': ['metric_code', 'measured', 'unit', 'verdict', 'standard_version', 'sample_id'], 'properties': {'metric_code': {'type': 'string'}, 'measured': {'type': ['number', 'string', 'null']}, 'unit': {'type': 'string'}, 'verdict': {'type': 'string', 'enum': ['pass', 'fail', 'inconclusive', 'not_tested']}, 'standard_version': {'type': 'string'}, 'sample_id': {'type': 'string'}, 'detector_version': {'type': ['string', 'null']}, 'defect_region_ref': {'type': ['string', 'null']}}},
        'AppLaunch': {'type': 'object', 'required': ['launch_url', 'expires_at'], 'properties': {'launch_url': {'type': 'string', 'format': 'uri-reference', 'description': '同站白名单启动目标，短时单次票据不得记录到日志'}, 'expires_at': {'type': 'string', 'format': 'date-time'}}},
        'UploadSession': {'type': 'object', 'required': ['id', 'upload_url', 'expires_at', 'max_size_bytes'], 'properties': {'id': {'type': 'string'}, 'upload_url': {'type': 'string', 'format': 'uri'}, 'expires_at': {'type': 'string', 'format': 'date-time'}, 'max_size_bytes': {'type': 'integer'}, 'method': {'type': 'string', 'enum': ['PUT', 'POST']}}},
    }
    # 按资源创建/编辑字段生成对应资源读模型；结果服务仍需完成细字段评审。
    resource_fields = {}
    for item in operations:
        if item['method'] == 'POST' and not re.search(r'\{[^}]+\}', item['path']) and item['result_kind'] == 'resource':
            resource_fields[item['path']] = item['request_fields']
    problem_response = {'description': '业务错误，结构遵循RFC9457；不得泄露凭据和未授权资源', 'content': {'application/problem+json': {'schema': {'$ref': '#/components/schemas/Problem'}}}}
    paths, api_rows = {}, []
    for item in operations:
        method, path = item['method'], item['path']
        parameters = []
        for name in re.findall(r'\{([^}]+)\}', path):
            parameters.append({'name': name, 'in': 'path', 'required': True, 'description': '工作区内已授权资源标识；engine仅允许已登记来源', 'schema': {'type': 'string', 'minLength': 1, 'maxLength': 128}})
        if item['result_kind'] == 'list' and method == 'GET':
            parameters.extend([{'name': 'cursor', 'in': 'query', 'schema': {'type': 'string'}, 'description': '不可猜测游标'}, {'name': 'limit', 'in': 'query', 'schema': {'type': 'integer', 'minimum': 1, 'maximum': 100, 'default': 20}, 'description': '分页上限，不能绕过数据范围'}])
        for name, value in item.get('query_fields', {}).items():
            parameters.append({'name': name, 'in': 'query', 'required': value.get('required', False), 'description': value['description'], 'schema': {key: val for key, val in value.items() if key not in {'required', 'description'}}})
        if item['idempotency']:
            parameters.append({'name': 'Idempotency-Key', 'in': 'header', 'required': True, 'description': '工作区、动作、用户及请求摘要共同限定幂等；同键不同输入返回409', 'schema': {'type': 'string', 'minLength': 8, 'maxLength': 128}})
        if item['optimistic_lock']:
            parameters.append({'name': 'If-Match', 'in': 'header', 'required': True, 'description': '读取时获得的资源版本ETag；冲突返回409', 'schema': {'type': 'string'}})
        if item['result_kind'] == 'events':
            parameters.append({'name': 'Last-Event-ID', 'in': 'header', 'description': '从已确认序号后续接，不重新提交任务', 'schema': {'type': 'string'}})
        status = '202' if item['result_kind'] == 'task' else '204' if item['result_kind'] == 'empty' else '200'
        result_schema = {'$ref': '#/components/schemas/Resource'}
        if item['result_kind'] == 'task':
            result_schema = {'$ref': '#/components/schemas/TaskRef'}
        elif path.endswith('/maintenance-analyses/{id}'):
            result_schema = {'$ref': '#/components/schemas/MaintenanceAnalysis'}
        elif path.endswith('/inspections/{id}/results'):
            result_schema = {'type': 'array', 'items': {'$ref': '#/components/schemas/InspectionResult'}}
        elif path.endswith('/app-launches'):
            result_schema = {'$ref': '#/components/schemas/AppLaunch'}
        elif path.endswith('/file-upload-sessions'):
            result_schema = {'$ref': '#/components/schemas/UploadSession'}
        else:
            candidates = sorted((key for key in resource_fields if path == key or path == key + '/{id}'), key=len, reverse=True)
            if candidates:
                schema_name = item['operation_id'] + '_Resource'
                shape = fields_schema(resource_fields[candidates[0]])
                shape['properties'].update(schemas['Resource']['properties'])
                shape.pop('required', None)
                shape['required'] = ['id', 'version', 'workspace_id']
                schemas[schema_name] = shape
                result_schema = {'$ref': '#/components/schemas/' + schema_name}
        if item['result_kind'] == 'list' and not path.endswith('/inspections/{id}/results'):
            result_schema = {'type': 'object', 'required': ['items'], 'properties': {'items': {'type': 'array', 'items': result_schema}, 'next_cursor': {'type': ['string', 'null']}}}
        success = {'description': '任务已持久受理，非执行成功' if status == '202' else '操作结果'}
        if status != '204':
            success['content'] = {'text/event-stream' if item['result_kind'] == 'events' else 'application/json': {'schema': {'type': 'string'} if item['result_kind'] == 'events' else result_schema}}
        responses = {status: success, **{code: problem_response for code in ['400', '401', '403', '404', '409', '422', '429', '503']}}
        entry = {'operationId': item['operation_id'], 'tags': [item['module_id']], 'summary': item['summary'], 'description': item['status'] + '。原生上游接口保留原协议；此处是平台业务契约。', 'parameters': parameters, 'responses': responses, 'security': [{'serviceBearer': [], 'callbackSignature': []}] if item['internal'] else [{'sessionCookie': []}, {'serviceBearer': []}], 'x-permission': item['permission'], 'x-owner': item['owner'], 'x-status': 'planned', 'x-module-id': item['module_id'], 'x-contract-stage': 'project-draft'}
        if item['request_fields']:
            schema_name = item['operation_id'] + '_Input'
            schemas[schema_name] = fields_schema(item['request_fields'], method == 'PATCH')
            entry['requestBody'] = {'required': True, 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/' + schema_name}}}}
        paths.setdefault(path, {})[method.lower()] = entry
        api_rows.append({'接口ID': item['id'], '模块': item['module_id'], '方法': method, '路径': path, 'operationId': item['operation_id'], '功能': item['summary'], '实现归属': item['owner'], '实现类型': item['implementation'], '权限': item['permission'], '内部接口': item['internal'], '幂等键': item['idempotency'], '版本条件': item['optimistic_lock'], '状态': item['status']})
    spec = {'openapi': '3.1.1', 'info': {'title': '中国制造业Agent平台业务契约', 'version': '0.1-draft', 'description': f'规划{len(operations)}个方法路径操作；不包含完整保留的Dify、Open WebUI原生API，不代表已实现。核心任务与质检结构已明确；其他资源字段需在具体需求Ready评审时冻结。'}, 'servers': [{'url': '/', 'description': '由实际部署入口提供，不内置任何业务数据库地址'}], 'tags': [{'name': m['id'], 'description': m['name']} for m in modules], 'paths': paths, 'components': {'securitySchemes': {'sessionCookie': {'type': 'apiKey', 'in': 'cookie', 'name': 'platform_session', 'description': '安全会话；Cookie写操作仍需CSRF校验'}, 'serviceBearer': {'type': 'http', 'scheme': 'bearer', 'description': '仅登记的受限服务身份，不是共享管理员'}, 'callbackSignature': {'type': 'apiKey', 'in': 'header', 'name': 'X-Callback-Signature', 'description': '内部事件签名，结合来源、时间窗、事件ID及服务凭据校验'}}, 'schemas': schemas}}
    write_json(ROOT / 'contracts/openapi.json', spec)
    write_csv(ROOT / 'contracts/接口目录.csv', api_rows)
    write_csv(ROOT / 'assets/功能清单.csv', [{'功能ID': f['id'], '模块': f['module_id'], '功能': f['name'], '角色': f['role'], '触发': f['trigger'], '输入': f['inputs'], '输出': f['outputs'], '达到效果与验收': f['acceptance'], '实现方式': f['implementation'], '页面ID': ';'.join(f['page_ids']), '阶段': f['phase'], '来源': f['source']} for f in features])
    write_csv(ROOT / 'assets/页面清单.csv', [{'页面ID': p['id'], '模块': p['module_id'], '页面': p['name'], '逻辑路由': p['route'], '类型': p['kind'], '角色': ';'.join(p['roles']), '实现归属': p['owner'], '阶段': p['phase'], '功能ID': ';'.join(p['feature_ids'])} for p in pages])
    mappings = []
    contract_map = {row['feature_id']: row for row in api.get('feature_contracts', [])}
    operation_map = {row['operation_id']: row for row in operations}
    for feature in features:
        assert feature['id'] in contract_map, f'功能缺少接口或原生能力承载：{feature["id"]}'
        link = contract_map[feature['id']]
        assert link['platform_operation_ids'] or link['native_or_ui_scope'], '功能没有实际承载'
        assert all(key in operation_map for key in link['platform_operation_ids']), '功能引用不存在的operationId'
        resolved = [operation_map[key] for key in link['platform_operation_ids']]
        mappings.append({'功能ID': feature['id'], '功能': feature['name'], '页面ID': ';'.join(feature['page_ids']), '实现': feature['implementation'], '平台接口ID': ';'.join(op['id'] for op in resolved), 'operationId': ';'.join(link['platform_operation_ids']), '原生或前端承载': ';'.join(link['native_or_ui_scope']), '映射级别': link['stage']})
    write_csv(ROOT / 'assets/功能接口映射.csv', mappings)
    summary = {'status': '规划基线，未实施', 'module_count': len(modules), 'feature_count': len(features), 'page_count': len(pages), 'api_count': len(operations), 'api_paths': len(paths), 'feature_phases': dict(Counter(f['phase'] for f in features)), 'page_phases': dict(Counter(p['phase'] for p in pages)), 'api_implementation_types': dict(Counter(op['implementation'] for op in operations)), 'api_owners': dict(Counter(op['owner'] for op in operations)), 'modules': [{'id': m['id'], 'name': m['name'], 'features': len(m['features']), 'pages': len(m['pages']), 'apis': sum(op['module_id'] == m['id'] for op in operations)} for m in modules], 'counting_rule': '功能为有验收结果的能力；页面为逻辑入口；接口为平台method+path。完整保留上游功能另按固定版本目录记录，不与本数直接相加。'}
    write_json(ROOT / 'assets/统计.json', summary)
    sync_markdown_counts(summary)
    checks = {'duplicate_ids': 0, 'missing_feature_page_refs': 0, 'missing_api_modules': 0, 'module_count': len(modules), 'feature_count': len(features), 'page_count': len(pages), 'api_count': len(operations), 'checked_at': '2026-10-20', 'boundary': '文档结构检查；不是运行、数据库、接口或性能验收'}
    write_json(ROOT / 'assets/文档校验.json', checks)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
