import asyncio
import json
from unittest.mock import create_autospec

import httpx
import pytest
from pydantic import SecretStr
from test_dashboard_refresh_service import principal, record
from test_dashboard_sql_generation import setup as setup_generation
from test_dify_workflows import WORKFLOW_ID, response_payload

from enterprise_platform.adapters.dashboard_sql_generator import NativeSqlDraftGenerator
from enterprise_platform.adapters.dify_workflows import PinnedWorkflowTarget
from enterprise_platform.application.dashboard_sql_generation import (
    DashboardSqlGeneration,
    SqlGenerationRequest,
    SqlSchemaColumn,
    SqlSchemaTable,
)
from enterprise_platform.application.errors import AccessDenied, DependencyUnavailable
from enterprise_platform.application.workflow_credential_ports import WorkflowCredentialResolver


def request():
    return SqlGenerationRequest(
        prompt="Show inspection count",
        dialect="postgresql",
        slots=record().design.slots,
        tables=(SqlSchemaTable(name="measurements", columns=(SqlSchemaColumn(name="count", sql_type="integer"),)),),
    )


def setup(handler):
    credentials = create_autospec(WorkflowCredentialResolver, instance=True)
    credentials.resolve.return_value = SecretStr("app-test-key")
    generator = NativeSqlDraftGenerator(
        base_url="http://dify.internal:5001/v1",
        target=PinnedWorkflowTarget("workspace-1", "app-1", WORKFLOW_ID),
        secret_ref="generation-token",
        credentials=credentials,
        transport=httpx.MockTransport(handler),
    )
    return generator, credentials


def test_native_generator_uses_pinned_workflow_scoped_token_and_data_only_context():
    requests = []
    response = response_payload()
    response["data"]["outputs"] = {"result": '{"slots": []}'}

    def handle(req):
        requests.append(req)
        return httpx.Response(200, json=response)

    generator, credentials = setup(handle)
    output = asyncio.run(generator.generate(principal(), request()))
    assert output == '{"slots": []}'
    credentials.resolve.assert_called_once_with("workspace-1", "app-1", "generation-token")
    assert len(requests) == 1
    sent = requests[0]
    assert sent.url.path == f"/v1/workflows/{WORKFLOW_ID}/run"
    assert sent.headers["authorization"] == "Bearer app-test-key"
    body = json.loads(sent.content)
    assert body["user"] == "actor-1"
    assert set(body["inputs"]) == {"context", "output_schema", "instructions"}
    assert json.loads(body["inputs"]["context"]) == request().model_dump(mode="json")
    assert "app-test-key" not in sent.content.decode()
    assert "generation-token" not in sent.content.decode()
    assert "visual_json" not in sent.content.decode()


@pytest.mark.parametrize("change", [{"workspace_id": "other"}, {"workspace_role": "normal"}])
def test_generator_rejects_wrong_actor_before_credential_or_network_access(change):
    generator, credentials = setup(lambda _: pytest.fail("No HTTP expected"))
    with pytest.raises(AccessDenied):
        asyncio.run(generator.generate(principal().model_copy(update=change), request()))
    credentials.resolve.assert_not_called()


@pytest.mark.parametrize("failure", ["http", "failed", "wrong_workflow", "non_text", "oversized"])
def test_native_generation_failure_is_opaque_and_never_retried(failure):
    requests = []
    response = response_payload()
    response["data"]["outputs"] = {"result": '{"slots": []}'}
    if failure == "failed":
        response["data"]["status"] = "failed"
    elif failure == "wrong_workflow":
        response["data"]["workflow_id"] = "b319d1d5-173c-48d5-bfda-e84531f1dcad"
    elif failure == "non_text":
        response["data"]["outputs"] = {"result": {"slots": []}}
    elif failure == "oversized":
        response["data"]["outputs"] = {"result": "x" * 262145}

    def handle(req):
        requests.append(req)
        return httpx.Response(503 if failure == "http" else 200, json=response)

    generator, _ = setup(handle)
    with pytest.raises(DependencyUnavailable, match="dashboard_sql_generation_unavailable"):
        asyncio.run(generator.generate(principal(), request()))
    assert len(requests) == 1


def test_application_and_native_adapter_produce_only_a_review_draft_together():
    base, repository, schemas, model, command = setup_generation()
    response = response_payload()
    response["data"]["outputs"] = {"result": model.generate.return_value}
    native, _ = setup(lambda _: httpx.Response(200, json=response))
    service = DashboardSqlGeneration(repository, schemas, native, base._drafts)

    draft = asyncio.run(service.generate(principal(), "dashboard-1", command))

    assert draft.status == "draft"
    assert draft.dashboard_revision == 1
    assert draft.schema_revision == "schema-1"
    assert draft.proposals.slots[0].sql == "SELECT count FROM measurements"
    repository.save_bindings.assert_not_called()
    repository.commit.assert_not_called()


def test_credential_revocation_prevents_network_access_and_hides_vault_details():
    generator, credentials = setup(lambda _: pytest.fail("No HTTP expected"))
    credentials.resolve.side_effect = AccessDenied("private vault path")
    with pytest.raises(DependencyUnavailable) as error:
        asyncio.run(generator.generate(principal(), request()))
    assert str(error.value) == "dashboard_sql_generation_unavailable"
