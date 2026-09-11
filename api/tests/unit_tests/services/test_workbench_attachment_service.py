"""Workbench attachment preflight preserves native access scope and every selected file."""

from importlib import import_module
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.app.entities.app_invoke_entities import InvokeFrom, UserFrom
from core.app.file_access import FileAccessScope, bind_file_access_scope, get_current_file_access_scope
from graphon.file import FileType
from models import Account


@pytest.fixture
def attachment():
    module = import_module("services.workbench_attachment_service")
    user = Account(name="User", email="user@example.test")
    user.id = "actor"
    return module, user


def test_each_selected_file_uses_native_account_scope_without_inherited_grants(attachment, monkeypatch):
    module, user = attachment
    mappings = [
        {"transfer_method": "local_file", "upload_file_id": "first"},
        {"transfer_method": "remote_url", "url": "https://example.test/file"},
    ]
    calls = []

    def build(**kwargs):
        calls.append(kwargs)
        scope = kwargs["access_controller"].current_scope()
        assert scope.tenant_id == "workspace"
        assert scope.user_id == "actor"
        assert scope.user_from == UserFrom.ACCOUNT
        assert scope.invoke_from == InvokeFrom.EXPLORE
        assert scope.granted_upload_file_ids == frozenset()

    monkeypatch.setattr(module.file_factory, "build_from_mapping", build)
    outer = FileAccessScope("other", "other", UserFrom.END_USER, InvokeFrom.WEB_APP, frozenset({"private"}))
    with bind_file_access_scope(outer):
        module.require_workbench_attachments(tenant_id="workspace", user=user, mappings=mappings, config=None)
        assert get_current_file_access_scope() is outer
    assert [call["mapping"] for call in calls] == mappings
    assert all(call["tenant_id"] == "workspace" for call in calls)


def test_invalid_mapping_is_not_silently_dropped_and_scope_is_restored(attachment, monkeypatch):
    module, user = attachment
    build = Mock(side_effect=ValueError("private file details"))
    monkeypatch.setattr(module.file_factory, "build_from_mapping", build)
    before = get_current_file_access_scope()
    with pytest.raises(ValueError, match="^Workbench attachment unavailable$"):
        module.require_workbench_attachments(tenant_id="workspace", user=user, mappings=[{}], config=None)
    build.assert_called_once()
    assert get_current_file_access_scope() is before


def test_empty_attachment_list_does_not_resolve_files(attachment, monkeypatch):
    module, user = attachment
    build = Mock()
    monkeypatch.setattr(module.file_factory, "build_from_mapping", build)
    module.require_workbench_attachments(tenant_id="workspace", user=user, mappings=[], config=None)
    build.assert_not_called()


@pytest.mark.parametrize("tenant_id", ["", None])
def test_missing_tenant_stops_before_file_resolution(attachment, monkeypatch, tenant_id):
    module, user = attachment
    build = Mock()
    monkeypatch.setattr(module.file_factory, "build_from_mapping", build)
    with pytest.raises(ValueError):
        module.require_workbench_attachments(tenant_id=tenant_id, user=user, mappings=[{}], config=None)
    build.assert_not_called()


@pytest.mark.parametrize("images_only", [False, True])
def test_native_batch_count_limits_are_preserved(attachment, monkeypatch, images_only):
    module, user = attachment
    config = SimpleNamespace(
        number_limits=0 if images_only else 1,
        image_config=SimpleNamespace(number_limits=1) if images_only else None,
    )
    build = Mock(return_value=SimpleNamespace(type=FileType.IMAGE))
    monkeypatch.setattr(module.file_factory, "build_from_mapping", build)
    with pytest.raises(ValueError, match="^Workbench attachment unavailable$"):
        module.require_workbench_attachments(tenant_id="workspace", user=user, mappings=[{}, {}], config=config)
