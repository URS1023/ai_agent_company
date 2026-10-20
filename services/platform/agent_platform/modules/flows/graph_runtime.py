"""纯Graphon图的定义/输入边界及平台运行投影；不复制图执行算法。"""

from __future__ import annotations

import importlib.util
import json
import sys
import threading

from sqlalchemy import select
from jsonschema import Draft7Validator, SchemaError
from referencing import Registry
from referencing.exceptions import NoSuchResource, Unresolvable

from agent_platform.core.errors import DomainError
from agent_platform.core.models import Task, TaskEvent
from agent_platform.core.records import sanitized
from agent_platform.core.settings import PROJECT_ROOT
from agent_platform.core.tasks import TaskService

_module_lock = threading.Lock()
MODULE_NAME = 'agent_platform_owned_graphon_worker'
GRAPH_EVENT = 'workflow_graph_event'
INPUT_TYPES = {'text-input', 'paragraph', 'number', 'select', 'checkbox', 'json_object'}


def _deny_schema_resource(uri):
    """仅使用当前输入Schema；即便引用解析出错也不得从网络或磁盘补取。"""
    raise NoSuchResource(ref=uri)


def worker_module():
    """仅加载本项目标准库适配器；Python 3.14进程不导入Graphon或Dify应用。"""
    path = PROJECT_ROOT / 'services/workflow_runtime/worker.py'
    with _module_lock:
        module = sys.modules.get(MODULE_NAME)
        if module is None:
            spec = importlib.util.spec_from_file_location(MODULE_NAME, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[MODULE_NAME] = module
            try:
                spec.loader.exec_module(module)
            except BaseException:
                sys.modules.pop(MODULE_NAME, None)
                raise
        if module.WORKER != path.resolve():
            raise DomainError(503, 'workflow_runtime_source_invalid', '执行器代码来源不匹配')
        return module


def definition_format(definition):
    value = definition.get('format') if isinstance(definition, dict) else None
    if value is None or value == 'legacy_steps':
        return 'legacy_steps'
    if value == 'graph':
        return 'graph'
    raise DomainError(422, 'workflow_format_invalid', '工作流格式不支持', {'definition.format': '请选择已支持的工作流格式'})


def normalize_definition(definition):
    """旧steps按原规则保存；图保持完整节点/连接，不互相强转或丢弃未知运行类型。"""
    if definition_format(definition) == 'legacy_steps':
        from .service import validate
        result = {'steps': validate(definition)}
        if definition.get('format') == 'legacy_steps':
            result['format'] = 'legacy_steps'
        return result
    worker = worker_module()
    if (definition.get('engine') != 'graphon' or definition.get('engine_version') != worker.ENGINE_VERSION
            or set(definition) - {'format', 'engine', 'engine_version', 'root_node_id', 'graph', 'variables', 'limits'}):
        raise DomainError(422, 'workflow_engine_invalid', '图执行内核或配置字段不支持')
    limits = definition.get('limits', {})
    if not isinstance(limits, dict) or set(limits) - {'max_steps'}:
        raise DomainError(422, 'workflow_limits_invalid', '图定义只接受节点执行次数预算')
    try:
        result = json.loads(json.dumps(definition, ensure_ascii=False, allow_nan=False))
        probe = {'protocol_version': 1, 'run_id': 'definition-validation',
            'root_node_id': result.get('root_node_id'), 'graph': result.get('graph'), 'inputs': {},
            'variables': result.get('variables', []), 'limits': {'max_steps': limits.get('max_steps', 1000), 'max_seconds': 90}}
        worker.validate_request(probe)
        if len(json.dumps(probe, ensure_ascii=False).encode()) > worker.MAX_INPUT_BYTES:
            raise worker.WorkerError('input_too_large', '图定义超过本次运行预算')
    except worker.WorkerError as exc:
        raise DomainError(422, 'workflow_' + exc.code, exc.message, exc.fields) from None
    except (TypeError, ValueError, RecursionError):
        raise DomainError(422, 'workflow_definition_invalid', '图定义必须是有限的JSON数据') from None
    input_fields(result)
    node_ids = {node['id'] for node in result['graph']['nodes']}
    if any(item['selector'][0] in node_ids for item in result.get('variables', [])):
        raise DomainError(422, 'workflow_variable_invalid', '初始运行变量不能覆盖节点输入或输出',
            {'variables': '请使用独立的运行变量作用域'})
    return result


def input_fields(definition):
    """表单来自顶层Start节点；顶层variables是运行状态，不能混作用户填写字段。"""
    nodes = definition['graph']['nodes']
    root = next((node for node in nodes if node.get('id') == definition['root_node_id']), None)
    if (any(not isinstance(node.get('id'), str) or not node['id'] for node in nodes)
            or root is None or root['data'].get('type') != 'start' or root['data'].get('container_id') or root.get('parentId')
            or len({node.get('id') for node in nodes}) != len(nodes)):
        raise DomainError(422, 'workflow_start_invalid', '请选择唯一的顶层开始节点')
    fields = root['data'].get('variables', [])
    if not isinstance(fields, list) or len(fields) > 100:
        raise DomainError(422, 'workflow_inputs_invalid', '开始节点输入字段格式不正确')
    names = set()
    for field in fields:
        if (not isinstance(field, dict) or not isinstance(field.get('variable'), str) or not field['variable']
                or field['variable'] in names or not isinstance(field.get('label'), str)
                or not isinstance(field.get('type'), str) or field['type'] not in INPUT_TYPES
                or type(field.get('required', False)) is not bool):
            raise DomainError(422, 'workflow_input_field_invalid', '输入字段名称、类型或标签不正确')
        names.add(field['variable'])
        if (field.get('max_length') is not None and (type(field['max_length']) is not int
                or not 1 <= field['max_length'] <= 40000)):
            raise DomainError(422, 'workflow_input_field_invalid', '输入字段长度不正确')
        if field['type'] == 'select' and (not isinstance(field.get('options'), list)
                or not field['options'] or any(not isinstance(item, str) for item in field['options'])):
            raise DomainError(422, 'workflow_input_field_invalid', '选择字段需要有效选项')
        schema = field.get('json_schema')
        if schema is not None and schema != '':
            try:
                schema = json.loads(schema) if isinstance(schema, str) else schema
                if not isinstance(schema, dict):
                    raise ValueError('schema must be an object')
                Draft7Validator.check_schema(schema)
            except (SchemaError, ValueError, TypeError, RecursionError):
                raise DomainError(422, 'workflow_input_schema_invalid', '输入结构定义不正确') from None
    return fields


def form_inputs(definition, values):
    """默认值只填补缺失项，保留0/false/空数组；字段错误定位到用户实际控件。"""
    if not isinstance(values, dict):
        raise DomainError(422, 'workflow_inputs_invalid', '请填写运行输入')
    fields = input_fields(definition)
    names = {field['variable'] for field in fields}
    if set(values) - names:
        raise DomainError(422, 'workflow_inputs_invalid', '输入包含未定义字段',
            {'inputs.' + key: '该字段不在开始节点中' for key in values if key not in names})
    result, errors = dict(values), {}
    for field in fields:
        key, kind = field['variable'], field['type']
        if key not in result and field.get('default') is not None:
            result[key] = field['default']
        if key not in result or result[key] is None or result[key] == '':
            if field.get('required'):
                errors['inputs.' + key] = '请填写' + field['label']
            continue
        value = result[key]
        valid = ((kind in {'text-input', 'paragraph', 'select'} and isinstance(value, str))
            or (kind == 'number' and type(value) in {int, float})
            or (kind == 'checkbox' and type(value) is bool)
            or (kind == 'json_object' and isinstance(value, dict)))
        if valid and isinstance(value, str):
            valid = len(value) <= (field.get('max_length') or 40000)
        if valid and kind == 'select':
            valid = value in field['options']
        if valid and kind == 'json_object' and field.get('json_schema'):
            schema = field['json_schema']
            schema = json.loads(schema) if isinstance(schema, str) else schema
            try:
                valid = Draft7Validator(schema, registry=Registry(retrieve=_deny_schema_resource)).is_valid(value)
            except Unresolvable:
                valid = False
        if not valid:
            errors['inputs.' + key] = '请检查' + field['label']
    if errors:
        raise DomainError(422, 'workflow_inputs_invalid', '请检查运行输入', errors)
    return result


def encode_inputs(definition, user_input, structured_inputs):
    """图输入规范编码到原Text列；旧字符串保持原字节，兼容已有幂等指纹。"""
    if definition_format(definition) == 'legacy_steps':
        if structured_inputs is not None:
            raise DomainError(422, 'workflow_inputs_invalid', '步骤流程使用文本输入', {'inputs': '请使用input字段'})
        return user_input
    if structured_inputs is not None and user_input:
        raise DomainError(422, 'workflow_inputs_invalid', '图运行输入不能同时使用两种格式', {'inputs': '请仅填写结构化输入'})
    values = structured_inputs if structured_inputs is not None else ({'input': user_input} if user_input else {})
    worker = worker_module()
    try:
        probe = {'protocol_version': 1, 'run_id': 'input-validation',
            'root_node_id': definition['root_node_id'], 'graph': definition['graph'], 'inputs': values,
            'variables': definition.get('variables', []), 'limits': {'max_seconds': 90}}
        worker.validate_request(probe)
        values = form_inputs(definition, values)
        probe['inputs'] = values
        if len(json.dumps(probe, ensure_ascii=False).encode()) + 100 > worker.MAX_INPUT_BYTES:
            raise worker.WorkerError('input_too_large', '图定义与输入超过本次运行预算')
        raw = json.dumps(values, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        if len(raw.encode('utf-8')) > worker.MAX_INPUT_BYTES // 2:
            raise worker.WorkerError('input_too_large', '图输入超过本次文本预算')
    except worker.WorkerError as exc:
        raise DomainError(422, 'workflow_' + exc.code, exc.message, exc.fields) from None
    except (ValueError, TypeError, RecursionError):
        raise DomainError(422, 'workflow_inputs_invalid', '图输入必须是有限的JSON对象') from None
    return raw


def graph_summary(record):
    return next((item for item in record.steps if item.get('type') == 'graph_result'), None)


def run_format(record):
    return definition_format(record.definition_snapshot or {})


def run_graph_fields(record):
    """兼容旧output字符串，并独立返回原生JSON outputs，不把数组/数值转成假文本结果。"""
    kind = run_format(record)
    if kind != 'graph':
        return {'format': kind}
    summary = graph_summary(record) or {}
    return {'format': kind, 'inputs': json.loads(record.input), 'outputs': summary.get('outputs', {}),
        'graph_progress': {key: summary.get(key) for key in ('completed_nodes', 'event_count', 'last_event_sequence')}}


def read_events(session, actor, record, *, after=0, limit=100):
    """复用现有只追加TaskEvent，不增加表或反序列化Dify数据库；调用前已核Run来源。"""
    TaskService(session, actor).get(record.task_id)
    bound_task(session, actor, record)
    rows = list(session.scalars(select(TaskEvent).where(TaskEvent.task_id == record.task_id,
        TaskEvent.type == GRAPH_EVENT, TaskEvent.data['run_id'].as_string() == record.id,
        TaskEvent.sequence > max(after, 0)).order_by(TaskEvent.sequence).limit(limit + 1)))
    selected = rows[:limit]
    return {'items': [{'cursor': row.sequence, **row.data, 'created_at': row.created_at} for row in selected],
        'next_cursor': selected[-1].sequence if selected else after, 'has_more': len(rows) > limit}


def bound_task(session, actor, record):
    """事件读写共用Run/Task双向归属；不以可变正文权限代替关联真实性。"""
    task = session.get(Task, record.task_id)
    if (task is None or task.workspace_id != record.workspace_id or record.workspace_id != actor.workspace_id
            or task.kind != 'platform_workflow' or task.user_id != record.created_by
            or task.inputs.get('run_id') != record.id or task.inputs.get('workflow_id') != record.workflow_id):
        raise DomainError(409, 'workflow_task_binding_invalid', '运行与任务的关联不一致')
    return task


class GraphExecution:
    """把原生事件逐条提交到平台任务账本；该类不解释节点和连接。"""

    def __init__(self, service, record, budget):
        self.service, self.record, self.budget = service, record, budget
        self.session, self.actor = service.session, service.actor
        self.sequence, self.completed = 0, 0

    def event(self, frame):
        self.budget.remaining()
        if frame['sequence'] <= self.sequence:
            raise DomainError(502, 'workflow_event_sequence_invalid', '图事件顺序不一致，请核对原运行')
        self.sequence = frame['sequence']
        if frame['event_type'] == 'NodeRunSucceededEvent':
            self.completed += 1
        task = bound_task(self.session, self.actor, self.record)
        # 内部事件写入仅核不可伪造的运行绑定；公共读取仍按Run/Task当前来源权限复核。
        self.session.add(TaskEvent(task_id=task.id, type=GRAPH_EVENT, data=sanitized(
            {'run_id': self.record.id, 'event_sequence': frame['sequence'],
                'event_type': frame['event_type'], 'data': frame['data']}, preserve_business_value=True)))
        summary = dict(graph_summary(self.record))
        summary.update(completed_nodes=self.completed, event_count=summary['event_count'] + 1,
            last_event_sequence=self.sequence)
        self.record.steps, self.record.current_step = [summary], self.completed
        # 每条原生事件与进度同事务提交；Session始终由同步路由线程持有。
        self.session.commit()

    def run(self):
        worker = worker_module()
        snapshot = self.record.definition_snapshot
        self.record.steps = [{'index': 1, 'name': '图运行', 'type': 'graph_result', 'status': 'running',
            'outputs': {}, 'completed_nodes': 0, 'event_count': 0, 'last_event_sequence': 0}]
        self.session.commit()
        remaining = self.budget.remaining()
        request = {'protocol_version': 1, 'run_id': self.record.id, 'root_node_id': snapshot['root_node_id'],
            'graph': snapshot['graph'], 'variables': snapshot.get('variables', []), 'inputs': json.loads(self.record.input),
            'limits': {'max_steps': snapshot.get('limits', {}).get('max_steps', 1000), 'max_seconds': remaining}}
        try:
            result = worker.run_wsl(request, timeout_seconds=remaining, on_event=self.event)
        except worker.WorkerError as exc:
            known = {'node_not_supported', 'graph_validation_failed', 'runtime_version_invalid',
                'wsl_keeper_not_running', 'wsl_runtime_identity_changed', 'runtime_proof_timeout', 'wsl_launcher_invalid'}
            code = 'workflow_graph_rejected' if exc.code in known else 'workflow_graph_unknown'
            summary = dict(graph_summary(self.record))
            summary['runtime_error'] = {'code': exc.code, **exc.fields}
            self.record.steps = [summary]
            raise DomainError(503 if code.endswith('unknown') else 422, code,
                '图执行结果需要核对' if code.endswith('unknown') else exc.message,
                {'run_id': self.record.id, 'runtime_code': exc.code, **exc.fields}) from None
        self.budget.remaining()
        terminal = result['result']
        summary = dict(graph_summary(self.record))
        summary.update(runtime=result.get('runtime'), process=result.get('process'))
        if terminal['status'] == 'succeeded':
            summary.update(status='ok', outputs=terminal['outputs'])
            self.record.output = json.dumps(terminal['outputs'], ensure_ascii=False, allow_nan=False)
        else:
            summary.update(status='failed', outputs=terminal.get('outputs', {}), runtime_error=terminal.get('error'))
            self.record.steps = [summary]
            raise DomainError(422, 'workflow_graph_failed', '图执行失败，请查看运行事件',
                {'runtime_error': terminal.get('error'), 'run_id': self.record.id})
        self.record.steps = [summary]
