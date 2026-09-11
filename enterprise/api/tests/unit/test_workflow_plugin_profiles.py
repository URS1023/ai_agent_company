import asyncio

import httpx
import pytest
from pydantic import SecretStr

from enterprise_platform.adapters.workflow_plugin_client_factory import ProfiledWorkflowPluginClientFactory
from enterprise_platform.application.contracts import Principal
from enterprise_platform.application.workflow_plugin_credentials import PluginCredentialRejected
from enterprise_platform.application.workflow_plugin_profiles import (
    PluginCredentialProfile,
    WorkflowPluginProfileRegistry,
    derive_execution_key,
)

WS = "f19ff571-2297-43fe-8c30-e7e103da664e"
APP = "a595ef4b-2a37-4531-93e6-e85a8691338f"
OTHER = "d9e7c318-1e4b-4678-a5b4-07d2b1b4a8d1"
MASTER = "private-master-012345678901234567890123456789"


def profile(**changes):
    return PluginCredentialProfile(
        **(
            dict(
                workspace_id=WS,
                config_ref="profile",
                config_revision=1,
                origin="https://enterprise.internal",
                expected_plugin_unique_identifier="plugin@v1",
                master_key_id="master-v1",
                master_secret=SecretStr(MASTER),
            )
            | changes
        )
    )


def test_deterministic_per_app_keys_and_no_master_serialization():
    first = derive_execution_key(profile(), app_id=APP, node_ids=frozenset({"assessment"}))
    again = derive_execution_key(profile(), app_id=APP, node_ids=frozenset({"assessment"}))
    other = derive_execution_key(profile(), app_id=OTHER, node_ids=frozenset({"assessment"}))
    assert first == again and first.key_id != other.key_id and first.secret != other.secret
    assert len(first.secret.get_secret_value()) == 64 and len(first.key_id) <= 128
    assert (first.workspace_id, first.app_id, first.node_ids) == (WS, APP, frozenset({"assessment"}))
    assert MASTER not in repr(profile()) + profile().model_dump_json() + repr(first)


def test_registry_exact_scope_versions_and_master_redefinitions():
    registry = WorkflowPluginProfileRegistry((profile(), profile(config_revision=2)))
    assert registry.resolve_profile(WS, configuration_ref="profile", configuration_revision=2).config_revision == 2
    for workspace, ref, revision in [(OTHER, "profile", 1), (WS, "missing", 1), (WS, "profile", 3)]:
        with pytest.raises(PluginCredentialRejected):
            registry.resolve_profile(workspace, configuration_ref=ref, configuration_revision=revision)
    with pytest.raises(ValueError):
        WorkflowPluginProfileRegistry((profile(), profile()))
    with pytest.raises(ValueError):
        WorkflowPluginProfileRegistry(
            (profile(), profile(config_revision=2, master_secret=SecretStr("different-master-secret-0123456789012345")))
        )


def test_factory_resolution_has_no_io_and_filters_exact_config():
    def fail(request):
        raise AssertionError("No HTTP during configuration resolution")

    factory = ProfiledWorkflowPluginClientFactory(
        base_url="https://native.internal/console/api", profiles=(profile(),), transport=httpx.MockTransport(fail)
    )
    principal = Principal(workspace_id=WS, actor_id="actor", workspace_role="owner", display_name="Owner")
    client = asyncio.run(factory.resolve(principal, app_id=APP, configuration_ref="profile", configuration_revision=1))
    assert callable(client.prepare)
    with pytest.raises(PluginCredentialRejected):
        asyncio.run(factory.resolve(principal, app_id=APP, configuration_ref="profile", configuration_revision=2))


@pytest.mark.parametrize(
    "options",
    [
        {"base_url": "https://user:secret@native.internal/console/api"},
        {"base_url": "https://native.internal/v1"},
        {"timeout_seconds": 0},
        {"timeout_seconds": True},
        {"max_response_bytes": 0},
    ],
)
def test_factory_rejects_invalid_transport_configuration_at_construction(options):
    with pytest.raises(ValueError):
        ProfiledWorkflowPluginClientFactory(
            **(dict(base_url="https://native.internal/console/api", profiles=()) | options)
        )


@pytest.mark.parametrize(
    "change",
    [
        {"workspace_id": OTHER},
        {"config_ref": "other"},
        {"config_revision": 2},
        {"master_key_id": "master-v2"},
        {"master_secret": SecretStr("other-private-master-0123456789012345678901")},
    ],
)
def test_all_context_fields_and_master_material_separate_keys(change):
    original = derive_execution_key(profile(), app_id=APP, node_ids=frozenset({"assessment"}))
    changed = derive_execution_key(profile(**change), app_id=APP, node_ids=frozenset({"assessment"}))
    assert original.secret != changed.secret and original.key_id != changed.key_id


def test_public_key_digest_is_not_signing_secret_and_matches_plugin_configuration():
    from enterprise_platform.application.workflow_plugin_profiles import derive_plugin_configuration

    key = derive_execution_key(profile(), app_id=APP, node_ids=frozenset({"assessment"}))
    config = derive_plugin_configuration(profile(), app_id=APP)
    assert (config.key_id, config.signing_secret) == (key.key_id, key.secret)
    assert key.key_id[3:] != key.secret.get_secret_value()
    assert set(key.secret.get_secret_value()) <= set("0123456789abcdef")


def test_registry_copies_input_and_resolved_secret_material():
    original = profile()
    registry = WorkflowPluginProfileRegistry((original,))
    original.master_secret._secret_value = "changed-secret-01234567890123456789012345"
    resolved = registry.resolve_profile(WS, configuration_ref="profile", configuration_revision=1)
    assert resolved.master_secret.get_secret_value() == MASTER
    resolved.master_secret._secret_value = "another-secret-01234567890123456789012345"
    assert (
        registry.resolve_profile(
            WS, configuration_ref="profile", configuration_revision=1
        ).master_secret.get_secret_value()
        == MASTER
    )


@pytest.mark.parametrize(
    "change",
    [
        {"workspace_id": "bad"},
        {"workspace_id": "00000000-0000-0000-0000-000000000000"},
        {"config_revision": True},
        {"config_revision": 0},
        {"master_secret": SecretStr("short")},
        {"master_secret": SecretStr("é" * 32)},
        {"origin": "http://enterprise.internal"},
        {"origin": "https://enterprise.internal/path"},
        {"allow_insecure_http": 1},
        {"expected_plugin_unique_identifier": ""},
        {"master_key_id": "with space"},
    ],
)
def test_profile_validation(change):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        profile(**change)


def test_explicit_private_http_opt_in_and_empty_registry():
    assert profile(origin="http://enterprise.internal", allow_insecure_http=True).allow_insecure_http
    registry = WorkflowPluginProfileRegistry(())
    with pytest.raises(PluginCredentialRejected):
        registry.resolve_profile(WS, configuration_ref="profile", configuration_revision=1)


@pytest.mark.parametrize("revision", [True, "1", 0])
def test_registry_revision_is_strict(revision):
    with pytest.raises(PluginCredentialRejected):
        WorkflowPluginProfileRegistry((profile(),)).resolve_profile(
            WS, configuration_ref="profile", configuration_revision=revision
        )


def test_client_cannot_be_reused_for_different_app_or_workspace():
    from enterprise_platform.application.workflow_setup_execution import NativeSetupSession

    calls = []

    def handler(request):
        calls.append(request)
        raise AssertionError("No native call for mismatched scoped client")

    factory = ProfiledWorkflowPluginClientFactory(
        base_url="https://native.internal/console/api", profiles=(profile(),), transport=httpx.MockTransport(handler)
    )
    principal = Principal(workspace_id=WS, actor_id="actor", workspace_role="owner", display_name="Owner")
    client = asyncio.run(factory.resolve(principal, app_id=APP, configuration_ref="profile", configuration_revision=1))
    session = NativeSetupSession(None, None, None)
    with pytest.raises(PluginCredentialRejected):
        asyncio.run(client.prepare(principal, session, app_id=OTHER, operation_id=OTHER))
    with pytest.raises(PluginCredentialRejected):
        asyncio.run(
            client.prepare(
                principal.model_copy(update={"workspace_id": OTHER}), session, app_id=APP, operation_id=OTHER
            )
        )
    assert calls == []


def test_master_key_reuse_is_allowed_only_with_matching_material():
    registry = WorkflowPluginProfileRegistry((profile(), profile(workspace_id=OTHER)))
    first = registry.resolve_profile(WS, configuration_ref="profile", configuration_revision=1)
    second = registry.resolve_profile(OTHER, configuration_ref="profile", configuration_revision=1)
    assert first.master_secret == second.master_secret
    assert (
        derive_execution_key(first, app_id=APP, node_ids=frozenset({"node"})).secret
        != derive_execution_key(second, app_id=APP, node_ids=frozenset({"node"})).secret
    )


def test_factory_prepares_with_derived_key_and_exact_app_not_master_material():
    import json

    from enterprise_platform.application.workflow_setup_execution import NativeSetupSession

    provider = "enterprise/enterprise_device_assessment/enterprise_device"
    operation = "ba82e084-6621-44c5-bcdb-31a5baf9a8be"
    credential = "e1f341a5-5739-44d7-b37a-13beeece0a5f"
    key = derive_execution_key(profile(), app_id=APP, node_ids=frozenset({"assessment"}))
    calls = []
    submitted = {}

    def handler(request):
        calls.append(request)
        step = len(calls)
        if step == 1:
            body = dict(enabled=True, credential_setup_enabled=True, workspace_id=WS)
        elif step == 2:
            body = [
                dict(
                    id=provider,
                    name=provider,
                    plugin_id="enterprise/enterprise_device_assessment",
                    plugin_unique_identifier="plugin@v1",
                )
            ]
        elif step == 3:
            body = [dict(name="evaluate_device")]
        elif step == 4:
            body = dict(supported_credential_types=["api-key"], credentials=[])
        elif step == 5:
            submitted.update(json.loads(request.content))
            body = dict(result="success")
        else:
            body = dict(
                supported_credential_types=["api-key"],
                credentials=[
                    dict(
                        id=credential,
                        name=submitted["name"],
                        provider=provider,
                        credential_type="api-key",
                        visibility="all_team_members",
                        created_by="actor",
                        credentials=submitted["credentials"] | {"secret": "masked"},
                    )
                ],
            )
        return httpx.Response(200, json=body, headers={"X-Enterprise-Workspace": WS})

    principal = Principal(workspace_id=WS, actor_id="actor", workspace_role="owner", display_name="Owner")
    factory = ProfiledWorkflowPluginClientFactory(
        base_url="https://native.internal/console/api", profiles=(profile(),), transport=httpx.MockTransport(handler)
    )
    client = asyncio.run(factory.resolve(principal, app_id=APP, configuration_ref="profile", configuration_revision=1))
    session = NativeSetupSession("access_token=native; csrf_token=csrf", None, "csrf")
    outcome = asyncio.run(client.prepare(principal, session, app_id=APP, operation_id=operation))
    assert outcome.state == "credential_created"
    assert submitted["credentials"]["key_id"] == key.key_id
    assert submitted["credentials"]["secret"] == key.secret.get_secret_value()
    assert submitted["credentials"]["expected_app_id"] == APP
    assert MASTER not in json.dumps(submitted) + repr(outcome)


def test_public_profile_choices_are_scoped_sorted_and_secret_free():
    from enterprise_platform.application.workflow_plugin_profiles import PluginProfileChoice

    registry = WorkflowPluginProfileRegistry(
        (
            profile(config_ref="z"),
            profile(config_revision=2, display_name="Second"),
            profile(),
            profile(workspace_id=OTHER, config_ref="hidden"),
        )
    )
    choices = registry.list_profiles(WS)
    assert choices == (
        PluginProfileChoice(config_ref="profile", config_revision=1, display_name="profile"),
        PluginProfileChoice(config_ref="profile", config_revision=2, display_name="Second"),
        PluginProfileChoice(config_ref="z", config_revision=1, display_name="z"),
    )
    assert all(set(item.model_dump()) == {"config_ref", "config_revision", "display_name"} for item in choices)
    assert MASTER not in repr(choices)
    assert registry.list_profiles(APP) == ()


def test_display_name_changes_never_change_derived_key():
    first = derive_execution_key(profile(), app_id=APP, node_ids=frozenset({"assessment"}))
    renamed = derive_execution_key(
        profile(display_name="Human display"), app_id=APP, node_ids=frozenset({"assessment"})
    )
    assert first == renamed


@pytest.mark.parametrize("value", ["", "   ", "x" * 129, 1])
def test_invalid_profile_display_name(value):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        profile(display_name=value)


def test_listing_rejects_invalid_workspace_and_choices_are_strict_frozen():
    from pydantic import ValidationError

    from enterprise_platform.application.workflow_plugin_profiles import PluginProfileChoice

    registry = WorkflowPluginProfileRegistry(())
    with pytest.raises(PluginCredentialRejected):
        registry.list_profiles("bad")
    choice = PluginProfileChoice(config_ref="profile", config_revision=1, display_name="Title")
    with pytest.raises(ValidationError):
        choice.config_revision = 2
    with pytest.raises(ValidationError):
        PluginProfileChoice(config_ref="profile", config_revision=True, display_name="Title")
    with pytest.raises(ValidationError):
        PluginProfileChoice(config_ref="profile", config_revision=1, display_name="Title", master_key_id="hidden")
