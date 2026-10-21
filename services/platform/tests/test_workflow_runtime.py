"""独立Python 3.13执行无网络纯图；其余协议/停止分支仅用替身验证。"""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location('owned_workflow_worker', ROOT / 'services/workflow_runtime/worker.py')
worker = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = worker
SPEC.loader.exec_module(worker)
PYTHON = ROOT / '.local/native_windows/venvs/dify/Scripts/python.exe'


def node(id, kind, *, owner=None, version='1', **data):
    result = {'id': id, 'data': {'type': kind, 'title': id, 'version': version, **data}}
    if owner:
        result['data']['container_id'] = owner
    return result


def edge(source, target, handle='source'):
    return {'id': source + '-' + target + '-' + handle, 'source': source, 'target': target, 'sourceHandle': handle}


def end(id='end', selector=None, *, variable='result'):
    return node(id, 'end', outputs=[{'variable': variable, 'value_selector': selector or ['start', 'message']}])


def request(nodes=None, edges=None, inputs=None, variables=None):
    return {'protocol_version': 1, 'run_id': 'pure-graph-fixture', 'root_node_id': 'start',
        'graph': {'nodes': nodes or [node('start', 'start', variables=[]), end()],
            'edges': edges or [edge('start', 'end')]}, 'inputs': inputs or {'message': '点检记录'},
        'variables': variables or [], 'limits': {'max_steps': 100, 'max_seconds': 10}}


def run(value):
    if os.environ.get('WORKFLOW_PURE_TEST_WSL') == '1':
        return worker.run_wsl(value, timeout_seconds=15)
    pytest.skip('真实图执行需显式WORKFLOW_PURE_TEST_WSL=1；默认仅运行纯协议与Mock，不回退旧Windows环境')


def execute(value):
    result = run(value)
    assert result['process']['stopped'] and result['process']['exit_code'] == 0, result['result'].get('error')
    assert result['result']['status'] == 'succeeded', result['result'].get('error')
    assert any(item['event_type'] == 'GraphRunSucceededEvent' for item in result['events'])
    return result


def test_start_end_executes_actual_graphon_and_serializes_input():
    result = execute(request())
    assert result['result']['outputs'] == {'result': '点检记录'}
    assert result['result']['engine_version'] == '0.8.0'


@pytest.mark.parametrize('number, selected', [(9, 'high'), (2, 'low')])
def test_branch_uses_real_case_handle_and_does_not_run_other_branch(number, selected):
    value = request([node('start', 'start'), node('branch', 'if-else', cases=[{
        'case_id': 'high', 'logical_operator': 'and', 'conditions': [{'variable_selector': ['start', 'number'],
            'comparison_operator': '>', 'value': '5'}]}]),
        end('high', ['start', 'high']), end('low', ['start', 'low'])],
        [edge('start', 'branch'), edge('branch', 'high', 'high'), edge('branch', 'low', 'false')],
        inputs={'number': number, 'high': '偏高', 'low': '正常'})
    result = execute(value)
    assert result['result']['outputs']['result'] == ('偏高' if selected == 'high' else '正常')
    completed = {event['data']['node_id'] for event in result['events'] if event['event_type'] == 'NodeRunSucceededEvent'}
    assert selected in completed and ({'high', 'low'} - {selected}).isdisjoint(completed)


def test_variable_assigner_v2_changes_typed_variable_and_aggregates_it():
    value = request([node('start', 'start'), node('assign', 'assigner', version='2', items=[{
        'variable_selector': ['conversation', 'count'], 'input_type': 'constant', 'operation': '+=', 'value': 2}]),
        node('aggregate', 'variable-aggregator', output_type='number', variables=[['conversation', 'count']]),
        end(selector=['aggregate', 'output'])],
        [edge('start', 'assign'), edge('assign', 'aggregate'), edge('aggregate', 'end')],
        variables=[{'selector': ['conversation', 'count'], 'value': 5}])
    result = execute(value)
    assert result['result']['outputs'] == {'result': 7}
    assert any(item['event_type'] == 'NodeRunVariableUpdatedEvent' for item in result['events'])


@pytest.mark.parametrize('parallel', [False, True])
def test_iteration_preserves_child_state_and_output_order(parallel):
    value = request([node('start', 'start'), node('iterate', 'iteration', start_node_id='iteration-start',
        iterator_selector=['start', 'items'], output_selector=['take-item', 'output'],
        is_parallel=parallel, parallel_nums=2),
        node('iteration-start', 'iteration-start', owner='iterate'),
        node('take-item', 'variable-aggregator', owner='iterate', output_type='string', variables=[['iterate', 'item']]),
        end(selector=['iterate', 'output'])],
        [edge('start', 'iterate'), edge('iterate', 'end'), edge('iteration-start', 'take-item')],
        inputs={'items': ['设备A', '设备B', '设备C']})
    result = execute(value)
    assert result['result']['outputs'] == {'result': ['设备A', '设备B', '设备C']}
    assert sum(item['event_type'] == 'NodeRunSucceededEvent' and item['data']['node_id'] == 'take-item'
        for item in result['events']) == 3


def test_nested_iteration_uses_factory_graph_scope_for_each_child_frame():
    value = request([node('start', 'start'),
        node('outer', 'iteration', start_node_id='outer-start', iterator_selector=['start', 'items'],
            output_selector=['inner', 'output'], flatten_output=False),
        node('outer-start', 'iteration-start', owner='outer'),
        node('inner', 'iteration', owner='outer', start_node_id='inner-start', iterator_selector=['outer', 'item'],
            output_selector=['copy', 'output']),
        node('inner-start', 'iteration-start', owner='inner'),
        node('copy', 'variable-aggregator', owner='inner', output_type='string', variables=[['inner', 'item']]),
        end(selector=['outer', 'output'])],
        [edge('start', 'outer'), edge('outer', 'end'), edge('outer-start', 'inner'), edge('inner-start', 'copy')],
        inputs={'items': [['甲', '乙'], ['丙']]})
    assert execute(value)['result']['outputs'] == {'result': [['甲', '乙'], ['丙']]}


def test_loop_uses_engine_container_handler_and_stops_at_declared_count():
    value = request([node('start', 'start'), node('loop', 'loop', start_node_id='loop-start',
        loop_count=3, break_conditions=[], logical_operator='and', loop_variables=[{
            'label': 'count', 'var_type': 'number', 'value_type': 'constant', 'value': 0}]),
        node('loop-start', 'loop-start', owner='loop'),
        node('increment', 'assigner', owner='loop', version='2', items=[{
            'variable_selector': ['loop', 'count'], 'input_type': 'constant', 'operation': '+=', 'value': 1}]),
        end(selector=['loop', 'count'])],
        [edge('start', 'loop'), edge('loop', 'end'), edge('loop-start', 'increment')])
    assert execute(value)['result']['outputs'] == {'result': 3}


@pytest.mark.parametrize('kind', ['llm', 'tool', 'code', 'http-request', 'knowledge-retrieval', 'agent', 'human-input', 'template-transform'])
def test_side_effecting_or_unadapted_node_is_rejected_before_spawn(kind, monkeypatch):
    monkeypatch.setattr(worker.subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('不应派发子进程'))
    value = request([node('start', 'start'), node('denied', kind), end()], [edge('start', 'denied'), edge('denied', 'end')])
    with pytest.raises(worker.WorkerError) as denied:
        worker.run_isolated(PYTHON, value)
    assert denied.value.code == 'node_not_supported' and denied.value.fields['node_type'] == kind


def test_invalid_child_is_prevalidated_instead_of_hidden_until_iteration_runs():
    value = request([node('start', 'start'), node('iterate', 'iteration', start_node_id='inner',
        iterator_selector=['start', 'items'], output_selector=['inner', 'output']),
        node('inner', 'iteration-start', owner='iterate'),
        node('invalid', 'assigner', owner='iterate', version='2', items='not-a-list'),
        end(selector=['iterate', 'output'])],
        [edge('start', 'iterate'), edge('iterate', 'end'), edge('inner', 'invalid')], inputs={'items': []})
    result = run(value)
    assert result['result']['status'] == 'failed' and result['events'] == []
    assert result['result']['error']['code'] == 'graph_validation_failed'


def test_duplicate_ids_and_cross_container_edges_are_not_silently_rewritten():
    value = request([node('start', 'start'), node('start', 'start'), end()])
    result = run(value)
    assert result['result']['status'] == 'failed' and result['events'] == []


def test_timeout_stops_only_spawned_owned_handle_and_never_retries(monkeypatch):
    calls = []
    class Process:
        pid, returncode = 1234, None
        def communicate(self, input=None, timeout=None):
            calls.append(('communicate', timeout))
            if input is not None:
                raise subprocess.TimeoutExpired('owned', timeout)
            self.returncode = -1
            return b'', b''
        def kill(self):
            calls.append(('kill', self.pid))
    monkeypatch.setattr(worker.subprocess, 'Popen', lambda *args, **kwargs: Process())
    with pytest.raises(worker.WorkerError) as failure:
        worker.run_isolated(PYTHON, request(), timeout_seconds=0.5)
    assert failure.value.code == 'runtime_timeout' and failure.value.fields == {'pid': 1234, 'stopped': True}
    assert calls == [('communicate', 0.5), ('kill', 1234), ('communicate', 2)]


def test_failed_cleanup_does_not_claim_process_stopped(monkeypatch):
    class Process:
        pid = 4567
        def communicate(self, input=None, timeout=None):
            raise subprocess.TimeoutExpired('owned', timeout)
        def kill(self):
            pass
    monkeypatch.setattr(worker.subprocess, 'Popen', lambda *args, **kwargs: Process())
    with pytest.raises(worker.WorkerError) as failure:
        worker.run_isolated(PYTHON, request())
    assert failure.value.code == 'owned_process_cleanup_failed' and failure.value.fields == {'pid': 4567}


def test_spawn_does_not_inherit_database_model_or_proxy_secrets(monkeypatch):
    captured = []
    monkeypatch.setenv('DATABASE_URL', 'postgresql://secret.invalid/db')
    monkeypatch.setenv('OPENAI_API_KEY', 'only-fixture-not-real')
    monkeypatch.setenv('HTTPS_PROXY', 'http://proxy.invalid')
    result = {'protocol_version': 1, 'sequence': 1, 'run_id': 'pure-graph-fixture', 'kind': 'result', 'engine': 'graphon',
        'engine_version': '0.8.0', 'status': 'failed', 'outputs': {}, 'error': {'code': 'fixture'}}
    class Process:
        pid, returncode = 111, 2
        def communicate(self, input=None, timeout=None):
            return json.dumps(result).encode(), b''
    def spawn(command, **kwargs):
        captured.append((command, kwargs))
        return Process()
    monkeypatch.setattr(worker.subprocess, 'Popen', spawn)
    worker.run_isolated(PYTHON, request())
    command, options = captured[0]
    assert command[1:6] == ['-I', '-B', '-X', 'utf8', '-u']
    assert not {'DATABASE_URL', 'OPENAI_API_KEY', 'HTTPS_PROXY', 'PYTHONPATH'}.intersection(options['env'])
    assert options['cwd'] == str(worker.WORKER.parent)


@pytest.mark.parametrize('corrupt', [b'not-json', b'', b'{"protocol_version":1,"sequence":5,"kind":"event"}\n'])
def test_invalid_protocol_is_not_interpreted_as_success(monkeypatch, corrupt):
    class Process:
        pid, returncode = 111, 0
        def communicate(self, input=None, timeout=None):
            return corrupt, b''
    monkeypatch.setattr(worker.subprocess, 'Popen', lambda *args, **kwargs: Process())
    with pytest.raises(worker.WorkerError):
        worker.run_isolated(PYTHON, request())


def test_protocol_rejects_duplicate_json_keys_nonfinite_values_and_unknown_version():
    for raw in [b'{"a":1,"a":2}', b'{"number":NaN}']:
        with pytest.raises(worker.WorkerError):
            worker._json(raw)
    value = request()
    value['graph']['nodes'][1]['data']['version'] = '999'
    with pytest.raises(worker.WorkerError) as rejected:
        worker.validate_request(value)
    assert rejected.value.code == 'node_not_supported'


@pytest.mark.parametrize('selector', [[], ['conversation'], ['conversation', 'state', 'reading'], ['part'] * 10])
def test_initial_variable_write_selector_is_rejected_before_wsl_proof_or_spawn(selector, monkeypatch):
    monkeypatch.setattr(worker, '_wsl_proof', lambda *args: pytest.fail('非法写入不能检查或启动WSL'))
    monkeypatch.setattr(worker.subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('非法写入不能派发子进程'))
    value = request(variables=[{'selector': selector, 'value': {'reading': 42}}])
    with pytest.raises(worker.WorkerError) as denied:
        worker.run_wsl(value)
    assert denied.value.code == 'variable_invalid'


def test_two_part_initial_variable_keeps_nested_node_read_selectors_unchanged():
    selector = ['conversation', 'state', 'measurements']
    value = request([node('start', 'start'),
        node('aggregate', 'variable-aggregator', output_type='array[number]', variables=[selector]),
        end(selector=['aggregate', 'output'])],
        [edge('start', 'aggregate'), edge('aggregate', 'end')],
        variables=[{'selector': ['conversation', 'state'], 'value': {'measurements': [0, 42]}}])
    assert worker.validate_request(value) is value
    assert value['variables'][0]['selector'] == ['conversation', 'state']
    assert value['graph']['nodes'][1]['data']['variables'] == [selector]


@pytest.mark.parametrize('reference', ['https://unapproved.invalid/schema', 'file:///etc/passwd', 'relative.json'])
@pytest.mark.parametrize('as_text', [False, True])
def test_json_schema_external_reference_is_rejected_before_child_or_io(reference, as_text, monkeypatch):
    monkeypatch.setattr(worker.subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('不应派发子进程'))
    schema = {'properties': {'data': {'$ref': reference}}}
    value = request([node('start', 'start', variables=[{'variable': 'json', 'label': '结构', 'type': 'json_object',
        'json_schema': json.dumps(schema) if as_text else schema}]), end()])
    with pytest.raises(worker.WorkerError) as failure:
        worker.run_isolated(PYTHON, value)
    assert failure.value.code == 'external_schema_reference'


def test_local_schema_reference_keeps_input_validation_capability():
    value = request([node('start', 'start', variables=[{'variable': 'data', 'label': '数据', 'type': 'json_object',
        'json_schema': {'$defs': {'item': {'type': 'number'}}, 'properties': {'reading': {'$ref': '#/$defs/item'}}}}]),
        end(selector=['start', 'data'])], inputs={'data': {'reading': 42}})
    assert execute(value)['result']['outputs'] == {'result': {'reading': 42}}


def test_audit_guard_denies_all_external_capabilities_and_file_changes(monkeypatch):
    guards = []
    monkeypatch.setattr(worker.sys, 'addaudithook', guards.append)
    worker.install_read_only_guard()
    guard = guards[0]
    for event, args in [('socket.__new__', ()), ('socket.connect', ()), ('sqlite3.connect', ('test.db',)),
            ('subprocess.Popen', ()), ('os.system', ('anything',)), ('os.remove', ('owned',)),
            ('open', ('owned', 'w', os.O_WRONLY)), ('open', ('owned', None, os.O_CREAT))]:
        with pytest.raises(PermissionError):
            guard(event, args)
    guard('open', ('source.py', 'r', os.O_RDONLY))


def test_wsl_command_uses_only_verified_runtime_and_clean_agentapp_environment(monkeypatch):
    captured = []
    proof = {'runtime_id': 'a' * 32, 'boot_id': 'b' * 64}
    monkeypatch.setattr(worker, '_wsl_proof', lambda timeout: (proof, '/mnt/e/owned/worker.py', {'SystemRoot': 'C:/Windows'}))
    record = {'protocol_version': 1, 'sequence': 1, 'run_id': 'pure-graph-fixture', 'kind': 'result',
        'engine': 'graphon', 'engine_version': '0.8.0', 'status': 'failed', 'outputs': {}}
    class Process:
        pid, returncode = 111, 2
        def communicate(self, input=None, timeout=None):
            return json.dumps(record).encode(), b''
    def spawn(command, **kwargs):
        captured.append((command, kwargs))
        return Process()
    monkeypatch.setattr(worker.subprocess, 'Popen', spawn)
    result = worker.run_wsl(request())
    command, options = captured[0]
    assert command[1:6] == ['--distribution', 'ai-agent-company', '--user', 'agentapp', '--exec']
    assert '/usr/bin/timeout' in command and '--kill-after=2s' in command
    assert command[command.index('/usr/bin/env') + 1] == '-i'
    assert '/opt/ai-agent-company/venvs/api/bin/python' in command
    assert command[-3:] == ['--owned-wsl', proof['runtime_id'], proof['boot_id']]
    assert options['env'] == {'SystemRoot': 'C:/Windows'} and result['runtime']['runtime_id'] == proof['runtime_id']
    assert result['process']['client_pid'] == 111 and 'pid' not in result['process']


@pytest.mark.parametrize('code, expected', [(124, 'runtime_timeout'), (137, 'runtime_terminated')])
def test_wsl_timeout_and_resource_exit_remain_distinct_without_retry(monkeypatch, code, expected):
    monkeypatch.setattr(worker, '_wsl_proof', lambda timeout: ({'runtime_id': 'a' * 32, 'boot_id': 'b' * 64}, '/mnt/e/owned/worker.py', {}))
    class Process:
        pid, returncode = 111, code
        def communicate(self, input=None, timeout=None):
            return b'', b''
    calls = []
    monkeypatch.setattr(worker.subprocess, 'Popen', lambda *args, **kwargs: calls.append(1) or Process())
    with pytest.raises(worker.WorkerError) as failure:
        worker.run_wsl(request())
    assert failure.value.code == expected and len(calls) == 1


def test_wsl_client_timeout_does_not_claim_linux_process_was_stopped():
    class Process:
        pid, returncode = 111, None
        def communicate(self, input=None, timeout=None):
            if input is not None:
                raise subprocess.TimeoutExpired('fixture', timeout)
            return b'', b''
        def kill(self):
            pass
    with pytest.raises(worker.WorkerError) as failure:
        worker._collect(Process(), b'{}', 1, 'fixture', wsl=True)
    assert failure.value.code == 'wsl_process_stop_unconfirmed' and failure.value.fields['stopped'] is False


@pytest.mark.parametrize('matches', [True, False])
def test_worker_uses_shared_raw_tick_boot_identity_without_root_manifest_or_wall_clock(monkeypatch, matches):
    from types import SimpleNamespace
    calls = []
    monkeypatch.setitem(sys.modules, 'pwd', SimpleNamespace(getpwnam=lambda _: SimpleNamespace(pw_uid=1001)))
    monkeypatch.setattr(worker.sys, 'platform', 'linux')
    monkeypatch.setattr(worker.sys, 'prefix', '/opt/ai-agent-company/venvs/api')
    monkeypatch.setattr(worker.os, 'getuid', lambda: 1001, raising=False)
    monkeypatch.setenv('WSL_DISTRO_NAME', 'ai-agent-company')
    def identity(**kwargs):
        calls.append(kwargs)
        return 'a' * 64
    monkeypatch.setattr(worker, 'linux_identity_helper', lambda: SimpleNamespace(boot_identity_v2=identity))
    if matches:
        worker.verify_linux_context('b' * 32, 'a' * 64)
    else:
        with pytest.raises(worker.WorkerError) as denied:
            worker.verify_linux_context('b' * 32, 'c' * 64)
        assert denied.value.code == 'wsl_runtime_identity_changed'
    assert calls == [{'namespace_pid': 'self'}]


def test_worker_identity_helper_is_fixed_project_source():
    helper = worker.linux_identity_helper()
    assert Path(helper.__file__).resolve() == ROOT / 'deploy/native/wsl_identity.py'
    assert helper.IDENTITY_VERSION == 2
