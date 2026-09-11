from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, create_autospec

import pytest
from fastapi.testclient import TestClient
from test_dashboard_query_execution import actor
from test_dashboard_storage import stored_record

from enterprise_platform.application.dashboard_refresh_service import DashboardRefreshService, DashboardRepository
from enterprise_platform.application.dashboard_service import DashboardService
from enterprise_platform.application.errors import Unauthenticated
from enterprise_platform.http.app import create_app

URL = "/enterprise/api/v1/dashboards/dashboard-1"
HEADERS = {"Origin": "https://console.test", "Authorization": "Bearer private", "X-CSRF-Token": "csrf"}


def test_list_dashboards_is_bounded_scoped_and_omits_data_and_queries() -> None:
    client, repository, refresh, identity = fixture()
    repository.list.return_value = tuple(replace(stored_record(), dashboard_id=f"dashboard-{i}") for i in range(3))
    response = client.get("/enterprise/api/v1/dashboards?limit=2", headers=HEADERS)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json()["next_cursor"] == "dashboard-1"
    assert [item["id"] for item in response.json()["items"]] == ["dashboard-0", "dashboard-1"]
    assert set(response.json()["items"][0]) == {"id", "name", "revision", "template_id", "status"}
    repository.list.assert_called_once_with("workspace-1", after=None, limit=3)
    refresh.refresh.assert_not_awaited()


def test_list_empty_and_cursor_last_page() -> None:
    client, repository, refresh, identity = fixture()
    repository.list.return_value = ()
    response = client.get("/enterprise/api/v1/dashboards?after=dashboard-1", headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None}
    repository.list.assert_called_once_with("workspace-1", after="dashboard-1", limit=51)


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "limit=true", "after=", "after=" + "a" * 129])
def test_list_invalid_pagination_never_reads_repository(query: str) -> None:
    client, repository, refresh, identity = fixture()
    response = client.get("/enterprise/api/v1/dashboards?" + query, headers=HEADERS)
    assert response.status_code == 422
    repository.list.assert_not_called()


def test_list_rejects_cross_workspace_records() -> None:
    client, repository, refresh, identity = fixture()
    repository.list.return_value = (replace(stored_record(), workspace_id="other"),)
    assert client.get("/enterprise/api/v1/dashboards", headers=HEADERS).status_code == 403


def test_list_unauthenticated_never_reads_repository() -> None:
    client, repository, refresh, identity = fixture()
    identity.resolve.side_effect = Unauthenticated()
    assert client.get("/enterprise/api/v1/dashboards", headers=HEADERS).status_code == 401
    repository.list.assert_not_called()


def fixture(
    templates=None,
    binding_service=None,
    query_discovery=None,
    sql_generation=None,
    sql_trial=None,
    sql_trial_evidence=None,
):
    repository = create_autospec(DashboardRepository, instance=True)
    repository.get.return_value = stored_record()
    refresh = create_autospec(DashboardRefreshService, instance=True)
    identity = AsyncMock()
    identity.resolve.return_value = actor()
    service = DashboardService(
        repository,
        refresh,
        templates=templates,
        binding_service=binding_service,
        query_discovery=query_discovery,
        sql_generation=sql_generation,
        sql_trial=sql_trial,
        sql_trial_evidence=sql_trial_evidence,
    )
    client = TestClient(
        create_app(MagicMock(), identity, allowed_origins=("https://console.test",), dashboards=service)
    )
    return client, repository, refresh, identity


def test_query_discovery_is_private_management_only_and_never_refreshes() -> None:
    from enterprise_platform.application.dashboard_query_registry import RegisteredDeviceDashboardQueries

    discovery = create_autospec(RegisteredDeviceDashboardQueries, instance=True)
    discovery.discover.return_value = ()
    client, repository, refresh, identity = fixture(query_discovery=discovery)
    response = client.get("/enterprise/api/v1/dashboard-queries", headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == {"items": []}
    assert response.headers["cache-control"] == "private, no-store"
    discovery.discover.assert_called_once_with(actor())
    refresh.refresh.assert_not_awaited()
    repository.get.assert_not_called()
    identity.resolve.return_value = actor().model_copy(update={"workspace_role": "normal"})
    discovery.discover.reset_mock()
    assert client.get("/enterprise/api/v1/dashboard-queries", headers=HEADERS).status_code == 403
    discovery.discover.assert_not_called()


def test_query_discovery_unconfigured_is_not_empty_success() -> None:
    client, _, _, _ = fixture()
    response = client.get("/enterprise/api/v1/dashboard-queries", headers=HEADERS)
    assert response.status_code == 503
    assert response.headers["cache-control"] == "private, no-store"


def test_query_discovery_authentication_and_dependency_errors_remain_private() -> None:
    from enterprise_platform.application.dashboard_query_registry import RegisteredDeviceDashboardQueries
    from enterprise_platform.application.errors import DependencyUnavailable

    discovery = create_autospec(RegisteredDeviceDashboardQueries, instance=True)
    client, _, _, identity = fixture(query_discovery=discovery)
    identity.resolve.side_effect = Unauthenticated()
    response = client.get("/enterprise/api/v1/dashboard-queries", headers=HEADERS)
    assert response.status_code == 401
    assert response.headers["cache-control"] == "private, no-store"
    discovery.discover.assert_not_called()
    identity.resolve.side_effect = None
    discovery.discover.side_effect = DependencyUnavailable("private-source-detail")
    response = client.get("/enterprise/api/v1/dashboard-queries", headers=HEADERS)
    assert response.status_code == 503
    assert response.json() == {"code": "dependency_unavailable"}
    assert response.headers["cache-control"] == "private, no-store"


def test_binding_read_is_management_only_and_exposes_references_not_connections() -> None:
    client, repository, refresh, identity = fixture()
    response = client.get(URL + "/bindings", headers=HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["dashboard_id"] == "dashboard-1" and body["revision"] == 2
    assert len(body["bindings"]) == 1
    assert set(body["bindings"][0]) == {"slot_id", "query_ref", "query_revision", "field_map", "parameters"}
    assert response.headers["cache-control"] == "private, no-store"
    identity.resolve.return_value = actor().model_copy(update={"workspace_role": "normal"})
    repository.get.reset_mock()
    assert client.get(URL + "/bindings", headers=HEADERS).status_code == 403
    repository.get.assert_not_called()


def test_binding_save_uses_authorized_service_and_returns_committed_revision() -> None:
    from enterprise_platform.domain.dashboard import RefreshState

    bindings = MagicMock()
    bindings.save = AsyncMock(
        return_value=replace(
            stored_record(), revision=3, bindings=(), state=RefreshState(stored_record().design.identity)
        )
    )
    client, repository, refresh, identity = fixture(binding_service=bindings)
    payload = {"expected_revision": 2, "expected_design_identity": stored_record().design.identity, "bindings": []}
    response = client.put(URL + "/bindings", headers=HEADERS, json=payload)
    assert response.status_code == 200
    assert response.json()["revision"] == 3 and response.json()["bindings"] == []
    bindings.save.assert_awaited_once_with(
        actor(),
        "dashboard-1",
        expected_revision=2,
        expected_design_identity=stored_record().design.identity,
        bindings=(),
    )
    refresh.refresh.assert_not_awaited()


@pytest.mark.parametrize("extra", [{"sql": "SELECT 1"}, {"expected_revision": True}])
def test_binding_save_rejects_untyped_or_arbitrary_query_payload(extra) -> None:
    bindings = MagicMock()
    bindings.save = AsyncMock()
    client, repository, refresh, identity = fixture(binding_service=bindings)
    payload = {
        "expected_revision": 2,
        "expected_design_identity": stored_record().design.identity,
        "bindings": [],
        **extra,
    }
    assert client.put(URL + "/bindings", headers=HEADERS, json=payload).status_code == 422
    bindings.save.assert_not_awaited()


@pytest.mark.parametrize("case", ["duplicate", "oversized", "sql", "too_many"])
def test_binding_document_limits_are_enforced_before_save(case: str) -> None:
    bindings = MagicMock()
    bindings.save = AsyncMock()
    client, repository, refresh, identity = fixture(binding_service=bindings)
    item = {
        "slot_id": "metric",
        "query_ref": "query",
        "query_revision": "v1",
        "field_map": {"value": "value"},
        "parameters": {},
    }
    values = [item]
    if case == "duplicate":
        values = [item, item]
    elif case == "oversized":
        item["parameters"] = {"filter": "x" * 262145}
    elif case == "sql":
        item["sql"] = "SELECT 1"
    else:
        values = [{**item, "slot_id": str(index)} for index in range(101)]
    payload = {"expected_revision": 2, "expected_design_identity": stored_record().design.identity, "bindings": values}
    assert client.put(URL + "/bindings", headers=HEADERS, json=payload).status_code == 422
    bindings.save.assert_not_awaited()


@pytest.mark.parametrize("role,origin", [("normal", "https://console.test"), ("editor", "https://other.test")])
def test_binding_save_enforces_management_and_origin(role: str, origin: str) -> None:
    bindings = MagicMock()
    bindings.save = AsyncMock()
    client, repository, refresh, identity = fixture(binding_service=bindings)
    identity.resolve.return_value = actor().model_copy(update={"workspace_role": role})
    payload = {"expected_revision": 2, "expected_design_identity": stored_record().design.identity, "bindings": []}
    assert client.put(URL + "/bindings", headers={**HEADERS, "Origin": origin}, json=payload).status_code == 403
    bindings.save.assert_not_awaited()


def test_create_dashboard_uses_template_and_mandatory_idempotency_key() -> None:
    from enterprise_platform.application.errors import NotFound

    templates = MagicMock()
    templates.get.return_value = stored_record().design
    client, repository, refresh, identity = fixture(templates)
    repository.get.side_effect = NotFound()
    repository.create.side_effect = lambda record, **kwargs: record
    payload = {
        "template_id": stored_record().design.template_id,
        "expected_design_identity": stored_record().design.identity,
    }
    response = client.post(
        "/enterprise/api/v1/dashboards", headers={**HEADERS, "Idempotency-Key": "create-1"}, json=payload
    )
    assert response.status_code == 200
    assert response.json()["status"] == "empty"
    assert response.headers["cache-control"] == "private, no-store"
    repository.create.assert_called_once()
    repository.create.reset_mock()
    for body, headers in (
        (payload, HEADERS),
        ({**payload, "visual_json": "{}"}, {**HEADERS, "Idempotency-Key": "create-2"}),
    ):
        response = client.post("/enterprise/api/v1/dashboards", headers=headers, json=body)
        assert response.status_code == 422
    repository.create.assert_not_called()


@pytest.mark.parametrize(
    "role,origin,status", [("normal", "https://console.test", 403), ("editor", "https://other.test", 403)]
)
def test_create_requires_management_and_console_origin(role: str, origin: str, status: int) -> None:
    client, repository, refresh, identity = fixture()
    identity.resolve.return_value = actor().model_copy(update={"workspace_role": role})
    response = client.post(
        "/enterprise/api/v1/dashboards",
        headers={**HEADERS, "Origin": origin, "Idempotency-Key": "create-1"},
        json={"template_id": "equipment", "expected_design_identity": "a" * 64},
    )
    assert response.status_code == status
    repository.create.assert_not_called()


def test_template_catalog_returns_pinned_metadata_without_visual_or_query_documents() -> None:
    templates = MagicMock()
    design = stored_record().design
    templates.list.return_value = (design,)
    client, repository, refresh, identity = fixture(templates)
    response = client.get("/enterprise/api/v1/dashboard-templates", headers=HEADERS)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    item = response.json()["items"][0]
    assert item["template_id"] == design.template_id
    assert item["design_identity"] == design.identity
    assert item["renderer_build_id"] == design.renderer_build_id
    assert item["template_revision"] == design.template_revision
    assert {"visual_json", "bindings", "sql", "parameters", "asset_digests"}.isdisjoint(item)
    assert len(item["slots"]) == len(design.slots)
    repository.get.assert_not_called()
    templates.list.assert_called_once_with()


def test_template_catalog_requires_authentication_and_explicit_configuration() -> None:
    templates = MagicMock()
    client, repository, refresh, identity = fixture(templates)
    identity.resolve.side_effect = Unauthenticated()
    assert client.get("/enterprise/api/v1/dashboard-templates", headers=HEADERS).status_code == 401
    templates.list.assert_not_called()
    client, repository, refresh, identity = fixture()
    assert client.get("/enterprise/api/v1/dashboard-templates", headers=HEADERS).status_code == 503


def test_template_route_uses_current_pinned_file_and_returns_opaque_errors(tmp_path) -> None:
    from test_dashboard_templates import template, write_catalog

    from enterprise_platform.adapters.dashboard_templates import FileDashboardTemplates

    path = tmp_path / "catalog.json"
    digest = write_catalog(path, [template()])
    client, repository, refresh, identity = fixture(FileDashboardTemplates(path, digest))
    identity.resolve.return_value = actor().model_copy(update={"workspace_role": "normal"})
    response = client.get("/enterprise/api/v1/dashboard-templates", headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["items"][0]["template_id"] == "equipment"
    path.write_text("tampered")
    response = client.get("/enterprise/api/v1/dashboard-templates", headers=HEADERS)
    assert response.status_code == 503
    assert response.json() == {"code": "dependency_unavailable"}
    assert response.headers["cache-control"] == "private, no-store"


def test_get_dashboard_preserves_decimal_text_and_private_headers() -> None:
    client, repository, refresh, identity = fixture()
    response = client.get(URL, headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["current"]["slots"][0]["rows"][0]["decimal"] == {"kind": "decimal", "value": "98.2500"}
    assert response.headers["cache-control"] == "private, no-store"
    refresh.refresh.assert_not_awaited()
    repository.get.assert_called_once_with("workspace-1", "dashboard-1")


def test_refresh_returns_repository_readback_not_optimistic_state() -> None:
    client, repository, refresh, identity = fixture()
    repository.get.return_value = replace(stored_record(), revision=3)
    response = client.post(URL + "/refresh", headers=HEADERS, json={"expected_revision": 2})
    assert response.status_code == 200
    assert response.json()["revision"] == 3
    refresh.refresh.assert_awaited_once_with(actor(), "dashboard-1", expected_revision=2)


@pytest.mark.parametrize("payload", [{"expected_revision": True}, {"expected_revision": 1, "sql": "SELECT 1"}])
def test_refresh_rejects_identity_or_query_override_payloads(payload) -> None:
    client, repository, refresh, identity = fixture()
    response = client.post(URL + "/refresh", headers=HEADERS, json=payload)
    assert response.status_code == 422
    assert response.headers["cache-control"] == "private, no-store"
    refresh.refresh.assert_not_awaited()


def test_native_authentication_failure_never_reads_data() -> None:
    client, repository, refresh, identity = fixture()
    identity.resolve.side_effect = Unauthenticated()
    response = client.get(URL, headers=HEADERS)
    assert response.status_code == 401
    repository.get.assert_not_called()


def test_wrong_workspace_receipt_is_rejected() -> None:
    client, repository, refresh, identity = fixture()
    repository.get.return_value = replace(stored_record(), workspace_id="other")
    assert client.get(URL, headers=HEADERS).status_code == 403


def test_disabled_dashboard_routes_are_explicitly_unavailable() -> None:
    identity = AsyncMock()
    identity.resolve.return_value = actor()
    client = TestClient(create_app(MagicMock(), identity))
    assert client.get(URL, headers=HEADERS).status_code == 503


def test_read_only_member_cannot_refresh_queries() -> None:
    client, repository, refresh, identity = fixture()
    identity.resolve.return_value = actor().model_copy(update={"workspace_role": "normal"})
    assert client.get(URL, headers=HEADERS).status_code == 200
    response = client.post(URL + "/refresh", headers=HEADERS, json={"expected_revision": 2})
    assert response.status_code == 403
    refresh.refresh.assert_not_awaited()


def test_foreign_origin_is_rejected_before_refresh() -> None:
    client, repository, refresh, identity = fixture()
    response = client.post(
        URL + "/refresh", headers={**HEADERS, "Origin": "https://other.test"}, json={"expected_revision": 2}
    )
    assert response.status_code == 403
    refresh.refresh.assert_not_awaited()
