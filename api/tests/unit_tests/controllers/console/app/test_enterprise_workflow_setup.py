"""Exercise managed import guards against the actual native controller without external I/O."""

from inspect import unwrap
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from flask import Flask
from werkzeug.exceptions import BadRequest, Conflict, Forbidden

from configs.feature import WorkflowConfig
from controllers.console.app import app_import as controller
from models.account import Account, Tenant
from services.app_dsl_service import Import
from services.entities.dsl_entities import ImportStatus


@pytest.fixture
def app() -> Flask:
    return Flask(__name__)


@pytest.fixture
def native_import(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock]:
    session = MagicMock()
    session.__enter__.return_value = session
    factory = MagicMock(return_value=session)
    service = MagicMock()
    monkeypatch.setattr(controller, "db", SimpleNamespace(engine=object()))
    monkeypatch.setattr(controller, "Session", factory)
    monkeypatch.setattr(controller, "AppDslService", MagicMock(return_value=service))
    monkeypatch.setattr(
        controller, "dify_config", SimpleNamespace(ENTERPRISE_WORKFLOW_SETUP_ENABLED=True, RBAC_ENABLED=False)
    )
    monkeypatch.setattr(
        controller.FeatureService,
        "get_system_features",
        lambda: SimpleNamespace(webapp_auth=SimpleNamespace(enabled=False)),
    )
    return factory, service


def test_setup_is_opt_in() -> None:
    assert WorkflowConfig.model_fields["ENTERPRISE_WORKFLOW_SETUP_ENABLED"].default is False


@pytest.mark.parametrize("enabled", [False, True])
def test_capabilities_use_native_account(enabled: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(controller, "dify_config", SimpleNamespace(ENTERPRISE_WORKFLOW_SETUP_ENABLED=enabled))
    api = controller.EnterpriseWorkflowSetupCapabilitiesApi()
    response = unwrap(api.get)(api, SimpleNamespace(current_tenant_id="workspace-a"))
    assert response == {
        "enabled": enabled,
        "publish_enabled": enabled,
        "credential_setup_enabled": enabled,
        "draft_credential_bind_enabled": enabled,
        "draft_read_enabled": enabled,
        "service_api_token_issue_enabled": enabled,
        "publication_read_enabled": enabled,
        "workspace_id": "workspace-a",
    }


@pytest.mark.parametrize(
    ("enabled", "expected", "workspace", "payload", "error"),
    [
        (False, "workspace-a", "workspace-a", {"mode": "yaml-content"}, Forbidden),
        (True, "workspace-a", "workspace-b", {"mode": "yaml-content"}, Conflict),
        (True, "", "workspace-a", {"mode": "yaml-content"}, Conflict),
        (True, "workspace-a", None, {"mode": "yaml-content"}, Conflict),
        (True, "workspace-a", "workspace-a", {"mode": "yaml-url", "yaml_url": "https://example.com"}, BadRequest),
        (True, "workspace-a", "workspace-a", {"mode": "yaml-content", "app_id": "existing"}, BadRequest),
        (True, "workspace-a", "workspace-a", {"mode": "yaml-content", "app_id": ""}, BadRequest),
    ],
)
def test_managed_guard_rejects_before_session(
    enabled: bool,
    expected: str,
    workspace: str | None,
    payload: dict[str, str],
    error: type[Exception],
    app: Flask,
    native_import: tuple[MagicMock, MagicMock],
) -> None:
    factory, service = native_import
    controller.dify_config.ENTERPRISE_WORKFLOW_SETUP_ENABLED = enabled
    api = controller.AppImportApi()
    with (
        app.test_request_context(
            "/console/api/apps/imports",
            method="POST",
            json=payload,
            headers={"X-Enterprise-Expected-Workspace": expected},
        ),
        pytest.raises(error),
    ):
        unwrap(api.post)(api, SimpleNamespace(current_tenant_id=workspace))
    factory.assert_not_called()
    service.import_app.assert_not_called()


@pytest.mark.parametrize("managed", [False, True])
@pytest.mark.parametrize(
    ("status", "http_status"),
    [
        (ImportStatus.FAILED, 400),
        (ImportStatus.PENDING, 202),
        (ImportStatus.COMPLETED, 200),
        (ImportStatus.COMPLETED_WITH_WARNINGS, 200),
    ],
)
def test_import_passes_same_account_and_acknowledges_only_managed_results(
    managed: bool,
    status: ImportStatus,
    http_status: int,
    app: Flask,
    native_import: tuple[MagicMock, MagicMock],
) -> None:
    factory, service = native_import
    service.import_app.return_value = Import(id="import-1", status=status)
    account = Account(name="Native account", email="account@example.test")
    tenant = Tenant(name="Native workspace")
    tenant.id = "workspace-a"
    # The native login loader populates this cached tenant before controller entry.
    account._current_tenant = tenant
    controller.dify_config.ENTERPRISE_WORKFLOW_SETUP_ENABLED = managed
    api = controller.AppImportApi()
    headers = {"X-Enterprise-Expected-Workspace": "workspace-a"} if managed else {}
    with app.test_request_context(
        "/console/api/apps/imports",
        method="POST",
        json={"mode": "yaml-content", "yaml_content": "kind: app"},
        headers=headers,
    ):
        response = unwrap(api.post)(api, account)
    assert response[1] == http_status
    assert response[0]["status"] == status.value
    assert service.import_app.call_args.kwargs["account"] is account
    if managed:
        assert response[2] == {"X-Enterprise-Workspace": "workspace-a"}
    else:
        assert len(response) == 2
    session = factory.return_value
    if status == ImportStatus.FAILED:
        session.rollback.assert_called_once_with()
        session.commit.assert_not_called()
    else:
        session.commit.assert_called_once_with()
        session.rollback.assert_not_called()
