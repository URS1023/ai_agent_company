from uuid import UUID

import pytest
from pydantic import ValidationError
from test_workflow_enrollment_contracts import (
    APP,
    NOW,
    TOKEN,
    WS,
    claimed_token,
    credential,
    finish_enrollment_token,
    transition_args,
)
from test_workflow_provisioning_bootstrap import profile as profile_data

from enterprise_platform.application.contracts import Binding
from enterprise_platform.application.errors import AccessDenied, Conflict
from enterprise_platform.application.workflow_activation_contracts import create_activation, revoke_activation
from enterprise_platform.application.workflow_plugin_profiles import PluginCredentialProfile, derive_execution_key


def enrollment():
    stored = claimed_token()
    return finish_enrollment_token(
        stored, state="token_stored", native_token_id=TOKEN, credential=credential(), **transition_args(stored)
    ).view


def profile():
    return PluginCredentialProfile.model_validate(
        profile_data()
        | {
            "workspace_id": WS,
            "config_ref": "plugin-config",
            "expected_plugin_unique_identifier": "enterprise/plugin:1@digest",
        }
    )


def binding():
    e = enrollment()
    p = e.provisioning
    return Binding(
        id="binding",
        workspace_id=WS,
        device_id=p.device_id,
        scenario=p.scenario,
        revision=1,
        app_id=APP,
        workflow_id=UUID(e.publication.workflow_id),
        specification_revision="spec-1",
        secret_ref=e.credential.secret_ref,
        source_id=p.source_id,
        source_revision=p.source_revision,
        read_id=p.read_id,
        read_revision=p.read_revision,
        created_at=NOW,
        updated_at=NOW,
    )


def create(**changes):
    return create_activation(
        enrollment(), binding(), profile(), activation_id=str(UUID(int=51)), actor_id="actor", now=NOW, **changes
    )


def test_activation_pins_binding_and_derives_only_public_key_id():
    value = create()
    assert value.active and value.revision == 1
    assert value.binding == binding()
    assert value.key_id == derive_execution_key(profile(), app_id=APP, node_ids=frozenset({"assessment"})).key_id
    assert profile().master_secret.get_secret_value() not in value.model_dump_json()
    assert "signing_secret" not in value.model_dump_json()


@pytest.mark.parametrize(
    "field,value",
    [
        ("workspace_id", "other"),
        ("app_id", "other"),
        ("device_id", "other"),
        ("scenario", "quality"),
        ("workflow_id", UUID(int=99)),
        ("secret_ref", "other"),
        ("source_revision", "other"),
        ("read_revision", "other"),
        ("revision", 3),
        ("active_run_id", "run"),
    ],
)
def test_unrelated_or_busy_binding_rejected(field, value):
    with pytest.raises((ValidationError, Conflict)):
        create_activation(
            enrollment(),
            binding().model_copy(update={field: value}),
            profile(),
            activation_id=str(UUID(int=51)),
            actor_id="actor",
            now=NOW,
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("workspace_id", str(UUID(int=99))),
        ("config_ref", "other"),
        ("config_revision", 9),
        ("expected_plugin_unique_identifier", "other@digest"),
    ],
)
def test_profile_must_match_prepared_plugin(field, value):
    with pytest.raises(Conflict):
        create_activation(
            enrollment(),
            binding(),
            profile().model_copy(update={field: value}),
            activation_id=str(UUID(int=51)),
            actor_id="actor",
            now=NOW,
        )


def test_only_owned_enrollment_can_activate():
    with pytest.raises(AccessDenied):
        create_activation(
            enrollment(), binding(), profile(), activation_id=str(UUID(int=51)), actor_id="other", now=NOW
        )


def test_revocation_is_revision_fenced_and_not_replayed():
    active = create()
    revoked = revoke_activation(active, expected_revision=1, actor_id="actor", now=NOW)
    assert not revoked.active and revoked.revision == 2
    assert revoked.binding == active.binding and revoked.enrollment == active.enrollment
    with pytest.raises(Conflict):
        revoke_activation(revoked, expected_revision=2, actor_id="actor", now=NOW)
