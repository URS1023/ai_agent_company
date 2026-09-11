import asyncio
from dataclasses import replace
from threading import Lock, get_ident
from unittest.mock import Mock

import pytest
from test_input_capture import device
from test_source_contracts import db_draft
from test_source_service import principal
from test_source_service import service as source_service

from enterprise_platform.application.contracts import Page
from enterprise_platform.application.errors import AccessDenied, Conflict, InvalidInput, NotFound, PersistenceError
from enterprise_platform.application.service import BusinessService
from enterprise_platform.application.source_contracts import SourceDraft
from enterprise_platform.application.workflow_setup_contracts import SetupRequest, SetupView
from enterprise_platform.application.workflow_setup_execution import (
    NativeImportOutcome,
    NativeSetupSession,
    SetupImportRejected,
)
from enterprise_platform.application.workflow_setup_service import WorkflowSetupService

SESSION = NativeSetupSession("session=secret", "Bearer secret", "csrf-secret")


class Setups:
    def __init__(self):
        self.rows = {}
        self.lock = Lock()
        self.thread_ids = []
        self.fail_finish = False

    def find_request(self, workspace_id, request_key):
        self.thread_ids.append(get_ident())
        return next(
            (s for s in self.rows.values() if s.view.workspace_id == workspace_id and s.request_key == request_key),
            None,
        )

    def create(self, setup, *, actor_id):
        with self.lock:
            existing = self.find_request(setup.view.workspace_id, setup.request_key)
            if existing:
                return existing
            self.rows[setup.view.id] = setup
            return setup

    def get(self, workspace_id, setup_id):
        value = self.rows.get(setup_id)
        if value is None or value.view.workspace_id != workspace_id:
            raise NotFound()
        return value

    def list(self, workspace_id, *, device_id, scenario, offset, limit):
        rows = tuple(
            s.view
            for s in self.rows.values()
            if (s.view.workspace_id, s.view.device_id, s.view.scenario) == (workspace_id, device_id, scenario)
        )
        return Page(items=rows[offset : offset + limit], total=len(rows), offset=offset, limit=limit)

    def claim_import(self, workspace_id, setup_id, *, expected_revision, nonce, actor_id):
        with self.lock:
            old = self.get(workspace_id, setup_id)
            if old.view.state != "queued" or old.view.revision != expected_revision:
                raise Conflict()
            value = replace(
                old,
                view=old.view.model_copy(update={"state": "importing", "revision": old.view.revision + 1}),
                import_nonce=nonce,
            )
            self.rows[setup_id] = value
            return value

    def finish_import(self, workspace_id, setup_id, *, nonce, state, app_id, import_id, reason_code, actor_id):
        if self.fail_finish:
            raise PersistenceError()
        with self.lock:
            old = self.get(workspace_id, setup_id)
            if old.import_nonce != nonce or old.view.state != "importing":
                raise Conflict()
            view = SetupView.model_validate(
                old.view.model_dump()
                | dict(
                    state=state,
                    app_id=app_id,
                    import_id=import_id,
                    reason_code=reason_code,
                    revision=old.view.revision + 1,
                )
            )
            value = replace(old, view=view, import_nonce=None)
            self.rows[setup_id] = value
            return value


class Importer:
    def __init__(self, repo):
        self.repo = repo
        self.calls = []
        self.outcome = NativeImportOutcome("draft_ready", "app", "import")
        self.error = None

    async def import_default(self, actor, session, *, setup_id, scenario, name):
        assert self.repo.get(actor.workspace_id, setup_id).view.state == "importing"
        self.calls.append((actor, session, setup_id, scenario, name))
        await asyncio.sleep(0.001)
        if self.error:
            raise self.error
        return self.outcome


def harness():
    sources, _, _, _ = source_service()
    view = sources.create_source(principal(), SourceDraft.model_validate(db_draft()), request_key="source-key")
    business = Mock(spec=BusinessService)
    business.get_device.return_value = device()
    business.get_binding.side_effect = NotFound()
    catalog = Mock()
    catalog.get_source.return_value = view
    repo = Setups()
    importer = Importer(repo)
    service = WorkflowSetupService(repo, business, catalog, importer)
    request = SetupRequest(source_id=view.source_id, expected_source_revision=view.revision)
    return service, repo, business, catalog, importer, request


def start(service, request, actor=None, key="request"):
    return service.start(actor or principal(), SESSION, "device-1", "alert", request, key)


def test_store_before_io_and_replay_frozen_command_before_changed_source_or_binding():
    service, repo, business, sources, importer, request = harness()
    result = asyncio.run(start(service, request))
    assert result.state == "draft_ready"
    assert result.source_revision == sources.get_source.return_value.source_revision
    assert result.name.startswith(device().name)
    assert result.revision == 3
    assert result.expected_binding_revision is None
    sources.get_source.side_effect = NotFound()
    business.get_binding.side_effect = Conflict()
    assert asyncio.run(start(service, request)) == result
    assert len(importer.calls) == 1
    assert set(repo.thread_ids) != {get_ident()}
    assert not ({"request_hash", "import_nonce", "authorization", "cookie_header"} & result.model_dump().keys())


def test_same_key_actor_or_command_conflict():
    service, _, _, _, importer, request = harness()
    asyncio.run(start(service, request))
    for actor, changed in (
        (principal().model_copy(update={"actor_id": "other"}), request),
        (principal(), request.model_copy(update={"expected_source_revision": 2})),
    ):
        with pytest.raises(Conflict):
            asyncio.run(start(service, changed, actor))
    assert len(importer.calls) == 1


@pytest.mark.parametrize("kind", ["revision", "membership", "workspace", "source_id", "device", "binding"])
def test_fresh_scope_and_revision_rejections_have_no_native_side_effect(kind):
    service, repo, business, sources, importer, request = harness()
    if kind == "device":
        business.get_device.return_value = device().model_copy(update={"workspace_id": "other"})
    elif kind == "binding":
        business.get_binding.side_effect = None
        business.get_binding.return_value = Mock(
            revision=1, workspace_id="workspace-1", device_id="device-1", scenario="alert"
        )
    else:
        updates = {
            "revision": {"revision": 2},
            "membership": {"device_ids": ("other",)},
            "workspace": {"workspace_id": "other"},
            "source_id": {"source_id": "other"},
        }
        sources.get_source.return_value = sources.get_source.return_value.model_copy(update=updates[kind])
    with pytest.raises((Conflict, AccessDenied)):
        asyncio.run(start(service, request))
    assert not importer.calls
    assert not repo.rows


@pytest.mark.parametrize("state", ["draft_ready", "confirmation_required", "failed", "uncertain"])
def test_native_outcomes_are_durable_and_never_reimported(state):
    service, _, _, _, importer, request = harness()
    importer.outcome = NativeImportOutcome(state, "app", "import")
    result = asyncio.run(start(service, request))
    assert result.state == state
    assert asyncio.run(start(service, request)) == result
    assert len(importer.calls) == 1


@pytest.mark.parametrize(
    "error,state,reason",
    [
        (RuntimeError("session=secret"), "uncertain", "native_workflow_setup_uncertain"),
        (SetupImportRejected("secret"), "failed", "native_workflow_setup_unavailable"),
    ],
)
def test_import_errors_are_sanitized_and_persisted(error, state, reason):
    service, _, _, _, importer, request = harness()
    importer.error = error
    result = asyncio.run(start(service, request))
    assert (result.state, result.reason_code) == (state, reason)
    assert "secret" not in result.model_dump_json()


def test_cancellation_keeps_importing_and_retry_never_posts():
    service, repo, _, _, importer, request = harness()
    importer.error = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(start(service, request))
    assert next(iter(repo.rows.values())).view.state == "importing"
    assert asyncio.run(start(service, request)).state == "importing"
    assert len(importer.calls) == 1


def test_finish_failure_never_claims_success_or_reimports():
    service, repo, _, _, importer, request = harness()
    repo.fail_finish = True
    with pytest.raises(PersistenceError):
        asyncio.run(start(service, request))
    assert asyncio.run(start(service, request)).state == "importing"
    assert len(importer.calls) == 1


def test_competing_requests_only_one_claimant_imports():
    service, _, _, _, importer, request = harness()

    async def execute():
        return await asyncio.gather(*(start(service, request) for _ in range(8)))

    results = asyncio.run(execute())
    assert len({value.id for value in results}) == 1
    assert len(importer.calls) == 1


def test_reads_are_device_scoped_and_pagination_is_validated():
    service, _, business, _, _, request = harness()
    created = asyncio.run(start(service, request))
    assert asyncio.run(service.get(principal(), created.id)) == created
    assert asyncio.run(service.list(principal(), "device-1", "alert")).items == (created,)
    with pytest.raises(InvalidInput):
        asyncio.run(service.list(principal(), "device-1", "alert", limit=True))
    business.get_device.side_effect = NotFound()
    with pytest.raises(NotFound):
        asyncio.run(service.get(principal(), created.id))


def test_permissions_and_constructed_payload_are_revalidated():
    service, repo, _, _, _, request = harness()
    with pytest.raises(AccessDenied):
        asyncio.run(start(service, request, principal("normal")))
    with pytest.raises(InvalidInput):
        asyncio.run(start(service, request.model_copy(update={"expected_source_revision": True})))
    with pytest.raises(InvalidInput):
        asyncio.run(start(service, request, key=" "))
    assert not repo.rows


def test_queued_recovery_uses_original_source_snapshot_after_preclaim_failure():
    service, repo, _, sources, importer, request = harness()
    original_claim = repo.claim_import
    repo.claim_import = Mock(side_effect=PersistenceError())
    with pytest.raises(PersistenceError):
        asyncio.run(start(service, request))
    queued = next(iter(repo.rows.values()))
    assert queued.view.state == "queued"
    sources.get_source.side_effect = NotFound()
    repo.claim_import = original_claim
    result = asyncio.run(start(service, request))
    assert result.state == "draft_ready"
    assert result.source_revision == queued.view.source_revision
    assert len(importer.calls) == 1


def test_lost_claim_returns_durable_current_state_without_native_io():
    service, repo, _, _, importer, request = harness()
    original_claim = repo.claim_import

    def competing_claim(*args, **kwargs):
        original_claim(*args, **(kwargs | {"nonce": "another-owner"}))
        raise Conflict()

    repo.claim_import = competing_claim
    assert asyncio.run(start(service, request)).state == "importing"
    assert not importer.calls


@pytest.mark.parametrize("device_id,scenario", [("x" * 129, "alert"), ("device-1", "unknown")])
def test_direct_call_validates_path_fields_before_io(device_id, scenario):
    service, repo, _, _, _, request = harness()
    with pytest.raises(InvalidInput):
        asyncio.run(service.start(principal(), SESSION, device_id, scenario, request, "key"))
    assert not repo.thread_ids


def test_exact_binding_revision_is_frozen_and_never_written():
    service, _, business, _, _, request = harness()
    business.get_binding.side_effect = None
    business.get_binding.return_value = Mock(
        revision=3, workspace_id="workspace-1", device_id="device-1", scenario="alert"
    )
    result = asyncio.run(start(service, request.model_copy(update={"expected_binding_revision": 3})))
    assert result.expected_binding_revision == 3
    business.put_binding.assert_not_called()


def test_malformed_import_result_is_uncertain_not_false_draft_success():
    service, _, _, _, importer, request = harness()
    importer.outcome = NativeImportOutcome("draft_ready")
    assert asyncio.run(start(service, request)).state == "uncertain"
