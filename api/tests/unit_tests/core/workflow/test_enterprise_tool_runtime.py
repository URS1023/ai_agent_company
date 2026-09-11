"""Native composition tests using the real tool/runtime/factory classes.

These require Dify's complete backend test environment, unlike the pure policy
tests. External tool resolution and invocation are mocked; no database or daemon
is contacted. Do not substitute extracted AST or fabricated native modules.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, sentinel

import pytest
from pydantic import ValidationError

from configs.feature import WorkflowConfig
from core.app.entities.app_invoke_entities import InvokeFrom, UserFrom
from core.tools.__base.tool_runtime import ToolRuntime
from core.tools.entities.common_entities import I18nObject
from core.tools.entities.tool_entities import ToolEntity, ToolIdentity
from core.tools.plugin_tool.tool import PluginTool
from core.workflow import node_factory, node_runtime
from core.workflow.enterprise_execution import ManagedExecutionConfigurationError, ManagedToolRegistry
from core.workflow.system_variables import SystemVariableKey, get_system_text, system_variable_selector
from graphon.nodes.tool.entities import ToolNodeData, ToolProviderType
from graphon.nodes.tool.exc import ToolRuntimeResolutionError
from graphon.runtime import VariablePool
from tests.workflow_test_utils import build_test_graph_init_params, build_test_run_context

WORKFLOW = "00000000-0000-4000-8000-000000000001"
RUN = "00000000-0000-4000-8000-000000000002"
EXECUTION = "00000000-0000-4000-8000-000000000003"
CREDENTIAL = "00000000-0000-4000-8000-000000000004"
PROVIDER = "enterprise/enterprise_device_assessment/enterprise_device"


def policy_json() -> str:
    return json.dumps(
        [
            {
                "workspace_id": "workspace-1",
                "app_id": "app-1",
                "workflow_id": WORKFLOW,
                "provider_id": PROVIDER,
                "tool_name": "evaluate_device",
                "credential_id": CREDENTIAL,
                "node_id": "node-1",
            }
        ]
    )


def node_data() -> ToolNodeData:
    return ToolNodeData(
        title="Managed assessment",
        provider_id=PROVIDER,
        provider_type=ToolProviderType.BUILT_IN,
        provider_name=PROVIDER,
        tool_name="evaluate_device",
        tool_label="Assessment",
        credential_id=CREDENTIAL,
        tool_configurations={},
        tool_parameters={},
    )


def context(*, invoke_from: InvokeFrom = InvokeFrom.SERVICE_API):
    return build_test_run_context(
        tenant_id="workspace-1",
        app_id="app-1",
        user_id="end-user-1",
        user_from=UserFrom.END_USER,
        invoke_from=invoke_from,
        extra_context={"enterprise_context": {"app_id": "forged", "native_run_id": "forged"}},
    )


@pytest.fixture
def plugin_tool(monkeypatch: pytest.MonkeyPatch) -> PluginTool:
    tool = PluginTool(
        entity=ToolEntity(
            identity=ToolIdentity(
                author="enterprise",
                name="evaluate_device",
                label=I18nObject(en_US="Assessment"),
                provider=PROVIDER,
            )
        ),
        runtime=ToolRuntime(
            tenant_id="workspace-1",
            credentials={"provider_key": "fixture-credential", "nested": {"value": "original"}},
            runtime_parameters={"enterprise_context": {"app_id": "forged-runtime"}},
        ),
        tenant_id="workspace-1",
        icon="icon.svg",
        plugin_unique_identifier="enterprise/enterprise_device_assessment:0.1.0",
    )
    monkeypatch.setattr(node_runtime.ToolManager, "get_workflow_tool_runtime", Mock(return_value=tool))
    return tool


def runtime(*, policy: bool = True, getter=lambda: RUN, invoke_from: InvokeFrom = InvokeFrom.SERVICE_API):
    session_maker = MagicMock()
    session_maker.begin.return_value.__enter__.return_value = sentinel.session
    return node_runtime.DifyToolNodeRuntime(
        context(invoke_from=invoke_from),
        session_maker=session_maker,
        workflow_id=WORKFLOW,
        workflow_execution_id_getter=getter,
        managed_tools=ManagedToolRegistry.from_json(policy_json()) if policy else ManagedToolRegistry(),
    )


def test_opt_in_metadata_is_private_until_invoke_and_never_mutates_the_original_tool(
    monkeypatch: pytest.MonkeyPatch,
    plugin_tool: PluginTool,
) -> None:
    service = runtime()
    handle = service.get_runtime(
        node_id="node-1", node_data=node_data(), variable_pool=None, node_execution_id=EXECUTION
    )
    assert handle.raw.tool is plugin_tool
    assert "__enterprise_execution" not in plugin_tool.runtime.credentials
    invoked: list[PluginTool] = []

    def capture(**kwargs):
        invoked.append(kwargs["tool"])
        assert kwargs["tool_parameters"]["enterprise_context"]["app_id"] == "forged-parameter"
        return iter(())

    monkeypatch.setattr(node_runtime.ToolEngine, "generic_invoke", capture)
    monkeypatch.setattr(
        node_runtime.ToolFileMessageTransformer, "transform_tool_invoke_messages", lambda **kw: kw["messages"]
    )
    for _ in range(2):
        assert (
            list(
                service.invoke(
                    tool_runtime=handle,
                    tool_parameters={"enterprise_context": {"app_id": "forged-parameter"}},
                    workflow_call_depth=0,
                    provider_name=PROVIDER,
                )
            )
            == []
        )
    assert invoked[0] is not invoked[1]
    assert invoked[0] is not plugin_tool
    assert invoked[0].runtime is not plugin_tool.runtime
    assert invoked[0].runtime.credentials["__enterprise_execution"] == {
        "workspace_id": "workspace-1",
        "app_id": "app-1",
        "workflow_id": WORKFLOW,
        "native_run_id": RUN,
        "node_id": "node-1",
        "node_execution_id": EXECUTION,
        "invoke_from": "service-api",
    }
    assert invoked[0].runtime.credentials["provider_key"] == "fixture-credential"
    invoked[0].runtime.credentials["nested"]["value"] = "changed"
    assert plugin_tool.runtime.credentials["nested"]["value"] == "original"
    assert "__enterprise_execution" not in plugin_tool.runtime.credentials
    assert invoked[1].runtime.credentials["nested"]["value"] == "original"


def test_disabled_feature_preserves_tool_identity_and_never_reads_the_getter(
    monkeypatch: pytest.MonkeyPatch,
    plugin_tool: PluginTool,
) -> None:
    getter = Mock(side_effect=AssertionError("Unexpected getter"))
    service = runtime(policy=False, getter=getter)
    handle = service.get_runtime(node_id="node-1", node_data=node_data(), variable_pool=None)
    invoke = Mock(return_value=iter(()))
    monkeypatch.setattr(node_runtime.ToolEngine, "generic_invoke", invoke)
    monkeypatch.setattr(
        node_runtime.ToolFileMessageTransformer, "transform_tool_invoke_messages", lambda **kw: kw["messages"]
    )
    list(service.invoke(tool_runtime=handle, tool_parameters={}, workflow_call_depth=0, provider_name=PROVIDER))
    assert invoke.call_args.kwargs["tool"] is plugin_tool
    getter.assert_not_called()


@pytest.mark.parametrize("field", ["tenant", "runtime_tenant", "provider", "tool", "reserved"])
def test_registered_tool_rejects_resolved_scope_mismatch_or_reserved_credentials(
    plugin_tool: PluginTool,
    field: str,
) -> None:
    if field == "tenant":
        plugin_tool.tenant_id = "other"
    elif field == "runtime_tenant":
        plugin_tool.runtime.tenant_id = "other"
    elif field == "provider":
        plugin_tool.entity.identity.provider = "other/provider/name"
    elif field == "tool":
        plugin_tool.entity.identity.name = "other"
    else:
        plugin_tool.runtime.credentials["__enterprise_execution"] = {"app_id": "forged"}
    with pytest.raises(ToolRuntimeResolutionError, match="enterprise_execution_identity_invalid"):
        runtime().get_runtime(node_id="node-1", node_data=node_data(), variable_pool=None, node_execution_id=EXECUTION)


@pytest.mark.parametrize("missing", ["run", "node_execution"])
def test_registered_tool_missing_execution_identity_fails_closed(plugin_tool: PluginTool, missing: str) -> None:
    with pytest.raises(ToolRuntimeResolutionError, match="enterprise_execution_identity_invalid"):
        runtime(getter=lambda: None if missing == "run" else RUN).get_runtime(
            node_id="node-1",
            node_data=node_data(),
            variable_pool=None,
            node_execution_id=None if missing == "node_execution" else EXECUTION,
        )


@pytest.mark.parametrize("invoke_from", [InvokeFrom.DEBUGGER, InvokeFrom.WEB_APP, InvokeFrom.OPENAPI])
def test_registered_tool_is_not_attested_from_other_native_entry_points(
    plugin_tool: PluginTool,
    invoke_from: InvokeFrom,
) -> None:
    with pytest.raises(ToolRuntimeResolutionError, match="enterprise_execution_identity_invalid"):
        runtime(invoke_from=invoke_from).get_runtime(
            node_id="node-1",
            node_data=node_data(),
            variable_pool=None,
            node_execution_id=EXECUTION,
        )


def test_unregistered_plugin_is_unchanged_even_when_other_registrations_exist(plugin_tool: PluginTool) -> None:
    getter = Mock(side_effect=AssertionError("Unregistered getter"))
    handle = runtime(getter=getter).get_runtime(node_id="node-other", node_data=node_data(), variable_pool=None)
    assert handle.raw.tool is plugin_tool
    assert handle.raw.enterprise_execution is None
    getter.assert_not_called()


def test_server_configuration_is_default_off_and_reads_only_the_explicit_environment_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ENTERPRISE_MANAGED_TOOLS_JSON", raising=False)
    assert WorkflowConfig(_env_file=None).ENTERPRISE_MANAGED_TOOLS_JSON == ""
    monkeypatch.setenv("ENTERPRISE_MANAGED_TOOLS_JSON", policy_json())
    assert policy_json() == WorkflowConfig(_env_file=None).ENTERPRISE_MANAGED_TOOLS_JSON
    with pytest.raises(ValidationError):
        WorkflowConfig(_env_file=None, ENTERPRISE_MANAGED_TOOLS_JSON=" " * 65537)


def test_non_plugin_tool_with_matching_node_configuration_is_not_modified(monkeypatch: pytest.MonkeyPatch) -> None:
    ordinary = sentinel.ordinary_tool
    monkeypatch.setattr(node_runtime.ToolManager, "get_workflow_tool_runtime", Mock(return_value=ordinary))
    getter = Mock(side_effect=AssertionError("Should not request metadata"))
    handle = runtime(getter=getter).get_runtime(node_id="node-1", node_data=node_data(), variable_pool=None)
    assert handle.raw.tool is ordinary
    getter.assert_not_called()


def test_factory_binds_the_live_variable_pool_not_the_empty_parameter_pool(
    monkeypatch: pytest.MonkeyPatch,
    plugin_tool: PluginTool,
) -> None:
    monkeypatch.setattr(node_factory.dify_config, "ENTERPRISE_MANAGED_TOOLS_JSON", policy_json())
    monkeypatch.setattr(
        node_factory, "build_dify_model_access", Mock(return_value=(sentinel.credentials, sentinel.model))
    )
    pool = VariablePool()
    state = SimpleNamespace(variable_pool=pool)
    factory = node_factory.DifyNodeFactory(
        graph_init_params=build_test_graph_init_params(
            workflow_id=WORKFLOW,
            tenant_id="workspace-1",
            app_id="app-1",
            invoke_from=InvokeFrom.SERVICE_API,
        ),
        graph_runtime_state=state,
    )
    assert get_system_text(pool, SystemVariableKey.WORKFLOW_EXECUTION_ID) is None
    monkeypatch.setattr(node_factory.dify_config, "ENTERPRISE_MANAGED_TOOLS_JSON", "")
    pool.add(system_variable_selector(SystemVariableKey.WORKFLOW_EXECUTION_ID), RUN)
    handle = factory._tool_runtime.get_runtime(
        node_id="node-1",
        node_data=node_data(),
        variable_pool=None,
        node_execution_id=EXECUTION,
    )
    assert handle.raw.enterprise_execution["native_run_id"] == RUN
    assert handle.raw.enterprise_execution["workflow_id"] == WORKFLOW


def test_invalid_enabled_configuration_fails_before_a_managed_tool_can_execute(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(node_factory.dify_config, "ENTERPRISE_MANAGED_TOOLS_JSON", "invalid-json")
    with pytest.raises(ManagedExecutionConfigurationError, match="enterprise_managed_tools_configuration_invalid"):
        node_factory.DifyNodeFactory(
            graph_init_params=build_test_graph_init_params(),
            graph_runtime_state=SimpleNamespace(variable_pool=VariablePool()),
        )


def test_registration_settings_default_off_and_hide_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import SecretStr

    from configs.feature import WorkflowConfig

    monkeypatch.delenv("ENTERPRISE_REGISTRATION_ORIGIN", raising=False)
    monkeypatch.delenv("ENTERPRISE_NATIVE_REGISTRATION_TOKEN", raising=False)
    config = WorkflowConfig(_env_file=None)
    assert config.ENTERPRISE_REGISTRATION_ORIGIN == ""
    assert config.ENTERPRISE_NATIVE_REGISTRATION_TOKEN is None
    configured = WorkflowConfig(_env_file=None, ENTERPRISE_NATIVE_REGISTRATION_TOKEN=SecretStr("private-token"))
    assert "ENTERPRISE_NATIVE_REGISTRATION_TOKEN" not in configured.model_dump()
    assert "private-token" not in repr(configured)


@pytest.mark.parametrize("enabled", [False, True])
def test_factory_composes_dynamic_registration_only_when_configured(
    monkeypatch: pytest.MonkeyPatch,
    plugin_tool: PluginTool,
    enabled: bool,
) -> None:
    from pydantic import SecretStr

    monkeypatch.setattr(node_factory.dify_config, "ENTERPRISE_MANAGED_TOOLS_JSON", "")
    monkeypatch.setattr(
        node_factory.dify_config, "ENTERPRISE_REGISTRATION_ORIGIN", "https://enterprise.test" if enabled else ""
    )
    monkeypatch.setattr(
        node_factory.dify_config, "ENTERPRISE_NATIVE_REGISTRATION_TOKEN", SecretStr("a" * 40) if enabled else None
    )
    client = Mock()
    client.fetch.return_value = ManagedToolRegistry.from_json(policy_json())
    factory_client = Mock(return_value=client)
    monkeypatch.setattr(node_factory, "NativeRegistrationClient", factory_client)
    monkeypatch.setattr(
        node_factory, "build_dify_model_access", Mock(return_value=(sentinel.credentials, sentinel.model))
    )
    pool = VariablePool()
    pool.add(system_variable_selector(SystemVariableKey.WORKFLOW_EXECUTION_ID), RUN)
    factory = node_factory.DifyNodeFactory(
        graph_init_params=build_test_graph_init_params(
            workflow_id=WORKFLOW, tenant_id="workspace-1", app_id="app-1", invoke_from=InvokeFrom.SERVICE_API
        ),
        graph_runtime_state=SimpleNamespace(variable_pool=pool),
    )
    handle = factory._tool_runtime.get_runtime(
        node_id="node-1", node_data=node_data(), variable_pool=None, node_execution_id=EXECUTION
    )
    if enabled:
        client.fetch.assert_called_once_with("workspace-1", "app-1", WORKFLOW)
        assert handle.raw.enterprise_execution["native_run_id"] == RUN
    else:
        factory_client.assert_not_called()
        assert handle.raw.enterprise_execution is None
