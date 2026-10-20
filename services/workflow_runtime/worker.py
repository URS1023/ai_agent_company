"""以独立Python 3.13执行Graphon纯图；平台进程只负责子进程和JSON协议。

本模块顶层仅使用标准库，所以Python 3.14平台可以调用run_isolated。
Graphon只在子进程加载；不启动监听，不向子进程传递密钥或数据库配置。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import importlib.metadata
import importlib
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import queue
import secrets
import subprocess
import sys
import threading
import time
from typing import Any

PROTOCOL_VERSION = 1
ENGINE_VERSION = '0.8.0'
MAX_INPUT_BYTES = 1024 * 1024
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_EVENTS = 4000
PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKER = Path(__file__).resolve()
SUPPORTED = {
    'start': ('1',), 'end': ('1',), 'answer': ('1',), 'if-else': ('1',),
    'variable-aggregator': ('1',), 'variable-assigner': ('1',), 'assigner': ('1', '2'),
    'list-operator': ('1',), 'iteration': ('1',), 'iteration-start': ('1',),
    'loop': ('1',), 'loop-start': ('1',), 'loop-end': ('1',),
}


class WorkerError(ValueError):
    """可被父进程展示的稳定协议错误；不包含原始输入、凭据或异常堆栈。"""

    def __init__(self, code: str, message: str, fields=None):
        super().__init__(message)
        self.code, self.message, self.fields = code, message, fields or {}

    def payload(self):
        return {'code': self.code, 'message': self.message, 'fields': self.fields}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise WorkerError('duplicate_json_key', 'JSON包含重复字段')
        result[key] = value
    return result


def _json(raw: bytes):
    try:
        return json.loads(raw, object_pairs_hook=_unique_object,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError('non-finite JSON')))
    except (ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, WorkerError):
            raise
        raise WorkerError('invalid_json', '请输入完整且有限的JSON数据') from None


def _tree(value, depth=0):
    """限制单次IPC结构和展开规模，不把文件对象或任意Python对象交给图引擎。"""
    if depth > 32:
        raise WorkerError('input_too_deep', '输入结构层级过深')
    if isinstance(value, str) and len(value) > 40_000:
        raise WorkerError('input_value_too_large', '单个文本超过本次运行预算')
    if isinstance(value, float) and not math.isfinite(value):
        raise WorkerError('invalid_json', '输入数值必须有限')
    if isinstance(value, dict):
        if len(value) > 1024:
            raise WorkerError('input_too_large', '输入字段超过本次运行预算')
        for key, item in value.items():
            if not isinstance(key, str):
                raise WorkerError('invalid_json', 'JSON字段名称必须是文本')
            _tree(item, depth + 1)
    elif isinstance(value, list):
        if len(value) > 2000:
            raise WorkerError('input_too_large', '数组超过本次运行预算')
        for item in value:
            _tree(item, depth + 1)
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise WorkerError('invalid_json', '输入只能包含JSON值')


def validate_request(value):
    if not isinstance(value, dict) or value.get('protocol_version') != PROTOCOL_VERSION:
        raise WorkerError('protocol_version_invalid', '执行协议版本不支持')
    if set(value) - {'protocol_version', 'run_id', 'graph', 'root_node_id', 'inputs', 'variables', 'limits'}:
        raise WorkerError('request_fields_invalid', '执行请求包含未支持的字段')
    _tree(value)
    run_id = value.get('run_id')
    if not isinstance(run_id, str) or not run_id or len(run_id) > 100:
        raise WorkerError('run_id_invalid', '请提供本次运行编号')
    graph, inputs, variables = value.get('graph'), value.get('inputs', {}), value.get('variables', [])
    if not isinstance(graph, dict) or not isinstance(inputs, dict) or not isinstance(variables, list):
        raise WorkerError('graph_input_invalid', '图、输入或初始变量格式不正确')
    nodes, edges = graph.get('nodes'), graph.get('edges')
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 256 or not isinstance(edges, list) or len(edges) > 1024:
        raise WorkerError('graph_size_invalid', '图节点或连接超过本次执行预算')
    root = value.get('root_node_id')
    if not isinstance(root, str) or not root:
        raise WorkerError('root_node_invalid', '请指定图的开始节点')
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get('data'), dict):
            raise WorkerError('node_invalid', '节点数据格式不正确')
        data = node['data']
        node_type, version = data.get('type'), str(data.get('version', '1'))
        if node_type not in SUPPORTED or version not in SUPPORTED[node_type]:
            raise WorkerError('node_not_supported', '该节点或版本尚未接入当前纯图执行器',
                {'node_id': node.get('id'), 'node_type': node_type, 'node_version': version})
        if node_type == 'start' and isinstance(data.get('variables'), list):
            for variable in data['variables']:
                if isinstance(variable, dict):
                    schema = variable.get('json_schema')
                    # Dify编辑器会保存Schema文本；必须先解析再检查，不能让字符串绕过引用边界。
                    if isinstance(schema, str) and schema.strip():
                        schema = _json(schema.encode('utf-8'))
                    _tree(schema)
                    _local_schema(schema)
        retry = data.get('retry_config') or {}
        if not isinstance(retry, dict) or not isinstance(retry.get('max_retries', 0), int) or not 0 <= retry.get('max_retries', 0) <= 3:
            raise WorkerError('retry_budget_invalid', '步骤重试次数超过本次执行预算')
    for item in variables:
        if (not isinstance(item, dict) or set(item) != {'selector', 'value'} or not isinstance(item['selector'], list)
                or not 2 <= len(item['selector']) <= 10 or not all(isinstance(part, str) and part for part in item['selector'])):
            raise WorkerError('variable_invalid', '初始变量选择器格式不正确')
    limits = value.get('limits', {})
    if not isinstance(limits, dict) or set(limits) - {'max_steps', 'max_seconds'}:
        raise WorkerError('limits_invalid', '执行预算格式不正确')
    steps, seconds = limits.get('max_steps', 1000), limits.get('max_seconds', 10)
    if (isinstance(steps, bool) or not isinstance(steps, int) or not 1 <= steps <= 1000
            or isinstance(seconds, bool) or not isinstance(seconds, (float, int))
            or not math.isfinite(seconds) or not 0 < seconds <= 90):
        raise WorkerError('limits_invalid', '执行预算应为1至1000步及不超过90秒')
    return value


def _local_schema(schema):
    """JSON输入校验也不能解析远程或磁盘引用；只保留文档内的#选择器。"""
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key in {'$ref', '$dynamicRef', '$recursiveRef'} and (not isinstance(value, str) or not value.startswith('#')):
                raise WorkerError('external_schema_reference', '输入结构只能引用当前JSON Schema内的定义')
            _local_schema(value)
    elif isinstance(schema, list):
        for item in schema:
            _local_schema(item)


def install_read_only_guard():
    """对子进程额外拒绝网络、数据库、派生进程和文件写入；纯节点白名单仍是首要边界。"""
    forbidden = {'os.system', 'os.fork', 'os.forkpty', 'os.posix_spawn', 'os.exec',
        'os.remove', 'os.rename', 'os.rmdir', 'os.mkdir', 'os.link', 'os.symlink', 'os.chmod',
        'os.chown', 'os.truncate', 'os.utime'}
    def audit(event, args):
        if event.startswith(('socket.', 'sqlite3.', 'subprocess.', 'os.spawn')) or event in forbidden:
            raise PermissionError('pure_graph_io_denied')
        if event == 'open':
            mode = args[1] if len(args) > 1 else None
            flags = args[2] if len(args) > 2 else 0
            if (isinstance(mode, str) and any(char in mode for char in 'wax+')) or (
                    isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)):
                raise PermissionError('pure_graph_write_denied')
    sys.addaudithook(audit)


def _node_classes():
    """只加载已审核的纯节点；不导入Dify应用、插件或Slim自动下载器。"""
    from graphon.nodes.start.start_node import StartNode
    from graphon.nodes.end.end_node import EndNode
    from graphon.nodes.answer.answer_node import AnswerNode
    from graphon.nodes.if_else.if_else_node import IfElseNode
    from graphon.nodes.variable_aggregator.variable_aggregator_node import VariableAggregatorNode
    from graphon.nodes.variable_assigner.v1.node import VariableAssignerNode as AssignerV1
    from graphon.nodes.variable_assigner.v2.node import VariableAssignerNode as AssignerV2
    from graphon.nodes.list_operator.node import ListOperatorNode
    from graphon.nodes.iteration.iteration_node import IterationNode
    from graphon.nodes.iteration.iteration_start_node import IterationStartNode
    from graphon.nodes.loop.loop_node import LoopNode
    from graphon.nodes.loop.loop_start_node import LoopStartNode
    from graphon.nodes.loop.loop_end_node import LoopEndNode
    return {kind: {'1': cls} for kind, cls in {
        'start': StartNode, 'end': EndNode, 'answer': AnswerNode, 'if-else': IfElseNode,
        'variable-aggregator': VariableAggregatorNode, 'variable-assigner': VariableAggregatorNode,
        'list-operator': ListOperatorNode, 'iteration': IterationNode, 'iteration-start': IterationStartNode,
        'loop': LoopNode, 'loop-start': LoopStartNode, 'loop-end': LoopEndNode}.items()} | {
            'assigner': {'1': AssignerV1, '2': AssignerV2}}


@dataclass(frozen=True)
class PureNodeFactory:
    """工厂复制每个容器的运行状态和图作用域，不共享父帧的可变变量池。"""

    init_params: Any
    runtime_state: Any
    classes: dict

    def with_runtime_state(self, runtime_state):
        return replace(self, runtime_state=runtime_state)

    def with_graph_config(self, graph_config):
        return replace(self, init_params=self.init_params.model_copy(update={'graph_config': graph_config}))

    def resolve(self, node_config):
        data = node_config['data']
        kind, version = data.type, str(data.version)
        cls = self.classes.get(kind, {}).get(version)
        if cls is None:
            raise WorkerError('node_not_supported', '节点实现或版本尚未接入', {'node_id': node_config['id']})
        return cls, cls.validate_node_data(data)

    def validate_node(self, node_config):
        cls, _ = self.resolve(node_config)
        return cls.execution_type

    def create_node(self, node_config):
        cls, data = self.resolve(node_config)
        return cls(node_id=node_config['id'], data=data, init_params=self.init_params, runtime_state=self.runtime_state)


class Emitter:
    def __init__(self, stream):
        self.stream, self.sequence, self.bytes, self.run_id = stream, 0, 0, None

    def emit(self, kind, payload, *, terminal=False):
        value = {'protocol_version': PROTOCOL_VERSION, 'sequence': self.sequence + 1,
            'run_id': self.run_id, 'kind': kind, **payload}
        encoded = (json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n').encode('utf-8')
        if (not terminal and self.sequence >= MAX_EVENTS) or self.bytes + len(encoded) > MAX_OUTPUT_BYTES - (0 if terminal else 4096):
            raise WorkerError('output_budget_exceeded', '事件或输出超过本次运行预算')
        self.stream.write(encoded)
        self.stream.flush()
        self.sequence += 1
        self.bytes += len(encoded)


def execute(request, emitter):
    """调用原Graph/Engine并转发真实事件；不自行解释步骤或伪造节点成功。"""
    if sys.version_info[:2] != (3, 13) or importlib.metadata.version('graphon') != ENGINE_VERSION:
        raise WorkerError('runtime_version_invalid', '执行器需要Python 3.13及Graphon 0.8.0')
    from graphon.engine import Engine
    from graphon.engine.layer import ExecutionLimitsLayer
    from graphon.graph import Graph
    from graphon.runtime import InitParams, RuntimeState, VariablePool
    from graphon.engine_events import GraphRunSucceededEvent, GraphRunPartialSucceededEvent, GraphRunFailedEvent, GraphRunAbortedEvent

    root, graph_config = request['root_node_id'], request['graph']
    pool = VariablePool()
    for key, value in request.get('inputs', {}).items():
        pool.add([root, key], value)
    for item in request.get('variables', []):
        pool.add(item['selector'], item['value'])
    state = RuntimeState(workflow_id=request['run_id'], variable_pool=pool, start_at=time.perf_counter())
    params = InitParams(workflow_id=request['run_id'], graph_config=graph_config, run_context={}, call_depth=0)
    factory = PureNodeFactory(params, state, _node_classes())
    graph = Graph.init(graph_config=graph_config, node_factory=factory, root_node_id=root)
    # 首批固定一个工作线程，容器调度仍完全使用Engine自身的Loop/Iteration handler。
    engine = Engine(graph, state, workers=1)
    limits = request.get('limits', {})
    engine.add_layer(ExecutionLimitsLayer(max_steps=limits.get('max_steps', 1000), max_time=limits.get('max_seconds', 10)))
    terminal = None
    events = engine.run()
    try:
        for event in events:
            emitter.emit('event', {'event_type': type(event).__name__, 'data': event.model_dump(mode='json')})
            if isinstance(event, GraphRunSucceededEvent):
                terminal = {'status': 'succeeded', 'outputs': event.outputs}
            elif isinstance(event, GraphRunPartialSucceededEvent):
                terminal = {'status': 'partial-succeeded', 'outputs': event.outputs}
            elif isinstance(event, (GraphRunFailedEvent, GraphRunAbortedEvent)):
                terminal = {'status': 'failed', 'outputs': {}, 'error': {
                    'code': 'graph_failed' if isinstance(event, GraphRunFailedEvent) else 'graph_aborted',
                    'message': (getattr(event, 'error', None) or getattr(event, 'reason', None) or '图运行未完成')[:400]}}
    finally:
        events.close()
    if terminal is None:
        raise WorkerError('terminal_event_missing', '图引擎未确认本次运行终态')
    return terminal


def verify_linux_context(runtime_id, boot_id):
    """父进程已核私有manifest和keeper；子进程只核实际用户、解释器与启动标识。"""
    import pwd
    import psutil
    # 与既有boot_identity保持同一摘要口径；读取自己的namespace避免跨用户ptrace读取。
    boot = {'kernel': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
        'init_started_at': psutil.Process(1).create_time(), 'pid_namespace': Path('/proc/self/ns/pid').stat().st_ino}
    actual_boot = hashlib.sha256(json.dumps(boot, sort_keys=True).encode()).hexdigest()
    if (sys.platform != 'linux' or os.environ.get('WSL_DISTRO_NAME') != 'ai-agent-company'
            or os.getuid() != pwd.getpwnam('agentapp').pw_uid
            or Path(sys.prefix) != Path('/opt/ai-agent-company/venvs/api')
            or len(runtime_id) != 32 or any(char not in '0123456789abcdef' for char in runtime_id)
            or actual_boot != boot_id):
        raise WorkerError('wsl_runtime_identity_changed', '本机执行环境归属已变化')


def linux_identity(run_id, runtime_id, boot_id):
    """握手只含本次进程的非机密归属；nonce不等于模型或平台访问凭据。"""
    import psutil
    process = psutil.Process()
    fields = Path('/proc/self/stat').read_text().rpartition(')')[2].split()
    command = process.cmdline()
    return {'pid': process.pid, 'start_ticks': fields[19], 'uid': os.getuid(),
        'executable': process.exe(), 'command_sha256': hashlib.sha256(json.dumps(command).encode()).hexdigest(),
        'worker_path': str(WORKER), 'launch_nonce': os.environ.get('WORKFLOW_RUNTIME_NONCE'),
        'run_id': run_id, 'runtime_id': runtime_id, 'boot_id': boot_id}


def stop_linux_owned(value, runtime_id, boot_id):
    """仅停止握手对应的本次纯图PID；每次信号前重核出生时间、用户、命令及nonce。"""
    import psutil
    verify_linux_context(runtime_id, boot_id)
    expected = value.get('process', {}) if isinstance(value, dict) else {}
    if (not isinstance(value, dict) or not isinstance(expected, dict)
            or value.get('protocol_version') != 1 or expected.get('runtime_id') != runtime_id
            or expected.get('boot_id') != boot_id or expected.get('worker_path') != str(WORKER)
            or not isinstance(expected.get('pid'), int) or expected['pid'] <= 1
            or expected.get('uid') != os.getuid() or len(expected.get('launch_nonce') or '') != 32):
        return {'stopped': False, 'reason': 'process_identity_invalid'}
    def owned():
        try:
            candidate = psutil.Process(expected['pid'])
            fields = Path(f'/proc/{candidate.pid}/stat').read_text().rpartition(')')[2].split()
            command = candidate.cmdline()
            environment = candidate.environ()
            if (fields[19] != expected.get('start_ticks') or candidate.uids().real != expected['uid']
                    or candidate.exe() != expected.get('executable')
                    or hashlib.sha256(json.dumps(command).encode()).hexdigest() != expected.get('command_sha256')
                    or command[-4:] != [str(WORKER), '--owned-wsl', runtime_id, boot_id]
                    or environment.get('WORKFLOW_RUNTIME_NONCE') != expected['launch_nonce']
                    or environment.get('WORKFLOW_RUNTIME_RUN_ID') != expected.get('run_id')):
                raise WorkerError('owned_process_identity_changed', '纯图进程归属已变化，拒绝停止')
            return candidate if candidate.status() != psutil.STATUS_ZOMBIE else None
        except (psutil.NoSuchProcess, FileNotFoundError):
            return None
    process = owned()
    if process is None:
        return {'stopped': True, 'already_exited': True}
    process.terminate()
    try:
        process.wait(timeout=1)
    except psutil.TimeoutExpired:
        process = owned()
        if process is not None:
            process.kill()
            try:
                process.wait(timeout=1)
            except psutil.TimeoutExpired:
                return {'stopped': False, 'reason': 'process_still_alive'}
    return {'stopped': owned() is None}


def main():
    logging.disable(logging.CRITICAL)
    emitter = Emitter(sys.stdout.buffer)
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise WorkerError('input_too_large', '执行请求超过大小预算')
        if len(sys.argv) == 4 and sys.argv[1] == '--stop-owned':
            result = stop_linux_owned(_json(raw), sys.argv[2], sys.argv[3])
            sys.stdout.write(json.dumps(result))
            return 0 if result['stopped'] else 2
        request = validate_request(_json(raw))
        emitter.run_id = request['run_id']
        if len(sys.argv) > 1:
            if len(sys.argv) != 4 or sys.argv[1] != '--owned-wsl':
                raise WorkerError('worker_arguments_invalid', '执行器参数不支持')
            verify_linux_context(sys.argv[2], sys.argv[3])
        elif sys.platform != 'win32':
            raise WorkerError('runtime_identity_required', 'Linux执行器需要本机归属证明')
        install_read_only_guard()
        if os.environ.get('WORKFLOW_RUNTIME_NONCE'):
            if os.environ.get('WORKFLOW_RUNTIME_RUN_ID') != request['run_id'] or len(sys.argv) != 4:
                raise WorkerError('process_identity_invalid', '本次纯图进程标识不一致')
            emitter.emit('process', {'data': linux_identity(request['run_id'], sys.argv[2], sys.argv[3])})
        result = execute(request, emitter)
    except Exception as exc:
        error = exc.payload() if isinstance(exc, WorkerError) else {
            'code': 'graph_validation_failed' if isinstance(exc, ValueError) else 'runtime_failed',
            'message': '图结构或节点参数不正确' if isinstance(exc, ValueError) else '纯图执行未完成',
            'fields': {'exception_type': type(exc).__name__}}
        if isinstance(exc, ModuleNotFoundError):
            error['fields']['missing_module'] = exc.name
        result = {'status': 'failed', 'outputs': {}, 'error': error}
    emitter.emit('result', {'engine': 'graphon', 'engine_version': ENGINE_VERSION, **result}, terminal=True)
    return 0 if result['status'] == 'succeeded' else 2


def _budget(timeout_seconds):
    if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 90):
        raise WorkerError('limits_invalid', '子进程总时限应在90秒内')


def _collect(process, data, timeout_seconds, run_id, *, wsl=False):
    """只消费本次持有的进程句柄；WSL客户端退出不冒充Linux进程已回收。"""
    try:
        stdout, stderr = process.communicate(data, timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            raise WorkerError('owned_process_cleanup_failed', '执行器停止尚未确认', {'pid': process.pid}) from None
        if wsl:
            raise WorkerError('wsl_process_stop_unconfirmed', 'WSL执行器停止尚未确认，请按原运行核对',
                {'client_pid': process.pid, 'stopped': False}) from None
        raise WorkerError('runtime_timeout', '本次图运行达到总时限，所属执行器已停止', {'pid': process.pid, 'stopped': True}) from None
    if len(stdout) > MAX_OUTPUT_BYTES or len(stderr) > 64 * 1024:
        raise WorkerError('output_budget_exceeded', '执行器返回超过大小预算')
    if wsl and process.returncode == 124:
        raise WorkerError('runtime_timeout', '本次纯图进程已由所属Linux timeout停止',
            {'client_pid': process.pid, 'stopped': True})
    if wsl and process.returncode == 137:
        # 137也可能来自资源终止，不能冒称必然是timeout或自动重试。
        raise WorkerError('runtime_terminated', '本次纯图执行器异常终止，运行结果未确认',
            {'client_pid': process.pid, 'exit_code': 137, 'stopped': True})
    records = [_json(line) for line in stdout.splitlines() if line]
    for sequence, item in enumerate(records, start=1):
        if (not isinstance(item, dict) or item.get('protocol_version') != PROTOCOL_VERSION
                or item.get('run_id') != run_id or item.get('sequence') != sequence
                or item.get('kind') not in {'event', 'result'}):
            raise WorkerError('worker_protocol_invalid', '执行器返回了无效事件协议')
    results = [item for item in records if item['kind'] == 'result']
    if len(results) != 1 or not records or records[-1] is not results[0]:
        raise WorkerError('terminal_event_missing', '执行器未确认运行终态，请保留原运行核对')
    result = results[0]
    if (result.get('engine') != 'graphon' or result.get('engine_version') != ENGINE_VERSION
            or result.get('status') not in {'succeeded', 'partial-succeeded', 'failed'}
            or (result.get('status') == 'succeeded' and process.returncode != 0)):
        raise WorkerError('worker_protocol_invalid', '执行器终态与进程结果不一致')
    return {'result': result, 'events': records[:-1],
        'process': {'pid': process.pid, 'exit_code': process.returncode, 'stopped': True}}


def _validate_identity(identity, expected):
    """握手只能引用本次固定命令；不能由事件中的任意PID决定停止目标。"""
    if (not isinstance(identity, dict) or any(identity.get(key) != value for key, value in expected.items())
            or type(identity.get('pid')) is not int or identity['pid'] <= 1
            or type(identity.get('uid')) is not int or identity['uid'] <= 0
            or not isinstance(identity.get('start_ticks'), str) or not identity['start_ticks'].isdigit()
            or not isinstance(identity.get('executable'), str)
            or not identity['executable'].startswith('/opt/ai-agent-company/')):
        raise WorkerError('process_identity_invalid', '纯图进程握手归属不一致')


def _stop_wsl_worker(identity, proof, source, env, timeout_seconds):
    """用相同agentapp解释器二次核验Linux归属；未核实绝不按客户端PID强杀Linux服务。"""
    if identity is None or timeout_seconds <= 0:
        return {'stopped': False, 'reason': 'process_identity_unavailable'}
    command = [str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/wsl.exe'),
        '--distribution', 'ai-agent-company', '--user', 'agentapp', '--exec',
        '/usr/bin/timeout', '--signal=TERM', '--kill-after=1s', '3s', '/usr/bin/env', '-i',
        'HOME=/tmp', 'PATH=/usr/bin:/bin', 'LANG=C.UTF-8', 'WSL_DISTRO_NAME=ai-agent-company',
        '/opt/ai-agent-company/venvs/api/bin/python', '-I', '-B', '-X', 'utf8', '-u', source,
        '--stop-owned', proof['runtime_id'], proof['boot_id']]
    try:
        response = subprocess.run(command, input=json.dumps({'protocol_version': 1, 'process': identity}).encode(),
            capture_output=True, env=env, cwd=str(WORKER.parent), timeout=min(3, timeout_seconds),
            creationflags=subprocess.CREATE_NO_WINDOW)
        if response.returncode or len(response.stdout) > 8192 or len(response.stderr) > 8192:
            return {'stopped': False, 'reason': 'owned_process_cleanup_unconfirmed'}
        result = _json(response.stdout)
        if not isinstance(result, dict) or result.get('stopped') is not True:
            return {'stopped': False, 'reason': 'owned_process_cleanup_unconfirmed'}
        return {'stopped': True, 'already_exited': bool(result.get('already_exited'))}
    except (subprocess.SubprocessError, OSError, WorkerError):
        return {'stopped': False, 'reason': 'owned_process_cleanup_unconfirmed'}


def _collect_live(process, data, timeout_seconds, run_id, on_event, *, expected_identity=None, stop_owned=None,
                  cleanup_deadline=None):
    """后台线程只读取有界字节；主线程逐帧回调，数据库Session不会跨线程。

    调用方预留3秒精确回收预算。事件持久化失败时保留原异常类型，先回收本次进程，
    再由上层回滚事务；不能为了补写错误记录继续使用已失败的Session。
    """
    deadline = time.monotonic() + timeout_seconds
    messages = queue.Queue(maxsize=128)
    halted = threading.Event()
    events, identity, terminal = [], None, None
    def publish(kind, value):
        while not halted.is_set():
            try:
                messages.put((kind, value), timeout=0.05)
                return True
            except queue.Full:
                continue
        return False
    def output():
        size = 0
        try:
            while not halted.is_set():
                line = process.stdout.readline(MAX_OUTPUT_BYTES + 1)
                if not line:
                    publish('eof', None)
                    return
                size += len(line)
                if size > MAX_OUTPUT_BYTES:
                    raise WorkerError('output_budget_exceeded', '执行器返回超过大小预算')
                if not publish('line', line):
                    return
        except BaseException as exc:
            publish('error', exc)
    def errors():
        size = 0
        try:
            while not halted.is_set() and (chunk := process.stderr.read(4096)):
                size += len(chunk)
                if size > 64 * 1024:
                    raise WorkerError('output_budget_exceeded', '执行器错误输出超过预算')
        except BaseException as exc:
            publish('error', exc)
    def input_bytes():
        try:
            process.stdin.write(data)
            process.stdin.close()
        except (OSError, ValueError) as exc:
            publish('error', WorkerError('worker_input_failed', '执行器未接收完整输入'))
    readers = [threading.Thread(target=target, daemon=True) for target in (output, errors, input_bytes)]
    for reader in readers:
        reader.start()
    sequence = 0
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise WorkerError('runtime_timeout', '本次图运行达到总时限')
            try:
                kind, value = messages.get(timeout=remaining)
            except queue.Empty:
                raise WorkerError('runtime_timeout', '本次图运行达到总时限') from None
            if kind == 'error':
                raise value
            if kind == 'eof':
                break
            frame = _json(value)
            sequence += 1
            if (not isinstance(frame, dict) or frame.get('protocol_version') != PROTOCOL_VERSION
                    or frame.get('run_id') != run_id or frame.get('sequence') != sequence
                    or sequence > MAX_EVENTS + 1 or terminal is not None):
                raise WorkerError('worker_protocol_invalid', '执行器返回了无效事件协议')
            if frame.get('kind') == 'process':
                if sequence != 1 or expected_identity is None:
                    raise WorkerError('worker_protocol_invalid', '进程握手顺序不正确')
                _validate_identity(frame.get('data'), expected_identity)
                identity = frame['data']
            elif frame.get('kind') == 'event':
                if ((expected_identity is not None and identity is None)
                        or not isinstance(frame.get('event_type'), str) or not isinstance(frame.get('data'), dict)):
                    raise WorkerError('worker_protocol_invalid', '原生事件缺少进程归属或有效内容')
                on_event(frame)
                events.append(frame)
            elif frame.get('kind') == 'result':
                terminal = frame
            else:
                raise WorkerError('worker_protocol_invalid', '执行器事件类型不支持')
        process.wait(timeout=max(0.001, deadline - time.monotonic()))
        # 进程退出后错误管道也应结束，避免stdout先EOF而漏掉stderr超预算。
        for reader in readers:
            reader.join(timeout=max(0, min(0.2, deadline - time.monotonic())))
        while not messages.empty():
            kind, value = messages.get_nowait()
            if kind == 'error':
                raise value
        if terminal is None:
            raise WorkerError('terminal_event_missing', '执行器未确认运行终态，请保留原运行核对')
        if (terminal.get('engine') != 'graphon' or terminal.get('engine_version') != ENGINE_VERSION
                or terminal.get('status') not in {'succeeded', 'partial-succeeded', 'failed'}
                or not isinstance(terminal.get('outputs'), dict)
                or (terminal.get('status') == 'succeeded' and (process.returncode != 0
                    or (expected_identity is not None and identity is None)))):
            raise WorkerError('worker_protocol_invalid', '执行器终态与进程结果不一致')
        return {'result': terminal, 'events': events,
            'process': {'pid': process.pid, 'exit_code': process.returncode, 'stopped': True,
                **({'linux': identity} if identity else {})}}
    except BaseException as exc:
        # 停Linux worker与停Windows客户端是两条独立证据，不能混为一谈。
        try:
            cleanup = stop_owned(identity) if stop_owned is not None else {'stopped': False}
        except Exception:
            cleanup = {'stopped': False, 'reason': 'owned_process_cleanup_unconfirmed'}
        try:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=max(0.001, min(1,
                cleanup_deadline - time.monotonic() if cleanup_deadline is not None else 1)))
            if stop_owned is None:
                cleanup = {'stopped': True}
        except (OSError, subprocess.SubprocessError):
            cleanup = {**cleanup, 'client_stopped': False}
        exc.workflow_cleanup = cleanup
        if isinstance(exc, WorkerError):
            exc.fields.update(cleanup)
        elif isinstance(exc, subprocess.TimeoutExpired):
            raise WorkerError('runtime_timeout', '本次图运行达到总时限', cleanup) from None
        raise
    finally:
        halted.set()
        # 队列满时也能结束读线程；只在该线程结束后关闭管道，避免跨线程close阻塞。
        for reader in readers:
            reader.join(timeout=0.05)
        if not any(reader.is_alive() for reader in readers):
            for pipe in (process.stdout, process.stderr, process.stdin):
                try:
                    pipe.close()
                except OSError:
                    pass


def run_isolated(python_executable: Path, request: dict, *, timeout_seconds: float = 15, on_event=None) -> dict:
    """从平台启动一个自有纯图子进程；超时只终止该Popen句柄，不重试或扫描杀进程。

    Python必须位于本项目.local内的独立环境；环境变量只保留操作系统基础项，
    不继承provider、数据库、代理或用户Python路径。调用方负责先保存Run/Task预约。
    """
    _budget(timeout_seconds)
    validate_request(request)
    executable = Path(python_executable).resolve(strict=True)
    if not executable.is_relative_to(PROJECT_ROOT / '.local') or executable.name.lower() not in {'python.exe', 'python'}:
        raise WorkerError('runtime_path_invalid', '请选择本项目独立Python执行环境')
    data = json.dumps(request, ensure_ascii=False, allow_nan=False).encode('utf-8')
    if len(data) > MAX_INPUT_BYTES:
        raise WorkerError('input_too_large', '执行请求超过大小预算')
    env = {key: value for key, value in os.environ.items() if key.upper() in {'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP'}}
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
    process = subprocess.Popen([str(executable), '-I', '-B', '-X', 'utf8', '-u', str(WORKER)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(WORKER.parent), env=env, **options)
    if on_event is not None:
        return _collect_live(process, data, timeout_seconds, request['run_id'], on_event)
    return _collect(process, data, timeout_seconds, request['run_id'])


def _wsl_proof(timeout_seconds=5):
    """复用既有D盘发行版、Windows keeper和Linux启动标识证明，不启动或修复任何服务。"""
    native = PROJECT_ROOT / 'deploy/native'
    sys.path.insert(0, str(native))
    try:
        wsl = importlib.import_module('wsl')
        windows = importlib.import_module('windows')
        lifetime = importlib.import_module('wsl_lifetime')
        for module in (wsl, windows, lifetime):
            if Path(module.__file__).resolve().parent != native:
                raise WorkerError('wsl_helper_source_invalid', '本机部署证明代码来源不匹配')
        wsl.require_registered_distro()
        state = windows.load(windows.DEFAULT_ROOT)
        keeper = state.get('processes', {}).get(lifetime.COMPONENT, {})
        if not windows.owned_process(windows.DEFAULT_ROOT, lifetime.COMPONENT, state):
            raise WorkerError('wsl_keeper_not_running', '本项目Linux运行环境未就绪')
        # 复用同一只读status入口，但收紧本次超时，不能继承其通用25秒预算。
        checked = subprocess.run(lifetime.windows_command('status'), env=wsl.clean_environment(),
            capture_output=True, timeout=timeout_seconds, creationflags=subprocess.CREATE_NO_WINDOW)
        if checked.returncode or len(checked.stdout) > 128 * 1024:
            raise WorkerError('wsl_runtime_identity_changed', '本机运行归属核验未通过')
        proof = _json(checked.stdout)
        if (not proof.get('running') or proof.get('requires_boot_reconciliation')
                or proof.get('runtime_id') != keeper.get('runtime_id') or proof.get('keeper_id') != keeper.get('keeper_id')):
            raise WorkerError('wsl_runtime_identity_changed', '本项目Linux运行环境归属已变化')
        return proof, wsl.linux_path(WORKER), wsl.clean_environment()
    finally:
        sys.path.remove(str(native))


def run_wsl(request: dict, *, timeout_seconds: float = 15, on_event=None):
    """显式使用已运行的专属WSL只读依赖；GNU timeout只拥有本次纯图进程组。"""
    _budget(timeout_seconds)
    validate_request(request)
    if sys.platform != 'win32' or timeout_seconds <= 4:
        raise WorkerError('wsl_launcher_invalid', 'WSL入口需要Windows及至少4秒完整回收预算')
    data = json.dumps(request, ensure_ascii=False, allow_nan=False).encode('utf-8')
    if len(data) > MAX_INPUT_BYTES:
        raise WorkerError('input_too_large', '执行请求超过大小预算')
    started = time.monotonic()
    try:
        proof, source, env = _wsl_proof(min(5, timeout_seconds - 4))
    except subprocess.TimeoutExpired:
        raise WorkerError('runtime_proof_timeout', '归属核验已用尽预算，未执行本次图') from None
    remaining = timeout_seconds - (time.monotonic() - started)
    if remaining <= 4:
        raise WorkerError('runtime_proof_timeout', '归属核验已用尽预算，未执行本次图')
    linux_seconds = remaining - 3
    system = Path(os.environ.get('SystemRoot', 'C:/Windows'))
    nonce = secrets.token_hex(16) if on_event is not None else None
    command = [str(system / 'System32/wsl.exe'), '--distribution', 'ai-agent-company', '--user', 'agentapp', '--exec',
        '/usr/bin/timeout', '--signal=TERM', '--kill-after=2s', f'{linux_seconds:.3f}s',
        '/usr/bin/env', '-i', 'HOME=/tmp', 'PATH=/usr/bin:/bin', 'LANG=C.UTF-8', 'WSL_DISTRO_NAME=ai-agent-company',
        *([f'WORKFLOW_RUNTIME_NONCE={nonce}', f'WORKFLOW_RUNTIME_RUN_ID={request["run_id"]}'] if nonce else []),
        '/opt/ai-agent-company/venvs/api/bin/python', '-I', '-B', '-X', 'utf8', '-u', source,
        '--owned-wsl', proof['runtime_id'], proof['boot_id']]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env, cwd=str(WORKER.parent), creationflags=subprocess.CREATE_NO_WINDOW)
    if on_event is None:
        result = _collect(process, data, remaining, request['run_id'], wsl=True)
    else:
        execution_deadline = time.monotonic() + remaining
        python_command = command[command.index('/opt/ai-agent-company/venvs/api/bin/python'):]
        expected = {'run_id': request['run_id'], 'runtime_id': proof['runtime_id'], 'boot_id': proof['boot_id'],
            'worker_path': source, 'launch_nonce': nonce,
            'command_sha256': hashlib.sha256(json.dumps(python_command).encode()).hexdigest()}
        result = _collect_live(process, data, remaining - 3, request['run_id'], on_event, expected_identity=expected,
            stop_owned=lambda identity: _stop_wsl_worker(identity, proof, source, env,
                max(0, execution_deadline - time.monotonic())), cleanup_deadline=execution_deadline)
    # 这是Windows客户端PID，不能让后续维护者拿它冒充Linux worker归属或停止依据。
    result['process']['client_pid'] = result['process'].pop('pid')
    result['runtime'] = {'kind': 'owned-wsl', 'runtime_id': proof['runtime_id'], 'boot_id': proof['boot_id']}
    return result


if __name__ == '__main__':
    raise SystemExit(main())
