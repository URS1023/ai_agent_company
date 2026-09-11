from datetime import UTC, datetime
from itertools import count
from unittest.mock import Mock

import pytest
from test_source_contracts import db_draft
from test_source_encryption import keyring

from enterprise_platform.adapters.source_encryption import AesGcmSourceCipher
from enterprise_platform.application.contracts import Page, Principal
from enterprise_platform.application.errors import AccessDenied, Conflict, DependencyUnavailable, InvalidInput, NotFound
from enterprise_platform.application.input_capture import ImmutableReadCatalog
from enterprise_platform.application.source_contracts import SourceDraft
from enterprise_platform.application.source_registration import EndpointPolicy, SourceEndpoint
from enterprise_platform.application.source_service import SourceService, StoredReadCatalog


class Sources:
    def __init__(self):
        self.versions = []

    def find_request(self, workspace_id, request_key):
        return next(
            (s for s in self.versions if s.view.workspace_id == workspace_id and s.request_key == request_key), None
        )

    def create(self, source, *, actor_id):
        if self.find_request(source.view.workspace_id, source.request_key):
            raise Conflict()
        self.versions.append(source)
        return source

    def get(self, workspace_id, source_id):
        found = [s for s in self.versions if (s.view.workspace_id, s.view.source_id) == (workspace_id, source_id)]
        if not found:
            raise NotFound()
        return found[-1]

    def update(self, source, *, expected_revision, actor_id):
        if self.get(source.view.workspace_id, source.view.source_id).view.revision != expected_revision:
            raise Conflict()
        self.versions.append(source)
        return source

    def list(self, workspace_id, *, offset, limit):
        heads = {s.view.source_id: s.view for s in self.versions if s.view.workspace_id == workspace_id}
        items = tuple(heads.values())
        return Page(items=items[offset : offset + limit], total=len(items), offset=offset, limit=limit)

    def find_read(self, *key):
        return next(
            (
                s
                for s in self.versions
                if (s.view.workspace_id, s.view.source_id, s.view.source_revision, s.view.read_id, s.view.read_revision)
                == key
            ),
            None,
        )


def principal(role="owner", workspace="workspace-1"):
    return Principal(actor_id="actor-1", workspace_id=workspace, workspace_role=role, display_name="User")


def policy():
    return EndpointPolicy(
        (
            SourceEndpoint(kind="db", host="collector.test", port=5432, dialect="postgresql"),
            SourceEndpoint(kind="http", host="collector.test", port=443, scheme="https"),
        )
    )


def service():
    from test_input_capture import device

    repo = Sources()
    devices = Mock()
    devices.get_device.return_value = device()
    sequence = count(1)
    cipher = AesGcmSourceCipher(keyring())
    return (
        SourceService(
            repo,
            devices,
            cipher=cipher,
            endpoints=policy(),
            clock=lambda: datetime(2026, 9, 8, tzinfo=UTC),
            id_factory=lambda: f"server-{next(sequence)}",
        ),
        repo,
        devices,
        cipher,
    )


def test_create_update_exact_history_and_idempotency_never_follow_head() -> None:
    app, repo, devices, cipher = service()
    draft = SourceDraft.model_validate(db_draft())
    first = app.create_source(principal(), draft, request_key="request-1")
    assert first.revision == 1 and first.connection_status == "not_tested"
    assert first.workspace_id == "workspace-1"
    assert "fixture-password" not in first.model_dump_json() and "username" not in first.model_dump_json()
    data = db_draft()
    data["name"] = "renamed"
    data["connection"].pop("username")
    data["connection"].pop("password")
    second = app.update_source(principal(), first.source_id, SourceDraft.model_validate(data), expected_revision=1)
    assert second.revision == 2 and second.source_id == first.source_id and second.read_id == first.read_id
    assert second.source_revision != first.source_revision and second.read_revision != first.read_revision
    assert app.create_source(principal(), draft, request_key="request-1") == first
    catalog = StoredReadCatalog(repo, cipher, policy(), ImmutableReadCatalog(()))
    old = catalog.resolve(
        first.workspace_id, first.source_id, first.source_revision, first.read_id, first.read_revision
    )
    assert "fixture-password" in old.connection.connection_url.get_secret_value()
    with pytest.raises(InvalidInput):
        catalog.resolve(first.workspace_id, first.source_id, first.source_revision, first.read_id, second.read_revision)
    with pytest.raises(Conflict):
        app.update_source(principal(), first.source_id, draft, expected_revision=1)
    assert len(repo.versions) == 2
    with pytest.raises(Conflict):
        app.create_source(principal(), SourceDraft.model_validate(data), request_key="request-1")
    assert len(repo.versions) == 2


@pytest.mark.parametrize("role", ["editor", "normal", "dataset_operator"])
def test_only_workspace_owners_and_admins_manage_but_all_read(role: str) -> None:
    app, repo, devices, cipher = service()
    view = app.create_source(principal(), SourceDraft.model_validate(db_draft()), request_key="request-1")
    assert not app.capabilities(principal(role)).can_manage
    assert app.get_source(principal(role), view.source_id) == view
    assert app.list_sources(principal(role)).total == 1
    with pytest.raises(AccessDenied):
        app.create_source(principal(role), SourceDraft.model_validate(db_draft()), request_key="other")
    with pytest.raises(NotFound):
        app.get_source(principal(role, "other-workspace"), view.source_id)


def test_missing_key_or_endpoint_policy_disables_writes_not_read() -> None:
    app, repo, devices, cipher = service()
    view = app.create_source(principal(), SourceDraft.model_validate(db_draft()), request_key="request-1")
    for no_key, empty_policy, reason in [
        (True, False, "encryption_key_missing"),
        (False, True, "source_egress_policy_missing"),
    ]:
        disabled = SourceService(
            repo, devices, cipher=None if no_key else cipher, endpoints=EndpointPolicy(()) if empty_policy else policy()
        )
        assert disabled.capabilities(principal()).reason_code == reason
        assert disabled.get_source(principal(), view.source_id) == view
        with pytest.raises(DependencyUnavailable):
            disabled.create_source(principal(), SourceDraft.model_validate(db_draft()), request_key="other")


@pytest.mark.parametrize(
    "change",
    [
        {"host": "unregistered.test"},
        {"port": 5433},
        {"tls": False},
        {"sql": "DELETE FROM measurements"},
        {"sql": "SELECT * FROM forbidden WHERE device_code = :device"},
    ],
)
def test_operator_egress_and_existing_readonly_sql_validation_before_persist(change) -> None:
    app, repo, devices, cipher = service()
    data = db_draft()
    data["connection"].update(change)
    with pytest.raises((InvalidInput, AccessDenied)):
        app.create_source(principal(), SourceDraft.model_validate(data), request_key="r")
    assert not repo.versions


def test_cross_workspace_device_is_rejected_before_save() -> None:
    app, repo, devices, cipher = service()
    devices.get_device.return_value = devices.get_device.return_value.model_copy(update={"workspace_id": "other"})
    with pytest.raises(AccessDenied):
        app.create_source(principal(), SourceDraft.model_validate(db_draft()), request_key="r")
    assert not repo.versions


def test_database_target_change_requires_fresh_credentials() -> None:
    app, repo, devices, cipher = service()
    first = app.create_source(principal(), SourceDraft.model_validate(db_draft()), request_key="r")
    data = db_draft()
    data["connection"].update(database="different", username=None, password=None)
    with pytest.raises(InvalidInput):
        app.update_source(principal(), first.source_id, SourceDraft.model_validate(data), expected_revision=1)
    assert len(repo.versions) == 1


def test_current_endpoint_revocation_blocks_old_uncollected_versions() -> None:
    app, repo, devices, cipher = service()
    first = app.create_source(principal(), SourceDraft.model_validate(db_draft()), request_key="r")
    with pytest.raises(AccessDenied):
        StoredReadCatalog(repo, cipher, EndpointPolicy(()), ImmutableReadCatalog(())).resolve(
            first.workspace_id, first.source_id, first.source_revision, first.read_id, first.read_revision
        )


def http_draft():
    data = db_draft()
    data["connection"] = {
        "kind": "http",
        "url": "https://collector.test/rows",
        "method": "GET",
        "rows_path": ["records"],
        "headers": [{"name": "Authorization", "value": "fixture-token"}],
    }
    return data


def test_http_header_keep_clear_and_changed_target_are_explicit_and_reader_contract_matches() -> None:
    app, repo, devices, cipher = service()
    data = http_draft()
    data["connection"]["pagination"] = {"parameter": "cursor", "next_cursor_path": ["next"]}
    # Policy models are strict at a Python boundary; ordinary wire JSON is validated as JSON.
    import json

    draft = SourceDraft.model_validate_json(json.dumps(data))
    first = app.create_source(principal(), draft, request_key="http1")
    entry = cipher.open(first, repo.versions[0].sealed)
    assert entry.connection.url == first.connection.url
    assert entry.read.method == first.connection.method
    assert entry.read.rows_path == first.connection.rows_path
    assert entry.read.pagination == first.connection.pagination
    assert first.connection.header_names == ("Authorization",)
    assert "fixture-token" not in first.model_dump_json()
    keep = draft.model_copy(update={"connection": draft.connection.model_copy(update={"headers": None})})
    second = app.update_source(principal(), first.source_id, keep, expected_revision=1)
    assert cipher.open(second, repo.versions[-1].sealed).connection.headers == entry.connection.headers
    changed = keep.model_copy(
        update={"connection": keep.connection.model_copy(update={"url": "https://collector.test/other"})}
    )
    with pytest.raises(InvalidInput):
        app.update_source(principal(), first.source_id, changed, expected_revision=2)
    clear = changed.model_copy(update={"connection": changed.connection.model_copy(update={"headers": ()})})
    third = app.update_source(principal(), first.source_id, clear, expected_revision=2)
    assert not cipher.open(third, repo.versions[-1].sealed).connection.headers
    assert not third.connection.credentials_configured
    assert len(repo.versions) == 3


@pytest.mark.parametrize(
    "connection",
    [
        {"url": "https://user:pass@collector.test/rows"},
        {"url": "https://collector.test/rows?token=secret"},
        {"url": "https://collector.test:444/rows"},
        {"headers": [{"name": "Host", "value": "other"}]},
        {"headers": [{"name": "Authorization", "value": "secret"}, {"name": "authorization", "value": "secret"}]},
    ],
)
def test_http_untrusted_egress_or_routing_headers_never_persist(connection) -> None:
    app, repo, devices, cipher = service()
    data = http_draft()
    data["connection"].update(connection)
    with pytest.raises((InvalidInput, AccessDenied)):
        app.create_source(principal(), SourceDraft.model_validate(data), request_key="r")
    assert not repo.versions


def test_static_exact_fallback_only_on_missing_and_duplicate_scope_fails() -> None:
    from test_source_encryption import registration_pair

    from enterprise_platform.application.errors import PersistenceError

    view, entry = registration_pair()
    repo = Sources()
    cipher = AesGcmSourceCipher(keyring())
    catalog = StoredReadCatalog(repo, cipher, policy(), ImmutableReadCatalog((entry,)))
    key = (view.workspace_id, view.source_id, view.source_revision, view.read_id, view.read_revision)
    assert catalog.resolve(*key) == entry
    from enterprise_platform.application.source_ports import StoredSource

    repo.versions.append(StoredSource(view, cipher.seal(view, entry), "r", "a" * 64, "key-1"))
    with pytest.raises(Conflict):
        catalog.resolve(*key)
    broken = Mock()
    broken.find_read.side_effect = PersistenceError("fixture-raw-secret")
    with pytest.raises(PersistenceError):
        StoredReadCatalog(broken, cipher, policy(), ImmutableReadCatalog((entry,))).resolve(*key)


def test_rotation_replays_original_create_request_using_stored_fingerprint_key() -> None:
    app, repo, devices, cipher = service()
    draft = SourceDraft.model_validate(db_draft())
    first = app.create_source(principal(), draft, request_key="r")
    rotated = AesGcmSourceCipher(keyring().model_copy(update={"active_key_id": "key-2"}))
    newer = SourceService(repo, devices, cipher=rotated, endpoints=policy())
    assert newer.create_source(principal(), draft, request_key="r") == first
    assert len(repo.versions) == 1


def test_invalid_forged_contract_is_revalidated_before_any_persistence() -> None:
    app, repo, devices, cipher = service()
    draft = SourceDraft.model_validate(db_draft())
    forged = draft.model_copy(update={"device_ids": ()})
    with pytest.raises(InvalidInput):
        app.create_source(principal(), forged, request_key="r")
    assert not repo.versions
