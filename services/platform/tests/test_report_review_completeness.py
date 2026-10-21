"""历史错误复核不能绕过正式报告发布；只用纯对象，不连接数据库。"""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from agent_platform.core.errors import DomainError
from agent_platform.modules.reports.inspection_source import InspectionSource, digest


def source(result, review_outcome):
    standard = {'metrics': [{'id': 'weight'}, {'id': 'protein'}]}
    result = {**result, 'run_id': 'run'}
    inspection = SimpleNamespace(revision=4, template_revision=1, standard_snapshot=standard, result=result)
    run = SimpleNamespace(id='run', task_id='task', result=result)
    reviews = [{'id': 'legacy-review', 'outcome': review_outcome, 'event_sequence': 2}]
    snapshot = {'projection_version': 1, 'inspection_id': 'inspection', 'run_id': 'run',
        'task_id': 'task', 'result_hash': digest(result), 'standard_revision': 1,
        'standard_snapshot': deepcopy(standard), 'inspection_revision': 4,
        'review_event_sequence': 2, 'review_snapshot': deepcopy(reviews)}
    service = InspectionSource(None, None)
    service.load = lambda *args: (inspection, run)
    service.reviews = lambda *args: deepcopy(reviews)
    return service, snapshot, run


@pytest.mark.parametrize('result', [
    {'outcome': 'failed', 'missing_samples': 1, 'samples': []},
    {'outcome': 'failed', 'missing_samples': 0, 'samples': [{'outcome': 'failed'}, {'outcome': 'incomplete'}]},
    {'outcome': 'failed', 'missing_samples': 0, 'samples': [{'outcome': 'failed', 'metrics': [
        {'outcome': 'failed'}, {'outcome': 'incomplete'}]}]},
])
def test_legacy_passed_review_with_missing_evidence_can_read_but_cannot_publish(result):
    service, snapshot, run = source(result, 'passed')
    original = deepcopy(run.result)
    assert service.authorize(snapshot) is run
    with pytest.raises(DomainError) as error:
        service.authorize(snapshot, current=True)
    assert error.value.code == 'missing_evidence'
    assert run.result == original


@pytest.mark.parametrize('result,outcome', [
    ({'outcome': 'failed', 'missing_samples': 1, 'samples': []}, 'failed'),
    ({'outcome': 'failed', 'missing_samples': 0, 'samples': [{'outcome': 'failed', 'metrics': [
        {'outcome': 'failed'}, {'outcome': 'passed'}]}]}, 'passed'),
])
def test_failed_report_and_complete_manual_override_remain_publishable(result, outcome):
    service, snapshot, run = source(result, outcome)
    assert service.authorize(snapshot, current=True) is run
