"""Effective variable selection uses native converters and scoped database reads."""

import json
from importlib import import_module
from unittest.mock import Mock

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from graphon.variables.input_entities import VariableEntityType
from models.model import App, AppMode, AppModelConfig, Conversation
from models.workflow import Workflow


@pytest.mark.parametrize("mode", [AppMode.CHAT, AppMode.AGENT_CHAT])
@pytest.mark.parametrize("existing", [False, True])
def test_basic_variables_use_native_converter_and_pinned_config(mode, existing):
    module = import_module("services.workbench_input_service")
    app = App(id="app", tenant_id="tenant", mode=mode, app_model_config_id="current")
    conversation = Conversation(app_id="app", app_model_config_id="pinned") if existing else None
    model = Mock(spec=AppModelConfig)
    model.to_dict.return_value = {
        "user_input_form": [{"number": {"variable": "temperature", "label": "Temperature", "required": True}}]
    }
    session = Mock(spec=Session)
    session.scalar.return_value = model
    variables = module.read_workbench_variables(app_model=app, conversation=conversation, session=session)
    assert len(variables) == 1
    assert variables[0].type == VariableEntityType.NUMBER
    assert variables[0].variable == "temperature"
    assert variables[0].required is True
    params = session.scalar.call_args.args[0].compile(dialect=postgresql.dialect()).params.values()
    assert "app" in params
    assert ("pinned" if existing else "current") in params
    session.commit.assert_not_called()


def test_workflow_variables_use_native_start_form_converter():
    module = import_module("services.workbench_input_service")
    app = App(id="app", tenant_id="tenant", mode=AppMode.ADVANCED_CHAT, workflow_id="published")
    workflow = Mock(spec=Workflow)
    workflow.user_input_form.return_value = [
        {"variable": "document", "label": "Document", "type": "file", "required": True}
    ]
    session = Mock(spec=Session)
    session.scalar.return_value = workflow
    variables = module.read_workbench_variables(app_model=app, conversation=None, session=session)
    assert variables[0].type == VariableEntityType.FILE
    assert variables[0].variable == "document"
    params = session.scalar.call_args.args[0].compile(dialect=postgresql.dialect()).params.values()
    assert {"tenant", "app", "published"}.issubset(params)


@pytest.mark.parametrize("mode", [AppMode.CHAT, AppMode.ADVANCED_CHAT])
def test_missing_config_never_becomes_an_empty_variable_allowlist(mode):
    module = import_module("services.workbench_input_service")
    app = App(id="app", tenant_id="tenant", mode=mode, app_model_config_id="missing", workflow_id="missing")
    session = Mock(spec=Session)
    session.scalar.return_value = None
    with pytest.raises(ValueError, match="^Workbench input configuration unavailable$"):
        module.read_workbench_variables(app_model=app, conversation=None, session=session)


def test_foreign_conversation_is_rejected_without_reading_config():
    module = import_module("services.workbench_input_service")
    session = Mock(spec=Session)
    with pytest.raises(ValueError):
        module.read_workbench_variables(
            app_model=App(id="app", mode=AppMode.CHAT),
            conversation=Conversation(app_id="other", app_model_config_id="private"),
            session=session,
        )
    session.scalar.assert_not_called()


def test_real_workflow_graph_normalizes_schema_without_rewriting_stored_graph():
    module = import_module("services.workbench_input_service")
    graph = json.dumps(
        {
            "nodes": [
                {
                    "id": "start",
                    "data": {
                        "type": "start",
                        "variables": [
                            {
                                "variable": "payload",
                                "label": "Payload",
                                "type": VariableEntityType.JSON_OBJECT,
                                "required": True,
                                "json_schema": '{"type":"object"}',
                            }
                        ],
                    },
                }
            ]
        }
    )
    workflow = Workflow(id="published", app_id="app", tenant_id="tenant", graph=graph)
    session = Mock(spec=Session)
    session.scalar.return_value = workflow
    variables = module.read_workbench_variables(
        app_model=App(id="app", tenant_id="tenant", mode=AppMode.ADVANCED_CHAT, workflow_id="published"),
        conversation=None,
        session=session,
    )
    assert variables[0].json_schema == {"type": "object"}
    assert workflow.graph == graph


def test_malformed_form_has_opaque_failure_instead_of_partial_validation():
    module = import_module("services.workbench_input_service")
    record = Mock(spec=AppModelConfig)
    record.to_dict.return_value = {"user_input_form": [{"number": {"label": "private"}}]}
    session = Mock(spec=Session)
    session.scalar.return_value = record
    with pytest.raises(ValueError, match="^Workbench input configuration unavailable$"):
        module.read_workbench_variables(
            app_model=App(id="app", mode=AppMode.CHAT, app_model_config_id="current"),
            conversation=None,
            session=session,
        )
