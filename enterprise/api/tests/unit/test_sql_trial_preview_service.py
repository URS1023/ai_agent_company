import asyncio
from dataclasses import replace

import pytest
from test_dashboard_refresh_service import principal
from test_sql_trial_history import fixture

from enterprise_platform.application.errors import AccessDenied, Conflict


def preview(service, actor=None):
    return asyncio.run(service.preview_sql_trial(actor or principal(), "dashboard-1", "draft-1", "trial-1"))


def test_preview_uses_authorized_saved_capture_without_executing_or_publishing():
    service, parts, store, evidence = fixture()
    result = preview(service)
    assert result.trial_id == "trial-1"
    assert result.slot.slot_id == "a"
    assert result.status == "preview_only"
    assert result.semantic_status == "not_reviewed"
    parts[5].execute.assert_not_awaited()
    store.create.assert_not_called()
    parts[1].save_bindings.assert_not_called()
    parts[1].commit.assert_not_called()


def test_readonly_actor_never_loads_evidence_for_preview():
    service, parts, store, evidence = fixture()
    with pytest.raises(AccessDenied):
        preview(service, principal().model_copy(update={"workspace_role": "normal"}))
    store.get.assert_not_called()


def test_revocation_during_preview_prevents_returning_a_compatible_status():
    service, parts, store, evidence = fixture()
    permit = parts[4].authorize.return_value
    parts[4].authorize.side_effect = [permit, permit, AccessDenied()]
    with pytest.raises(AccessDenied):
        preview(service)
    parts[5].execute.assert_not_awaited()


def test_changed_dashboard_rejects_preview():
    service, parts, store, evidence = fixture()
    parts[1].get.return_value = replace(parts[1].get.return_value, revision=2)
    with pytest.raises(Conflict):
        preview(service)


def test_foreign_trial_identity_is_rejected_before_source_authorization():
    service, parts, store, evidence = fixture()
    parts[4].authorize.reset_mock()
    store.get.return_value = evidence.model_copy(update={"trial_id": "other"})
    with pytest.raises(AccessDenied):
        preview(service)
    parts[4].authorize.assert_not_called()
