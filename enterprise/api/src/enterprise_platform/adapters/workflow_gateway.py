"""Server-owned credential selection around the native pinned workflow client.

References are scoped to both workspace and application. They are resolved before
any network request, never forwarded in workflow inputs, and never fall back to a
different application's token. Each call owns and closes its HTTP client.
"""

from collections.abc import Callable
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr

from enterprise_platform.application.contracts import JsonObject, Run
from enterprise_platform.application.dispatcher import WorkflowPreparationFailed
from enterprise_platform.application.errors import EnterpriseError
from enterprise_platform.application.workflow_credential_contracts import WorkflowCredential as WorkflowCredential
from enterprise_platform.application.workflow_credential_ports import WorkflowCredentialResolver

from .dify_workflows import DifyWorkflowClient, WorkflowBinding, WorkflowResult, WorkflowStarted, validate_native_run_id


class NativeWorkflowGateway:
    _base_url: str
    _credentials: dict[tuple[str, str, str], SecretStr]
    _transport: httpx.BaseTransport | None
    _credential_resolver: WorkflowCredentialResolver | None

    def __init__(
        self,
        *,
        base_url: str,
        credentials: tuple[WorkflowCredential, ...] = (),
        credential_resolver: WorkflowCredentialResolver | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or not parsed.path.rstrip("/").endswith("/v1")
            or parsed.port == 0
        ):
            raise ValueError("Native service URL must be a fixed HTTP endpoint ending in /v1")
        if credentials and credential_resolver is not None:
            raise ValueError("Choose static credentials or the vault, not an implicit fallback")
        resolved: dict[tuple[str, str, str], SecretStr] = {}
        for credential in credentials:
            key = (credential.workspace_id, credential.app_id, credential.secret_ref)
            if key in resolved:
                raise ValueError("Duplicate workflow credential reference")
            resolved[key] = credential.api_key
        self._base_url, self._credentials, self._transport = base_url, resolved, transport
        self._credential_resolver = credential_resolver

    def _client(self, run: Run) -> DifyWorkflowClient:
        key = (run.workspace_id, run.spec.app_id, run.spec.secret_ref)
        credential: SecretStr | None
        if self._credential_resolver is not None:
            try:
                resolved = self._credential_resolver.resolve(*key)
                credential = WorkflowCredential(
                    workspace_id=key[0], app_id=key[1], secret_ref=key[2], api_key=resolved
                ).api_key
            except (EnterpriseError, ValueError):
                raise WorkflowPreparationFailed("Scoped workflow credential is unavailable") from None
        else:
            credential = self._credentials.get(key)
        if credential is None:
            raise WorkflowPreparationFailed("Scoped workflow credential is unavailable")
        return DifyWorkflowClient(
            base_url=self._base_url,
            workspace_id=run.workspace_id,
            app_id=run.spec.app_id,
            api_key=credential,
            transport=self._transport,
        )

    @staticmethod
    def _binding(run: Run) -> WorkflowBinding:
        return WorkflowBinding(
            workspace_id=run.workspace_id,
            device_id=run.spec.device_id,
            scenario=run.spec.scenario,
            app_id=run.spec.app_id,
            workflow_id=run.spec.workflow_id,
            specification_revision=run.spec.specification_revision,
        )

    def run(self, run: Run, inputs: JsonObject, *, on_started: Callable[[WorkflowStarted], None]) -> WorkflowResult:
        with self._client(run) as client:
            return client.run_stream(self._binding(run), actor_id=run.actor_id, inputs=inputs, on_started=on_started)

    def get_run(self, run: Run) -> WorkflowResult:
        if not run.dify_run_id:
            raise WorkflowPreparationFailed("Native execution identity is required")
        try:
            validate_native_run_id(run.dify_run_id)
        except ValueError:
            raise WorkflowPreparationFailed("Native execution identity is invalid") from None
        with self._client(run) as client:
            return client.get_run(self._binding(run), run.dify_run_id)
