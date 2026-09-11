"""Effective attachment config follows installed generation rather than debugger defaults."""

from importlib import import_module
from unittest.mock import Mock

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from models.model import App, AppMode, AppModelConfig, Conversation


@pytest.fixture
def setup():
    module = import_module("services.workbench_attachment_service")
    app = App(id="app", tenant_id="tenant", mode=AppMode.CHAT, app_model_config_id="current")
    return module, app, Mock(spec=Session)


@pytest.mark.parametrize("existing", [False, True])
def test_chat_reads_current_or_conversation_pinned_config(setup, monkeypatch, existing):
    module, app, session = setup
    record = Mock(spec=AppModelConfig)
    record.to_dict.return_value = {"file_upload": {"enabled": False}}
    session.scalar.return_value = record
    convert = Mock(return_value=None)
    monkeypatch.setattr(module.FileUploadConfigManager, "convert", convert)
    conversation = Conversation(app_id="app", app_model_config_id="pinned") if existing else None
    assert module.read_workbench_attachment_config(app_model=app, conversation=conversation, session=session) is None
    params = session.scalar.call_args.args[0].compile(dialect=postgresql.dialect()).params.values()
    assert "app" in params
    assert ("pinned" if existing else "current") in params
    convert.assert_called_once_with(record.to_dict.return_value)


def test_missing_config_fails_without_conversion(setup, monkeypatch):
    module, app, session = setup
    session.scalar.return_value = None
    convert = Mock()
    monkeypatch.setattr(module.FileUploadConfigManager, "convert", convert)
    with pytest.raises(ValueError, match="^Workbench attachment configuration unavailable$"):
        module.read_workbench_attachment_config(app_model=app, conversation=None, session=session)
    convert.assert_not_called()


def test_foreign_conversation_is_rejected_before_lookup(setup):
    module, app, session = setup
    with pytest.raises(ValueError):
        module.read_workbench_attachment_config(
            app_model=app, conversation=Conversation(app_id="other", app_model_config_id="private"), session=session
        )
    session.scalar.assert_not_called()


def test_workflow_uses_published_features_without_vision_override(setup, monkeypatch):
    module, app, session = setup
    app.mode = AppMode.ADVANCED_CHAT
    app.workflow_id = "published"
    workflow = Mock()
    workflow.features_dict = {"file_upload": {"enabled": False}}
    session.scalar.return_value = workflow
    convert = Mock(return_value=None)
    monkeypatch.setattr(module.FileUploadConfigManager, "convert", convert)
    module.read_workbench_attachment_config(app_model=app, conversation=None, session=session)
    params = session.scalar.call_args.args[0].compile(dialect=postgresql.dialect()).params.values()
    assert "tenant" in params
    assert "app" in params
    assert "published" in params
    convert.assert_called_once_with(workflow.features_dict, is_vision=False)


def test_unpublished_workflow_does_not_fall_back_to_chat_config(setup):
    module, app, session = setup
    app.mode = AppMode.ADVANCED_CHAT
    app.workflow_id = None
    with pytest.raises(ValueError):
        module.read_workbench_attachment_config(app_model=app, conversation=None, session=session)
    session.scalar.assert_not_called()


def test_config_conversion_cannot_mutate_persisted_snapshot(setup, monkeypatch):
    module, app, session = setup
    record = Mock(spec=AppModelConfig)
    snapshot = {"file_upload": {"enabled": True}}
    record.to_dict.return_value = snapshot
    session.scalar.return_value = record

    def convert(config):
        config["file_upload"]["image_config"] = {"number_limits": 1}

    monkeypatch.setattr(module.FileUploadConfigManager, "convert", convert)
    module.read_workbench_attachment_config(app_model=app, conversation=None, session=session)
    assert snapshot == {"file_upload": {"enabled": True}}


@pytest.mark.parametrize("mode", [AppMode.COMPLETION, AppMode.WORKFLOW])
def test_nonchat_app_is_rejected_before_any_lookup(setup, mode):
    module, app, session = setup
    app.mode = mode
    with pytest.raises(ValueError):
        module.read_workbench_attachment_config(app_model=app, conversation=None, session=session)
    session.scalar.assert_not_called()
