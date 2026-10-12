"""Offline regressions for dashboard generation and durable failed receipts.

中文说明：大屏生成失败的回归：失败要留下持久化的失败回执，且不影响后续对话。
隔离方式：SimpleNamespace + MagicMock，离线运行，不连库、不调用模型。
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from app.errors import ConflictError
from app.modules.reports import generation
from app.modules.business_ai import service
from app.modules.business_ai.schemas import DashboardDraftInput


@pytest.mark.parametrize('status', ['missing', 'completed', 'failed', 'running'])
def test_legacy_generation_lookup_with_session(status):
    db = MagicMock()
    ctx = SimpleNamespace(db=db, workspace_id='workspace', user_id='user')
    rows = [] if status == 'missing' else [SimpleNamespace(
        id='generation', request_json={'session_id': 'run', '_request_hash': 'hash'},
        result_json={'title': '设备预警'}, status=status)]
    db.scalars.return_value.all.return_value = rows
    if status in ('failed', 'running'):
        with pytest.raises(ConflictError):
            generation._existing_generation(ctx, SimpleNamespace(session_id='run'), 'screen', 'hash')
    else:
        result = generation._existing_generation(ctx, SimpleNamespace(session_id='run'), 'screen', 'hash')
        assert result == (None if status == 'missing' else {'title': '设备预警', 'reused': True})
    db.scalars.assert_called_once()


def test_failed_receipt_is_not_reopened(monkeypatch):
    db = MagicMock()
    ctx = SimpleNamespace(db=db, workspace_id='workspace', user_id='user')
    payload = DashboardDraftInput(requestKey='stable-request', requirement='设备预警',
                                  templateMode='manual', templateId='lynx-source-template-37')
    receipt = SimpleNamespace(id='receipt', action='dashboard.generate',
        request_hash=service._request_hash(payload), status='failed', resource_id=None,
        invocation_run_id='run', result_json={}, error_message='prior failure')
    db.scalar.return_value = receipt
    monkeypatch.setattr(service, 'require_permission', lambda *args: None)
    monkeypatch.setattr(service, 'list_analytics_sources', lambda *args, **kwargs: [SimpleNamespace(id='local', name='local')])
    monkeypatch.setattr(service, 'list_http_sources', lambda *args, **kwargs: [])
    monkeypatch.setattr(service, '_completed_invocation_receipt', lambda *args: None)
    generate = MagicMock()
    monkeypatch.setattr(service.report_generation, 'generate', generate)
    with pytest.raises(ConflictError):
        service.create_dashboard_draft(ctx, payload, invocation_run_id='run')
    assert receipt.status == 'failed'
    db.commit.assert_not_called()
    generate.assert_not_called()

