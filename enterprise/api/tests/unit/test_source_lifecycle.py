"""Versioned source lifecycle and preservation of pre-lifecycle encrypted registrations."""

import hashlib

import pytest
from pydantic import ValidationError
from test_source_contracts import db_draft
from test_source_encryption import keyring, registration_pair
from test_source_service import policy, principal, service

from enterprise_platform.adapters.source_encryption import AesGcmSourceCipher
from enterprise_platform.application.errors import AccessDenied
from enterprise_platform.application.input_capture import ImmutableReadCatalog
from enterprise_platform.application.source_contracts import SourceDraft
from enterprise_platform.application.source_service import StoredReadCatalog


def test_legacy_default_enabled_keeps_aad_and_request_fingerprint():
    view, _ = registration_pair()
    cipher = AesGcmSourceCipher(keyring())
    assert view.enabled is True
    assert (
        hashlib.sha256(cipher._aad(view, "key-1")).hexdigest()
        == "1ac7864d10c2f9302f442e4ed8be5cf22702136fd3f3dd6961c1c55b21a7219c"
    )
    assert (
        cipher.fingerprint(SourceDraft.model_validate(db_draft()))[1]
        == "08ab9d5bbb5edff71bf021a71eecc519076334e83bed0ea4a9609ea047fe58bd"
    )
    assert cipher._aad(view.model_copy(update={"enabled": False}), "key-1") != cipher._aad(view, "key-1")


@pytest.mark.parametrize("value", ["false", 0, None])
def test_enable_flag_requires_boolean(value):
    with pytest.raises(ValidationError):
        SourceDraft.model_validate({**db_draft(), "enabled": value})


def test_disable_blocks_historical_registration_but_preserves_history():
    app, repo, _, cipher = service()
    draft = SourceDraft.model_validate(db_draft())
    first = app.create_source(principal(), draft, request_key="r")
    catalog = StoredReadCatalog(repo, cipher, policy(), ImmutableReadCatalog(()))
    key = (first.workspace_id, first.source_id, first.source_revision, first.read_id, first.read_revision)
    assert catalog.resolve(*key)
    disabled = SourceDraft.model_validate({**db_draft(), "enabled": False})
    second = app.update_source(principal(), first.source_id, disabled, expected_revision=1)
    assert second.enabled is False and second.revision == 2
    assert repo.versions[0].view.enabled is True
    with pytest.raises(AccessDenied, match="source_disabled"):
        catalog.resolve(*key)
    third = app.update_source(principal(), first.source_id, draft, expected_revision=2)
    assert third.enabled is True
    assert catalog.resolve(*key)


def test_disabled_create_is_not_resolvable():
    app, repo, _, cipher = service()
    view = app.create_source(principal(), SourceDraft.model_validate({**db_draft(), "enabled": False}), request_key="r")
    catalog = StoredReadCatalog(repo, cipher, policy(), ImmutableReadCatalog(()))
    with pytest.raises(AccessDenied, match="source_disabled"):
        catalog.resolve(view.workspace_id, view.source_id, view.source_revision, view.read_id, view.read_revision)


def test_changed_head_enabled_flag_without_matching_ciphertext_is_rejected():
    from dataclasses import replace

    from enterprise_platform.application.errors import PersistenceError

    app, repo, _, cipher = service()
    first = app.create_source(principal(), SourceDraft.model_validate(db_draft()), request_key="r")
    app.update_source(
        principal(), first.source_id, SourceDraft.model_validate({**db_draft(), "enabled": False}), expected_revision=1
    )
    stored = repo.versions[-1]
    repo.versions[-1] = replace(stored, view=stored.view.model_copy(update={"enabled": True}))
    catalog = StoredReadCatalog(repo, cipher, policy(), ImmutableReadCatalog(()))
    with pytest.raises(PersistenceError):
        catalog.resolve(first.workspace_id, first.source_id, first.source_revision, first.read_id, first.read_revision)


def test_static_fallback_does_not_bypass_disabled_stored_source_identity():
    app, repo, _, cipher = service()
    first = app.create_source(principal(), SourceDraft.model_validate(db_draft()), request_key="r")
    entry = cipher.open(first, repo.versions[0].sealed)
    source = entry.read.source.model_copy(update={"revision": "static-revision"})
    static = entry.model_copy(
        update={
            "connection": entry.connection.model_copy(update={"source": source}),
            "read": entry.read.model_copy(update={"source": source}),
        }
    )
    app.update_source(
        principal(), first.source_id, SourceDraft.model_validate({**db_draft(), "enabled": False}), expected_revision=1
    )
    catalog = StoredReadCatalog(repo, cipher, policy(), ImmutableReadCatalog((static,)))
    with pytest.raises(AccessDenied, match="source_disabled"):
        catalog.resolve(first.workspace_id, first.source_id, "static-revision", first.read_id, first.read_revision)
