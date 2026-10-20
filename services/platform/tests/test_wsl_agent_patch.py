"""只在临时文件和隔离Python对象中验证Agent预算补丁，不访问WSL、HTTP或数据库。"""

import ast
import asyncio
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

from pydantic import BaseModel, Field, ValidationError
import pytest


ROOT = Path(__file__).resolve().parents[3]
NATIVE = ROOT / 'deploy/native'

# 用独立记录的精简上游控制流验证参数传递，不在仓库复制整套原参考代码。
FRAGMENTS = {
    'server/settings.py': '''class ServerSettings(BaseModel):
    run_timeout_seconds: float = Field(default=DEFAULT_AGENT_RUN_TIMEOUT_SECONDS, gt=0)
''',
    'server/app.py': '''def build(resolved_settings):
    if True:
        scheduler = RunScheduler(
            run_timeout_seconds=resolved_settings.run_timeout_seconds,
        )
        return scheduler
''',
    'runtime/run_scheduler.py': '''class RunScheduler:
    run_timeout_seconds: float

    def __init__(self, *, store=None, services=None,
        run_timeout_seconds: float = DEFAULT_AGENT_RUN_TIMEOUT_SECONDS,
    ):
        self.store = store
        self.services = services
        self.run_timeout_seconds = run_timeout_seconds

    def make_runner(self):
        return AgentRunRunner(
            run_timeout_seconds=self.run_timeout_seconds,
        )
''',
    'runtime/runner.py': '''class AgentRunRunner:
    run_timeout_seconds: float

    def __init__(self, *,
        run_timeout_seconds: float = DEFAULT_AGENT_RUN_TIMEOUT_SECONDS,
        stream_text_delta_flush_interval_seconds=0.1,
    ):
        if stream_text_delta_flush_interval_seconds <= 0:
            raise ValueError('invalid_flush')
        self.run_timeout_seconds = run_timeout_seconds

    async def _run_agent(self, agent):
        return await agent.run(
                            usage_limits=UsageLimits(request_limit=_MAX_AGENT_STEPS_PER_RUN),
        )
''',
}


def tool(name='wsl_agent_patch'):
    spec = importlib.util.spec_from_file_location(name, NATIVE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def sample(tmp_path, monkeypatch):
    module = tool()
    patches, originals, outputs = [], {}, {}
    for patch in module.PATCHES:
        original = FRAGMENTS[patch.path].encode()
        output = original
        for old, new in patch.replacements:
            assert output.count(old.encode()) == 1
            output = output.replace(old.encode(), new.encode(), 1)
        patches.append(replace(patch, original_sha256=module.sha256(original), patched_sha256=module.sha256(output)))
        path = tmp_path / patch.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(original)
        originals[patch.path], outputs[patch.path] = original, output
    monkeypatch.setattr(module, 'PATCHES', tuple(patches))
    return module, tmp_path, originals, outputs


def compiled_modules(module, originals):
    """只执行小型模型与构造器；任何异步run均使用内存替身。"""
    namespace = {'BaseModel': BaseModel, 'Field': Field, 'DEFAULT_AGENT_RUN_TIMEOUT_SECONDS': 3600,
        '_MAX_AGENT_STEPS_PER_RUN': 500}
    for name in ['server/settings.py', 'runtime/runner.py', 'runtime/run_scheduler.py', 'server/app.py']:
        patch = next(item for item in module.PATCHES if item.path == name)
        prepared, _ = module.prepare_file(originals[name], patch)
        exec(compile(prepared, name, 'exec'), namespace)
    return namespace


def test_production_digest_contract_and_original_defaults_are_locked():
    module = tool()
    assert module.SOURCE_VERSION == '1.17.1'
    assert module.ENVIRONMENT_VARIABLE == 'DIFY_AGENT_RUN_REQUEST_LIMIT'
    assert module.DEFAULT_REQUEST_LIMIT == 500
    assert len(module.PATCHES) == 4
    assert {item.path for item in module.PATCHES} == set(FRAGMENTS)
    for patch in module.PATCHES:
        assert len(patch.original_sha256) == len(patch.patched_sha256) == 64
        assert patch.original_sha256 != patch.patched_sha256
        assert all('shell' not in old + new for old, new in patch.replacements)


def test_locked_full_source_matches_real_before_after_sha_without_writing():
    module = tool()
    source = ROOT / '.local/native_windows/sources/dify/dify-agent/src/dify_agent'
    if not source.exists():
        pytest.skip('Git交付不含原参考源码；其余行为回归使用独立小型控制流')
    for patch in module.PATCHES:
        raw = (source / patch.path).read_bytes()
        result, receipt = module.prepare_file(raw, patch)
        assert module.sha256(result) == patch.patched_sha256
        ast.parse(result)
        assert receipt['observed_before_sha256'] == patch.original_sha256


def test_group_applies_atomically_per_file_and_second_call_is_no_write(sample):
    module, root, originals, outputs = sample
    receipt = module.apply_patch(root)
    assert receipt['applied'] and receipt['default_request_limit'] == 500
    assert receipt['database_requests'] == receipt['model_requests'] == 0
    assert receipt['shell_capability_changed'] is False
    assert all((root / name).read_bytes() == value for name, value in outputs.items())
    before = {name: (root / name).stat().st_mtime_ns for name in outputs}
    second = module.apply_patch(root)
    assert second['applied'] is False
    assert all((root / name).stat().st_mtime_ns == value for name, value in before.items())
    assert not list(root.rglob('*.pending')) and not (root / '.agent-request-limit.lock').exists()
    assert originals != outputs


def test_last_unknown_file_prevents_any_group_write(sample):
    module, root, originals, _ = sample
    final = root / 'runtime/runner.py'
    final.write_bytes(originals['runtime/runner.py'] + b'# unknown edit\n')
    with pytest.raises(RuntimeError, match='source_unrecognized:runtime/runner.py'):
        module.apply_patch(root)
    assert all((root / name).read_bytes() == value for name, value in originals.items() if name != 'runtime/runner.py')
    assert not (root / '.agent-request-limit.lock').exists()


def test_verified_only_reports_required_changes_without_write(sample):
    module, root, originals, _ = sample
    receipt = module.apply_patch(root, verify_only=True)
    assert receipt['verified_only'] and receipt['changes_required'] and not receipt['applied']
    assert all((root / name).read_bytes() == value for name, value in originals.items())
    assert not (root / '.agent-request-limit.lock').exists()


def test_known_partial_group_can_be_explicitly_completed(sample):
    module, root, _, outputs = sample
    (root / 'server/settings.py').write_bytes(outputs['server/settings.py'])
    result = module.apply_patch(root)
    assert [item['applied'] for item in result['files']] == [False, True, True, True]
    assert all((root / name).read_bytes() == value for name, value in outputs.items())


def test_exclusive_lock_blocks_parallel_writer_without_deleting_its_lock(sample):
    module, root, originals, _ = sample
    lock = root / '.agent-request-limit.lock'
    lock.write_text('another-owner')
    with pytest.raises(FileExistsError):
        module.apply_patch(root)
    assert lock.read_text() == 'another-owner'
    assert all((root / name).read_bytes() == value for name, value in originals.items())


def test_source_changed_during_commit_is_not_overwritten(sample, monkeypatch):
    module, root, originals, outputs = sample
    real_writer = module._write_file
    changed = root / 'server/settings.py'
    def race(path, original, output, before):
        path.write_bytes(b'concurrent-owner\n')
        return real_writer(path, original, output, before)
    monkeypatch.setattr(module, '_write_file', race)
    with pytest.raises(RuntimeError, match='source_changed'):
        module.apply_patch(root)
    assert changed.read_bytes() == b'concurrent-owner\n'
    assert (root / 'runtime/runner.py').read_bytes() == originals['runtime/runner.py']
    assert not list(root.rglob('*.pending'))


def test_patch_rejects_wrong_target_digest_before_any_write(sample, monkeypatch):
    module, root, originals, _ = sample
    monkeypatch.setattr(module, 'PATCHES', (replace(module.PATCHES[0], patched_sha256='0' * 64), *module.PATCHES[1:]))
    with pytest.raises(RuntimeError, match='result_digest_mismatch'):
        module.apply_patch(root)
    assert all((root / name).read_bytes() == value for name, value in originals.items())


def test_two_request_budget_travels_settings_app_scheduler_runner(sample):
    module, _, originals, _ = sample
    namespace = compiled_modules(module, originals)
    settings = namespace['ServerSettings'](run_request_limit=2, run_timeout_seconds=90)
    scheduler = namespace['build'](settings)
    runner = scheduler.make_runner()
    assert scheduler.run_request_limit == runner.run_request_limit == 2
    assert runner.run_timeout_seconds == 90
    assert namespace['build'](namespace['ServerSettings']()).make_runner().run_request_limit == 500


@pytest.mark.parametrize('limit', [0, -1, True, 1.5, '2'])
def test_direct_constructors_cannot_bypass_positive_integer_limit(sample, limit):
    module, _, originals, _ = sample
    namespace = compiled_modules(module, originals)
    for constructor in [namespace['RunScheduler'], namespace['AgentRunRunner']]:
        with pytest.raises(ValueError, match='positive integer'):
            constructor(run_request_limit=limit)
    if limit in [0, -1]:
        with pytest.raises(ValidationError):
            namespace['ServerSettings'](run_request_limit=limit)


def test_runner_passes_two_to_native_usage_limits_without_running_any_model(sample):
    module, _, originals, _ = sample
    namespace = compiled_modules(module, originals)
    namespace['UsageLimits'] = lambda **kwargs: kwargs
    calls = []
    class ModelFreeAgent:
        async def run(self, **kwargs):
            calls.append(kwargs)
            return 'no-model'
    runner = namespace['build'](namespace['ServerSettings'](run_request_limit=2)).make_runner()
    assert asyncio.run(runner._run_agent(ModelFreeAgent())) == 'no-model'
    assert calls == [{'usage_limits': {'request_limit': 2}}]


def test_setup_prepares_source_before_first_package_install(tmp_path, monkeypatch):
    module = tool('wsl_setup')
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    (tmp_path / 'source-receipt.json').write_text('{"filter_revision":2}')
    events = []
    monkeypatch.setattr(module, 'patch_agent_sources', lambda: events.append('patch') or {'patch_id': 'expected'})
    monkeypatch.setattr(module, 'python_path', lambda: tmp_path / 'python')
    monkeypatch.setattr(module, 'command', lambda args, **kwargs: events.append(args))
    result = module.setup_python()
    assert events[0] == 'patch' and len(events) == 3
    assert all('--frozen' in event and '--no-editable' in event for event in events[1:])
    assert result['agent_request_limit_patch'] == {'patch_id': 'expected'}


def test_setup_source_refusal_does_not_start_install(tmp_path, monkeypatch):
    module = tool('wsl_setup')
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    def reject():
        raise RuntimeError('agent_patch_source_unrecognized')
    monkeypatch.setattr(module, 'patch_agent_sources', reject)
    monkeypatch.setattr(module, 'command', lambda *a, **kw: pytest.fail('来源不符禁止安装'))
    with pytest.raises(RuntimeError, match='source_unrecognized'):
        module.setup_python()


def test_preparation_stage_preserves_failed_attempt_and_does_not_replay(tmp_path, monkeypatch):
    module = tool('wsl_setup')
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    calls = []
    def reject():
        calls.append('patch')
        raise RuntimeError('unknown_source')
    monkeypatch.setattr(module, 'patch_agent_sources', reject)
    args = SimpleNamespace(stage='agent-request-limit-patch', verify_only=False)
    with pytest.raises(RuntimeError, match='unknown_source'):
        module.execute_stage(args)
    assert json.loads((tmp_path / 'setup-stages.json').read_text())[args.stage]['status'] == 'failed'
    with pytest.raises(RuntimeError, match='previous_failed_stage_requires_review'):
        module.execute_stage(args)
    assert calls == ['patch']


def test_windows_preparation_entry_is_small_and_does_not_change_existing_budgets(monkeypatch):
    module = tool('wsl')
    monkeypatch.setattr(module, 'linux_path', lambda value: '/project/' + value.name)
    argv, seconds = module.specification('agent-request-limit-patch')
    assert argv == ['/usr/bin/python3', '/project/wsl_setup.py', 'agent-request-limit-patch']
    assert seconds == 30 and module.INSTALL_STAGES['python'] == 7200
    assert module.RUNTIME_BUDGETS['start'] == 900
