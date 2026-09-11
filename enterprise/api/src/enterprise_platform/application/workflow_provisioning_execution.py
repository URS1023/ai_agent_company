"""Execute one already-claimed phase; persistence and retries belong to the caller.

The journal must commit ownership before this boundary is invoked. Cancellation
propagates so ownership survives. Unknown write outcomes never become retryable
rejections, and no native credentials or sessions enter the returned receipts.
"""

from typing import Protocol

from pydantic import TypeAdapter

from .contracts import Principal
from .workflow_draft_execution import DraftCredentialRejected, WorkflowDraftCredentialBinder
from .workflow_draft_read import DraftReadRejected, WorkflowDraftReader
from .workflow_plugin_credentials import PluginCredentialRejected, WorkflowPluginCredentialClient
from .workflow_provisioning_contracts import (
    BindCredentialCommand,
    BindCredentialReceipt,
    PhaseCommand,
    PhaseOutcome,
    PrepareCredentialCommand,
    PrepareCredentialReceipt,
    PublishReceipt,
    ReadDraftCommand,
    ReadDraftReceipt,
)
from .workflow_publish_execution import PublishRejected, WorkflowPublisher
from .workflow_setup_execution import NativeSetupSession


class ProvisioningPluginClients(Protocol):
    async def resolve(
        self,
        principal: Principal,
        *,
        app_id: str,
        configuration_ref: str,
        configuration_revision: int,
    ) -> WorkflowPluginCredentialClient:
        """Resolve exact server-owned configuration without native mutation."""
        ...


class WorkflowProvisioningExecutor:
    _reader: WorkflowDraftReader
    _plugin_clients: ProvisioningPluginClients
    _binder: WorkflowDraftCredentialBinder
    _publisher: WorkflowPublisher

    def __init__(
        self,
        *,
        reader: WorkflowDraftReader,
        plugin_clients: ProvisioningPluginClients,
        binder: WorkflowDraftCredentialBinder,
        publisher: WorkflowPublisher,
    ) -> None:
        self._reader, self._plugin_clients, self._binder, self._publisher = reader, plugin_clients, binder, publisher

    async def execute(self, principal: Principal, session: NativeSetupSession, command: PhaseCommand) -> PhaseOutcome:
        command = TypeAdapter(PhaseCommand).validate_python(command.model_dump())
        try:
            if isinstance(command, ReadDraftCommand):
                metadata = await self._reader.read(principal, session, app_id=command.app_id)
                if (metadata.workspace_id, metadata.app_id) != (principal.workspace_id, command.app_id):
                    raise ValueError("Native draft scope mismatch")
                return PhaseOutcome(
                    state="succeeded", receipt=ReadDraftReceipt(kind=command.kind, **metadata.model_dump())
                )
            if isinstance(command, PrepareCredentialCommand):
                try:
                    client = await self._plugin_clients.resolve(
                        principal,
                        app_id=command.app_id,
                        configuration_ref=command.config_ref,
                        configuration_revision=command.config_revision,
                    )
                except Exception:
                    return PhaseOutcome(state="rejected", reason_code="plugin_configuration_unavailable")
                credential = await client.prepare(
                    principal, session, app_id=command.app_id, operation_id=command.operation_id
                )
                if credential.state != "credential_created" or credential.reason_code is not None:
                    raise ValueError("Native credential result unconfirmed")
                return PhaseOutcome(
                    state="succeeded",
                    receipt=PrepareCredentialReceipt(
                        kind=command.kind,
                        app_id=command.app_id,
                        credential_id=credential.credential_id,
                        plugin_unique_identifier=credential.plugin_unique_identifier,
                    ),
                )
            if isinstance(command, BindCredentialCommand):
                bound = await self._binder.bind_credential(
                    principal,
                    session,
                    app_id=command.app_id,
                    draft_id=command.draft_id,
                    expected_draft_hash=command.expected_draft_hash,
                    credential_id=command.credential_id,
                )
                if (
                    bound.state != "draft_bound"
                    or bound.reason_code is not None
                    or (bound.app_id, bound.draft_id, bound.credential_id, bound.accepted_draft_hash)
                    != (command.app_id, command.draft_id, command.credential_id, command.expected_draft_hash)
                ):
                    raise ValueError("Native binding result unconfirmed")
                return PhaseOutcome(
                    state="succeeded",
                    receipt=BindCredentialReceipt(
                        kind=command.kind,
                        app_id=command.app_id,
                        draft_id=command.draft_id,
                        credential_id=command.credential_id,
                        accepted_draft_hash=command.expected_draft_hash,
                        draft_hash=bound.draft_hash,
                    ),
                )
            published = await self._publisher.publish(
                principal,
                session,
                app_id=command.app_id,
                expected_draft_hash=command.expected_draft_hash,
                operation_id=command.operation_id,
            )
            if (
                published.state != "published"
                or published.reason_code is not None
                or (published.app_id, published.accepted_draft_hash) != (command.app_id, command.expected_draft_hash)
            ):
                raise ValueError("Native publication result unconfirmed")
            return PhaseOutcome(
                state="succeeded",
                receipt=PublishReceipt(
                    kind=command.kind,
                    app_id=command.app_id,
                    workflow_id=published.workflow_id,
                    accepted_draft_hash=command.expected_draft_hash,
                ),
            )
        except (DraftReadRejected, PluginCredentialRejected, DraftCredentialRejected, PublishRejected):
            return PhaseOutcome(state="rejected", reason_code="native_provisioning_preflight_rejected")
        except Exception:
            return PhaseOutcome(
                state="rejected" if isinstance(command, ReadDraftCommand) else "uncertain",
                reason_code="native_provisioning_result_unconfirmed",
            )
