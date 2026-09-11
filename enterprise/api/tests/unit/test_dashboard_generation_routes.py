from unittest.mock import create_autospec

import pytest
from test_dashboard_routes import HEADERS, URL, fixture
from test_dashboard_sql_generation import generate, setup

from enterprise_platform.application.dashboard_sql_generation import DashboardSqlGeneration, GenerateSqlCommand
from enterprise_platform.application.errors import Conflict, DependencyUnavailable, Unauthenticated


def generation_fixture():
    service, _, _, _, command = setup()
    generation = create_autospec(DashboardSqlGeneration, instance=True)
    generation.generate.return_value = generate(service, command)
    client, _, refresh, identity = fixture(sql_generation=generation)
    return client, generation, command.model_dump(mode="json"), identity, refresh


def test_generation_api_returns_private_draft_without_executing_refresh():
    client, generation, command, identity, refresh = generation_fixture()
    response = client.post(URL + "/sql-proposals", json=command, headers=HEADERS)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json()["status"] == "draft"
    assert response.json()["proposals"]["slots"][0]["sql"] == "SELECT count FROM measurements"
    generation.generate.assert_awaited_once_with(
        identity.resolve.return_value, "dashboard-1", GenerateSqlCommand.model_validate(command)
    )
    refresh.refresh.assert_not_awaited()


@pytest.mark.parametrize("case", ["sql", "schema", "style", "blank", "revision", "source"])
def test_generation_input_rejects_browser_schema_sql_or_invalid_context(case):
    client, generation, command, _, _ = generation_fixture()
    if case in {"sql", "schema", "style"}:
        command[case] = "untrusted"
    elif case == "blank":
        command["prompt"] = " "
    elif case == "revision":
        command["expected_revision"] = True
    else:
        del command["source_id"]
    response = client.post(URL + "/sql-proposals", json=command, headers=HEADERS)
    assert response.status_code == 422
    assert response.headers["cache-control"] == "private, no-store"
    generation.generate.assert_not_awaited()


@pytest.mark.parametrize("case,status", [("readonly", 403), ("origin", 403), ("login", 401)])
def test_generation_requires_native_identity_manage_permission_and_origin(case, status):
    client, generation, command, identity, _ = generation_fixture()
    headers = dict(HEADERS)
    if case == "readonly":
        identity.resolve.return_value = identity.resolve.return_value.model_copy(update={"workspace_role": "normal"})
    elif case == "origin":
        headers["Origin"] = "https://other.test"
    else:
        identity.resolve.side_effect = Unauthenticated()
    response = client.post(URL + "/sql-proposals", json=command, headers=headers)
    assert response.status_code == status
    assert response.headers["cache-control"] == "private, no-store"
    generation.generate.assert_not_awaited()


@pytest.mark.parametrize("error,status", [(Conflict, 409), (DependencyUnavailable, 503)])
def test_generation_errors_hide_internal_details(error, status):
    client, generation, command, _, _ = generation_fixture()
    generation.generate.side_effect = error("private connection data")
    response = client.post(URL + "/sql-proposals", json=command, headers=HEADERS)
    assert response.status_code == status
    assert response.json() == {"code": error.code}
    assert response.headers["cache-control"] == "private, no-store"


def test_unconfigured_generation_reports_unavailable_not_a_fake_draft():
    client, _, _, _ = fixture()
    _, _, _, _, command = setup()
    response = client.post(URL + "/sql-proposals", json=command.model_dump(mode="json"), headers=HEADERS)
    assert response.status_code == 503


def test_saved_draft_read_is_private_management_only_and_does_not_generate():
    from test_sql_draft_inspection import fixture as draft_fixture
    from test_sql_draft_inspection import inspect

    service, _, _, _, draft_id = draft_fixture()
    inspection = inspect(service, draft_id)
    client, generation, _, identity, refresh = generation_fixture()
    generation.inspect_draft.return_value = inspection
    response = client.get(URL + "/sql-proposals/" + draft_id, headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["draft"]["draft_id"] == draft_id
    assert response.json()["draft"]["prompt"] == inspection.draft.prompt
    assert response.headers["cache-control"] == "private, no-store"
    generation.generate.assert_not_awaited()
    refresh.refresh.assert_not_awaited()
    generation.inspect_draft.reset_mock()
    identity.resolve.return_value = identity.resolve.return_value.model_copy(update={"workspace_role": "normal"})
    assert client.get(URL + "/sql-proposals/" + draft_id, headers=HEADERS).status_code == 403
    generation.inspect_draft.assert_not_awaited()


def test_saved_draft_read_errors_never_echo_private_sql_or_prompt():
    from enterprise_platform.application.errors import AccessDenied

    client, generation, _, _, _ = generation_fixture()
    generation.inspect_draft.side_effect = AccessDenied("private SQL")
    response = client.get(URL + "/sql-proposals/draft-1", headers=HEADERS)
    assert response.status_code == 403
    assert response.json() == {"code": "access_denied"}
    assert response.headers["cache-control"] == "private, no-store"
